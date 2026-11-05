import os
import re
import time

from openai import AsyncOpenAI

DEFAULT_MODEL = "Meta-Llama-3.3-70B-Instruct"


def get_client():
    client = AsyncOpenAI(
        api_key=os.environ["SAMBANOVA_API_KEY"],
        base_url=os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1"),
    )
    if os.getenv("BENCH_LOG"):
        # Benchmark runs only: write down every request as sent. Loaded by
        # path so a normal run, and the image, never import it.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bench_recorder", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "bench", "recorder.py"))
        recorder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recorder)
        recorder.attach(client, os.environ["BENCH_LOG"])
    return client


def get_model():
    return os.getenv("SAMBANOVA_MODEL", DEFAULT_MODEL)


# Server-side timings SambaNova reports in its closing usage frame. These
# measure inference alone, with no network in them — comparing them against the
# client's own stopwatch is how you separate the model from the round trip.
SERVER_FIELDS = (
    "time_to_first_token",
    "total_latency",
    "completion_tokens_per_sec",
    "completion_tokens_after_first_per_sec",
    "total_tokens_per_sec",
    "completion_tokens",
    "prompt_tokens",
    "stop_reason",
)


def read_usage(chunk):
    """Pull SambaNova's inference timings out of a usage frame, if present."""
    usage = getattr(chunk, "usage", None)
    if not usage:
        return None
    data = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    picked = {k: data[k] for k in SERVER_FIELDS if data.get(k) is not None}
    return picked or None


class StreamClock:
    """Our own generation rate, timed on our side of the stream.

    SambaNova reports its own tokens per second in the usage frame, and
    other providers report nothing of the kind, so a comparison needs a
    figure every provider's stream gives the same way: output tokens
    divided by the time from the first streamed token to the last, on this
    machine's clock. Tool-call pieces count as output; they are tokens the
    model produced.

    The token count is the provider's own completion_tokens from its usage
    frame. A stream without one falls back to characters / 4, and says so.
    """

    def __init__(self):
        self.first = self.last = None
        self.chars = 0

    def tick(self, text):
        now = time.perf_counter()
        if self.first is None:
            self.first = now
        self.last = now
        self.chars += len(text or "")

    def into(self, stats):
        if stats is None or self.first is None:
            return
        tokens = stats.get("completion_tokens")
        source = "usage"
        if not tokens:
            tokens, source = round(self.chars / 4), "estimate"
        span = self.last - self.first
        stats.update(stream_output_tokens=tokens, stream_tokens_source=source,
                     stream_seconds=round(span, 4),
                     stream_tokens_per_sec=round(tokens / span, 1) if span > 0 else None)


DEFAULT_VISION_MODEL = "gemma-4-31B-it"


def get_vision_model():
    return os.getenv("SAMBANOVA_VISION_MODEL", DEFAULT_VISION_MODEL)


