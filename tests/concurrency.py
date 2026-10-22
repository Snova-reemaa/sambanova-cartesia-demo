"""Too many callers at once is answered, not waited out.

MAX_CONCURRENT caps one connection at four turns. Twenty callers at four
each is eighty requests into SambaNova, which is how a rate limit is met and
how a pod starts handing out timeouts. A pod-wide cap queues them instead —
and the point of this suite is the part after the queue: when it does not
clear, the caller is told, rather than left watching a spinner.

Offline: the turns never reach a model, which does not matter. What is being
checked is the gate in front of the model, not the model.
"""

import asyncio
import json
import os
import sys
import time

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import Server, sign_in                      # noqa: E402


async def ask(http, base, text):
    """Returns (what happened, seconds)."""
    async with http.ws_connect(f"{base}/ws") as ws:
        await ws.receive()
        started = time.perf_counter()
        await ws.send_json({"type": "prompt", "text": text,
                            "call": False, "search": False})
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=45)
            except asyncio.TimeoutError:
                return "hung", time.perf_counter() - started
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue
            event = json.loads(msg.data)
            if event["type"] == "busy":
                return "busy", time.perf_counter() - started
            if event["type"] in ("done", "error"):
                return "answered", time.perf_counter() - started


async def main():
    results = []

    # One slot, a one second queue: everyone past the first must be told,
    # and told quickly.
    with Server(MAX_CONCURRENT_LLM="1", LLM_QUEUE_SECONDS="1") as server:
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as http:
            await sign_in(http, server)
            out = await asyncio.gather(*[
                ask(http, server.base, f"question number {i}") for i in range(6)])

        hung = [t for what, t in out if what == "hung"]
        busy = [t for what, t in out if what == "busy"]
        slowest_busy = max(busy) if busy else 0

        results.append(("nobody was left hanging", not hung))
        results.append((f"{len(busy)} of 6 were told we are busy", bool(busy)))
        results.append((f"told within the queue window ({slowest_busy:.1f}s)",
                        not busy or slowest_busy < 4))
        results.append(("the server logged no tracebacks",
                        server.tracebacks() == 0))

    # And with room for everyone, nobody is turned away.
    with Server(MAX_CONCURRENT_LLM="8", LLM_QUEUE_SECONDS="6") as server:
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as http:
            await sign_in(http, server)
            out = await asyncio.gather(*[
                ask(http, server.base, f"question number {i}") for i in range(4)])
        results.append(("with slots free, nobody is turned away",
                        not [1 for what, _ in out if what == "busy"]))

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
