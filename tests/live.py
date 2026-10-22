"""Shared machinery for the suites that need a real model.

These start a server of their own and talk to it the way the browser does.
Unless a suite says otherwise they run it in TEXT_ONLY mode: what is being
checked is what the agent says, not what it sounds like, and Cartesia bills
by the character.
"""

import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import Server, sign_in                      # noqa: E402


def wanted():
    """Live suites are opt-in; say so clearly when they are skipped."""
    # The server reads .env on start; a test deciding whether it *can* run
    # has to look in the same place, or it reports a key missing that is
    # sitting right there. Values are never printed, only their presence.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except Exception:
        pass
    if not os.getenv("RUN_LIVE_TESTS"):
        print("  skipped — set RUN_LIVE_TESTS=1 (this one calls a real API)")
        print("\n  0/0 correct")
        return False
    for key in ("SAMBANOVA_API_KEY", "CARTESIA_API_KEY"):
        if not os.getenv(key):
            print(f"  skipped — {key} is not set")
            print("\n  0/0 correct")
            return False
    return True


class Caller:
    """One websocket, used the way a browser uses it."""

    def __init__(self, http, server, audio=False):
        self.http = http
        self.server = server
        self.audio = audio
        self.ws = None

    async def __aenter__(self):
        self.ws = await self.http.ws_connect(f"{self.server.base}/ws")
        await self.ws.receive()
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()
        return False

    async def say(self, text, search=False, call=True, timeout=120):
        """Returns everything the server said back about this turn."""
        await self.ws.send_json({"type": "prompt", "text": text,
                                 "call": call, "search": search})
        out = {"reply": None, "team": None, "caller": None, "route": None,
               "language": None, "order": None, "audio_bytes": 0, "error": None,
               "held": []}
        while True:
            try:
                msg = await asyncio.wait_for(
                    self.ws.receive(), timeout=10 if out["reply"] is not None else timeout)
            except asyncio.TimeoutError:
                return out
            if msg.type is aiohttp.WSMsgType.BINARY:
                out["audio_bytes"] += max(0, len(msg.data) - 2)
                continue
            if msg.type is not aiohttp.WSMsgType.TEXT:
                return out
            event = json.loads(msg.data)
            kind = event.get("type")
            if kind == "handoff":
                out["team"] = event.get("team")
            elif kind == "caller":
                out["caller"] = event.get("name")
            elif kind == "route":
                out["route"] = event.get("tier")
            elif kind == "held":
                out["held"].append(event.get("reason"))
            elif kind == "error":
                out["error"] = event.get("message")
            elif kind == "done" and out["reply"] is None:
                out["reply"] = event.get("reply") or ""
                metrics = event.get("metrics") or {}
                out["language"] = metrics.get("language")
                out["order"] = metrics.get("order")
        return out


def report(results):
    """results: list of (ok, line). Prints them and the score."""
    for ok, line in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {line}")
    bad = sum(not ok for ok, _ in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


def run(coro_factory, audio=False):
    """Boilerplate every live suite shares: skip, start, sign in, report."""
    if not wanted():
        return 0

    async def main():
        with Server(live=True, **({} if audio else {"TEXT_ONLY": "1"})) as server:
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(cookie_jar=jar) as http:
                await sign_in(http, server)
                return report(await coro_factory(http, server))

    return asyncio.run(main())