async def describe_images(client, data_urls, question, model=None):
    """Ask the vision model what it sees, and return plain text.

    Only gemma handles several images reliably — MiniMax-M3 reads one image
    well but mis-reports a batch, so frame sequences must go here.
    """
    content = [{"type": "text", "text": question}]
    content += [{"type": "image_url", "image_url": {"url": u}} for u in data_urls]

    reply = await client.chat.completions.create(
        model=model or get_vision_model(),
        max_tokens=180,   # descriptions run ~30 tokens; 400 was never reached
        messages=[{"role": "user", "content": content}],
    )
    text = (reply.choices[0].message.content or "").strip()
    # M3 wraps its reasoning in a tag that must never reach the speaker.
    text = re.sub(r"<mm:think>.*?</mm:think>", "", text, flags=re.S).strip()
    # Belt and braces on the prompt: any markdown that slips through would be
    # paraphrased downstream as a report rather than heard as an observation.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"\n{2,}", " ", text).strip()
    usage = getattr(reply, "usage", None)
    took = getattr(usage, "time_to_first_token", None) if usage else None
    return text, {"model": model or get_vision_model(),
                  "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                  "inference_s": took}


class ThinkFilter:
    """Strip <mm:think> blocks out of a token stream.

    MiniMax models narrate their reasoning inside these tags. In the vision
    call a regex over the finished text is enough, but a streamed reply goes
    straight to the speaker — once "the user wants me to describe" has been
    spoken it cannot be taken back. Tags also arrive split across deltas
    ("<mm", ":thi", "nk>"), so this holds back any trailing fragment that could
    still turn into one.
    """

    OPEN = "<mm:think>"
    CLOSE = "</mm:think>"

    def __init__(self):
        self.buf = ""
        self.inside = False

    @staticmethod
    def _partial(buf, tag):
        """Longest suffix of buf that could be the start of tag."""
        for n in range(min(len(tag) - 1, len(buf)), 0, -1):
            if buf.endswith(tag[:n]):
                return buf[-n:]
        return ""

    def feed(self, delta):
        self.buf += delta
        out = []
        while self.buf:
            if self.inside:
                at = self.buf.find(self.CLOSE)
                if at == -1:
                    self.buf = self._partial(self.buf, self.CLOSE)
                    break
                self.buf = self.buf[at + len(self.CLOSE):]
                self.inside = False
            else:
                at = self.buf.find(self.OPEN)
                if at == -1:
                    hold = self._partial(self.buf, self.OPEN)
                    ready = self.buf[:len(self.buf) - len(hold)]
                    if ready:
                        out.append(ready)
                    self.buf = hold
                    break
                if at:
                    out.append(self.buf[:at])
                self.buf = self.buf[at + len(self.OPEN):]
                self.inside = True
        return "".join(out)

    def flush(self):
        """Whatever is left that was never part of a tag."""
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


async def stream_tools(client, messages, model=None, tools=None, stats=None,
                       temperature=None):
    """Like stream_chat, but the model may answer with a tool call instead.

    Yields ('text', delta) as prose arrives, ('tool_start', None) at the first
    sign the model is calling a tool, and ('tool', {name, arguments}) once per
    completed call at the end.
    """
    kwargs = {"tools": tools} if tools else {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    stream = await client.chat.completions.create(
        model=model or get_model(),
        messages=messages,
        stream=True,
        **kwargs,
    )

    calls = {}
    announced = False
    think = ThinkFilter()
    clock = StreamClock()

    async for chunk in stream:
        # Usage can ride on the closing frame with no choices (SambaNova) or
        # on the last frame that has some (others), so look on every one.
        if stats is not None:
            found = read_usage(chunk)
            if found:
                stats.update(found)
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        parts = getattr(delta, "tool_calls", None)
        if parts:
            if not announced:
                announced = True
                yield ("tool_start", None)
            for part in parts:
                clock.tick((part.function and part.function.arguments) or "")
                slot = calls.setdefault(part.index or 0, {"name": "", "arguments": ""})
                if part.function:
                    if part.function.name:
                        slot["name"] += part.function.name
                    if part.function.arguments:
                        slot["arguments"] += part.function.arguments
        elif delta.content:
            clock.tick(delta.content)
            clean = think.feed(delta.content)
            if clean:
                yield ("text", clean)

    clock.into(stats)
    tail = think.flush()
    if tail:
        yield ("text", tail)

    for slot in calls.values():
        yield ("tool", slot)


async def stream_chat(client, messages, model=None, stats=None, temperature=None):
    """Stream deltas for a full message list. Reuses a caller-owned client so the
    TLS connection stays warm between turns.

    `stats`, if given, is filled in with SambaNova's own server-side timings once
    the closing usage frame arrives.
    """
    stream = await client.chat.completions.create(
        model=model or get_model(),
        messages=messages,
        stream=True,
        **({"temperature": temperature} if temperature is not None else {}),
    )

    think = ThinkFilter()
    clock = StreamClock()
    async for chunk in stream:
        # SambaNova closes the stream with a usage frame that carries no choices
        # — the one that used to crash this demo. It holds the inference timings.
        if stats is not None:
            found = read_usage(chunk)
            if found:
                stats.update(found)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            clock.tick(delta)
            clean = think.feed(delta)
            if clean:
                yield clean
    clock.into(stats)
    tail = think.flush()
    if tail:
        yield tail


async def stream_reply(prompt, system_prompt="Answer clearly and concisely."):
    client = get_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    async for delta in stream_chat(client, messages):
        yield delta
