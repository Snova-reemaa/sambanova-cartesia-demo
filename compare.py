"""One question to two providers at the same moment, timed the same way.

Fair by construction:

- Same messages, same temperature, same max_tokens, no tools, for both.
- Same start: each side opens its client, warms its connection (a free
  models list, so neither pays for TLS or the SDK's first-use set-up on
  the clock) and lines up; when both are waiting, one t0 is taken and
  both are released together. How long
  after t0 each request actually left is recorded, so a head start would
  show rather than hide.
- Same measurement: both stream through sambanova.stream_chat with its
  StreamClock, the code the app itself uses. Time to first token is from
  t0 to the first token, reasoning included where a model streams it
  separately. Tokens per second is output tokens over first-to-last token
  on our clock, with each provider's own token count. Full reply time is
  t0 to the end of the stream.
- No retries: the client is made with max_retries=0, because the SDK
  otherwise retries a 429 quietly and the wait shows up as a slow reply.

Honest about failure: a rate limit, a refused key, a missing model, a
timeout or a dropped connection is reported as that, with no number.

Repeats: run it five times and the summary gives each side's median and
worst, and how many of the five succeeded, not the luckiest run.
"""

import asyncio
import json
import os
import re
import statistics
import time

import openai

import providers
from sambanova import StreamClock, stream_chat

SETTINGS = {
    "temperature": float(os.getenv("COMPARE_TEMPERATURE", "0.3")),
    "max_tokens": int(os.getenv("COMPARE_MAX_TOKENS", "400")),
}
TIMEOUT_SECONDS = float(os.getenv("COMPARE_TIMEOUT_SECONDS", "60"))
MAX_RUNS = 5
METRICS = ("ttft", "tps", "total")
# Lower is better for the times; higher is better for tokens per second.
BETTER_HIGH = {"tps"}


def failure(exc):
    """(kind, what to show) for anything that stopped a side."""
    cause = exc.__cause__ or exc.__context__
    if isinstance(exc, providers.Refused) or isinstance(cause, providers.Refused):
        return "blocked", str(exc if isinstance(exc, providers.Refused) else cause)
    if isinstance(exc, openai.APIStatusError) and 300 <= exc.status_code < 400:
        return "redirect", f"The provider redirected ({exc.status_code}); redirects are not followed"
    if isinstance(exc, openai.BadRequestError) and "model" in str(exc).lower():
        return "not_found", "Unknown model (400): check the model name"
    if isinstance(exc, openai.APIConnectionError) and "certificate" in str(cause or "").lower():
        return "connection", "Could not connect securely (certificate problem)"
    if isinstance(exc, openai.RateLimitError):
        return "rate_limited", "Rate limited (429)"
    if isinstance(exc, openai.AuthenticationError):
        return "auth", "Key refused (401)"
    if isinstance(exc, openai.PermissionDeniedError):
        return "auth", "Not allowed (403)"
    if isinstance(exc, openai.NotFoundError):
        return "not_found", "Model not found (404)"
    if isinstance(exc, (openai.APITimeoutError, asyncio.TimeoutError)):
        return "timeout", f"Timed out after {TIMEOUT_SECONDS:g} s"
    if isinstance(exc, openai.APIStatusError):
        return "error", f"Error {exc.status_code}"
    if isinstance(exc, openai.APIConnectionError):
        return "connection", "Could not connect"
    return "error", type(exc).__name__


def detail(exc, secret=None):
    """The provider's own words, short, with the key and anything key-shaped
    removed: providers like to echo it ("Incorrect API key provided: sk-…")."""
    text = str(getattr(exc, "message", None) or exc)
    if secret:
        text = text.replace(secret, "…")
        if len(secret) >= 8:
            text = text.replace(secret[:8], "…")
    return re.sub(r"[A-Za-z0-9_\-]{20,}", "…", text)[:160]


def scrub(text, secret):
    return text.replace(secret, "…") if secret and text else text


def looks_like_a_tool_call(text):
    """A reply that is nothing but a JSON object or array."""
    text = text.strip()
    if not text or text[0] not in "{[":
        return False
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


async def warm(client):
    """Get everything that is not the model out of the way before the start
    line, so the clock times the request and nothing else. The SDK works
    out its platform headers on first use, about a quarter of a second,
    measured; and a cold connection pays for TCP and TLS on its first
    request, different for every provider. Listing models is free, opens
    the connection, and leaves it in the pool for the request to reuse. If
    it fails, the real request will say why."""
    _ = client.default_headers
    try:
        await asyncio.wait_for(client.models.list(), 10)
    except Exception:                                      # the request will report it
        pass


