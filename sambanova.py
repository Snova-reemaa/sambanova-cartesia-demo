import os
import re

from openai import AsyncOpenAI

DEFAULT_MODEL = "Meta-Llama-3.3-70B-Instruct"


def get_client():
    return AsyncOpenAI(
        api_key=os.environ["SAMBANOVA_API_KEY"],
        base_url=os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1"),
    )


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
        max_tokens=400,
        messages=[{"role": "user", "content": content}],
    )
    text = (reply.choices[0].message.content or "").strip()
    # M3 wraps its reasoning in a tag that must never reach the speaker.
    text = re.sub(r"<mm:think>.*?</mm:think>", "", text, flags=re.S).strip()
    usage = getattr(reply, "usage", None)
    took = getattr(usage, "time_to_first_token", None) if usage else None
    return text, {"model": model or get_vision_model(),
                  "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                  "inference_s": took}


async def stream_tools(client, messages, model=None, tools=None, stats=None):
    """Like stream_chat, but the model may answer with a tool call instead.

    Yields ('text', delta) as prose arrives, ('tool_start', None) at the first
    sign the model is calling a tool, and ('tool', {name, arguments}) once per
    completed call at the end.
    """
    kwargs = {"tools": tools} if tools else {}
    stream = await client.chat.completions.create(
        model=model or get_model(),
        messages=messages,
        stream=True,
        **kwargs,
    )

    calls = {}
    announced = False

    async for chunk in stream:
        if not chunk.choices:
            if stats is not None:
                found = read_usage(chunk)
                if found:
                    stats.update(found)
            continue

        delta = chunk.choices[0].delta
        parts = getattr(delta, "tool_calls", None)
        if parts:
            if not announced:
                announced = True
                yield ("tool_start", None)
            for part in parts:
                slot = calls.setdefault(part.index or 0, {"name": "", "arguments": ""})
                if part.function:
                    if part.function.name:
                        slot["name"] += part.function.name
                    if part.function.arguments:
                        slot["arguments"] += part.function.arguments
        elif delta.content:
            yield ("text", delta.content)

    for slot in calls.values():
        yield ("tool", slot)


async def stream_chat(client, messages, model=None, stats=None):
    """Stream deltas for a full message list. Reuses a caller-owned client so the
    TLS connection stays warm between turns.

    `stats`, if given, is filled in with SambaNova's own server-side timings once
    the closing usage frame arrives.
    """
    stream = await client.chat.completions.create(
        model=model or get_model(),
        messages=messages,
        stream=True,
    )

    async for chunk in stream:
        # SambaNova closes the stream with a usage frame that carries no choices
        # — the one that used to crash this demo. It holds the inference timings.
        if not chunk.choices:
            if stats is not None:
                found = read_usage(chunk)
                if found:
                    stats.update(found)
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


async def stream_reply(prompt, system_prompt="Answer clearly and concisely."):
    client = get_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    async for delta in stream_chat(client, messages):
        yield delta
