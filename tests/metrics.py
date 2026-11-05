"""What the turn's numbers mean, checked.

Tokens per second is timed by us from the stream: output tokens divided by
the time from the first streamed token to the last, so it is the same
figure from any provider. SambaNova's own figure is kept beside it and
never mixed in. And first token means the model's first token: the fixed
disclosure line at the start of a call does not count.

Offline: a stand-in provider stream with gaps we control, and a server in
TEXT_ONLY mode whose model calls fail.
"""

import asyncio
import os
import sys
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import sambanova                                              # noqa: E402

GAP = 0.15


def text_chunk(text):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=None))], usage=None)


def tool_chunk(args):
    part = NS(index=0, function=NS(name="lookup_order" if args.startswith("{") else "",
                                   arguments=args))
    return NS(choices=[NS(delta=NS(content=None, tool_calls=[part]))], usage=None)


def usage(**fields):
    return NS(model_dump=lambda: fields)


class FakeClient:
    """chat.completions.create, streaming the chunks it is given with a
    fixed gap between them."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kwargs):
        chunks = self.chunks

        async def stream():
            for n, chunk in enumerate(chunks):
                if n:
                    await asyncio.sleep(GAP)
                yield chunk
        return stream()


async def run(chunks, kind="tools"):
    stats = {}
    client = FakeClient(chunks)
    if kind == "tools":
        async for _ in sambanova.stream_tools(client, [], model="m", stats=stats):
            pass
    else:
        async for _ in sambanova.stream_chat(client, [], model="m", stats=stats):
            pass
    return stats


def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    # Three tokens' worth of text two gaps apart, then SambaNova's closing
    # usage frame with no choices, carrying its own (different) rate.
    closing = NS(choices=[], usage=usage(completion_tokens=30,
                                         completion_tokens_after_first_per_sec=999.0))
    stats = asyncio.run(run([text_chunk("Hello"), text_chunk(" there"),
                             text_chunk(" friend"), closing]))
    want = 30 / (2 * GAP)
    check("tokens/sec is output tokens over first-to-last token, timed by us",
          stats.get("stream_tokens_per_sec")
          and abs(stats["stream_tokens_per_sec"] - want) / want < 0.25)
    check("the token count is the provider's own, from its usage frame",
          stats.get("stream_output_tokens") == 30
          and stats.get("stream_tokens_source") == "usage")
    check("SambaNova's own figure is kept beside it, untouched",
          stats.get("completion_tokens_after_first_per_sec") == 999.0
          and stats["stream_tokens_per_sec"] != 999.0)
    check("the closing frame does not stretch the span",
          stats.get("stream_seconds") is not None
          and stats["stream_seconds"] < 2 * GAP * 1.25)

    # Another provider's habit: usage on the last frame that has choices.
    last = text_chunk(" friend")
    last.usage = usage(completion_tokens=12)
    stats = asyncio.run(run([text_chunk("Hello"), text_chunk(" there"), last], "chat"))
    check("usage on a frame with choices is read too (other providers)",
          stats.get("stream_output_tokens") == 12
          and stats.get("stream_tokens_source") == "usage")

    # No usage at all: an estimate, and it says so.
    stats = asyncio.run(run([text_chunk("a" * 40), text_chunk("b" * 40)], "chat"))
    check("with no usage frame the count is an estimate, labelled as one",
          stats.get("stream_output_tokens") == 20
          and stats.get("stream_tokens_source") == "estimate")

    # A tool call is output the model produced, and is timed like text.
    stats = asyncio.run(run([tool_chunk('{"order'), tool_chunk('": "44812"}'),
                             NS(choices=[], usage=usage(completion_tokens=9))]))
    check("a tool call's pieces are timed as output",
          stats.get("stream_output_tokens") == 9 and stats.get("stream_tokens_per_sec"))

    asyncio.run(first_token_is_the_models(check))

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


async def first_token_is_the_models(check):
    """The first turn says the disclosure line before the model says
    anything. With the model failing offline, that line is all there is,
    and it must not be reported as a first token."""
    import aiohttp
    from tests.harness import PASSPHRASE, Server

    with Server() as s:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            await http.post(f"{s.base}/login", data={"passphrase": PASSPHRASE, "consent": "yes"})
            async with http.ws_connect(f"{s.base}/ws") as ws:
                await ws.receive_json()
                await ws.send_json({"type": "prompt", "text": "Hello, where is order 44812?",
                                    "call": False, "search": False})
                seen, text = [], ""
                while True:
                    m = await asyncio.wait_for(ws.receive_json(), 40)
                    seen.append(m["type"])
                    if m["type"] == "token":
                        text += m.get("text", "")
                    if m["type"] in ("done", "error") and m.get("turn_id"):
                        break
    check("the disclosure line is still said on the first turn",
          "AI" in text)
    check("but it is not reported as the first token", "first_token" not in seen)


if __name__ == "__main__":
    raise SystemExit(main())