async def race(names, messages, emit, settings=None):
    """One round: every side, released at the same moment. A side is a
    provider's name or a providers.Side (the visitor's own provider)."""
    settings = settings or SETTINGS
    sides = [n if isinstance(n, providers.Side) else providers.side(n) for n in names]
    start = asyncio.Event()
    lined_up = []
    shared = {}

    async def run(entry):
        name, client = entry.name, entry.client
        extra = {"max_tokens": settings["max_tokens"]}
        if entry.ask_for_usage:
            extra["stream_options"] = {"include_usage": True}
        clock = StreamClock(on_first=lambda at: emit(
            {"type": "first_token", "side": name, "t": at - shared["t0"]}))
        stats = {}
        said = []
        await warm(client)
        lined_up.append(name)
        await start.wait()
        sent = time.perf_counter()
        out = {"side": name, "sent_after": sent - shared["t0"]}

        async def stream():
            async for text in stream_chat(client, messages, model=entry.model,
                                          stats=stats, temperature=settings["temperature"],
                                          extra=extra, clock=clock):
                said.append(text)
                emit({"type": "text", "side": name, "text": text})

        try:
            await asyncio.wait_for(stream(), TIMEOUT_SECONDS)
            end = time.perf_counter()
            if stats.get("stream_first_at") is None:
                out.update(ok=False, kind="empty", message="Empty reply")
            elif looks_like_a_tool_call("".join(said)):
                # Not an answer: a few tokens of JSON would win on time
                # against real words from the other side.
                out.update(ok=False, kind="tool_call",
                           message="Replied with a tool call, not words")
            else:
                out.update(
                    ok=True,
                    ttft=stats["stream_first_at"] - shared["t0"],
                    total=end - shared["t0"],
                    tps=stats.get("stream_tokens_per_sec"),
                    tokens=stats.get("stream_output_tokens"),
                    tokens_from=stats.get("stream_tokens_source"),
                    provider_tps=stats.get("completion_tokens_after_first_per_sec"),
                )
        except Exception as exc:                              # every kind is shown
            kind, message = failure(exc)
            out.update(ok=False, kind=kind, message=scrub(message, entry.secret),
                       detail=detail(exc, entry.secret))
        emit({"type": "side_done", **out})
        return out

    tasks = [asyncio.ensure_future(run(e)) for e in sides]
    while len(lined_up) < len(sides):
        await asyncio.sleep(0)
    shared["t0"] = time.perf_counter()
    emit({"type": "go"})
    start.set()
    return await asyncio.gather(*tasks)


def summarise(names, rounds):
    """Median and worst per side and metric, over the runs that worked."""
    names = [n.name if isinstance(n, providers.Side) else n for n in names]
    out = {}
    for name in names:
        results = [r for rnd in rounds for r in rnd if r["side"] == name]
        good = [r for r in results if r.get("ok")]
        side = {"runs": len(results), "ok": len(good),
                "failures": [r["message"] for r in results if not r.get("ok")]}
        for metric in METRICS:
            values = [r[metric] for r in good if r.get(metric) is not None]
            if not values:
                side[metric] = None
                continue
            worst = min(values) if metric in BETTER_HIGH else max(values)
            side[metric] = {"median": statistics.median(values), "worst": worst,
                            "best": max(values) if metric in BETTER_HIGH else min(values)}
        out[name] = side
    # Which side was faster, on the median full reply time, and by how much.
    timed = [(out[n]["total"]["median"], n) for n in names if out[n]["total"]]
    faster = None
    if len(timed) == len(names) == 2:
        (fast, fast_name), (slow, _) = sorted(timed)
        faster = {"side": fast_name, "by": slow / fast if fast > 0 else None}
    return {"sides": out, "faster": faster}


async def compare(names, messages, emit, runs=1, settings=None, pause=0.5):
    """`runs` rounds, one after another, then the summary."""
    runs = max(1, min(int(runs or 1), MAX_RUNS))
    rounds = []
    for n in range(1, runs + 1):
        emit({"type": "round", "n": n, "of": runs})
        rounds.append(await race(names, messages, emit, settings))
        if n < runs:
            await asyncio.sleep(pause)
    summary = summarise(names, rounds)
    emit({"type": "summary", **summary})
    return summary
