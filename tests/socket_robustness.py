"""A bad frame must not end the call.

Only the JSON parse was guarded, so anything malformed past that point —
a payload that is not an object, a text field that is not a string, an id
that cannot be a dict key — raised out of the message loop and took the
whole websocket with it. The caller lost the call and saw nothing; the
traceback went to the server log.

Offline: the replies here never reach the language model, so what is
asserted is that the socket survives and the turn starts.
"""

import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import Server, sign_in                      # noqa: E402

# Each of these killed the call.
BAD_FRAMES = [
    ('"just a string"', "payload is a JSON scalar, not an object"),
    ('{"type":"prompt","text":123}', "prompt text is a number"),
    ('{"type":"draft","id":{"a":1},"text":"hi"}', "draft id cannot be a dict key"),
    ('{"type":"prelook","id":"abc","images":["data:image/png;base64,iVBORw0KGgo="]}',
     "prelook id is a string"),
    # Near neighbours of the above, cheap to cover now we are here.
    ('[]', "payload is a list"),
    ('null', "payload is null"),
    ('{"type":123}', "type is not a string"),
    ('{"text":"no type at all"}', "no type field"),
    ('{"type":"cancel","turn_id":"not-a-number"}', "turn id is not a number"),
    ('{"type":"prompt","text":"hi","images":"not-a-list"}', "images is not a list"),
    ('{"type":"say","text":null}', "say with no text"),
]


async def still_alive(ws):
    """Send a valid prompt and confirm the turn starts."""
    await ws.send_json({"type": "prompt", "text": "what are your opening hours",
                        "call": False, "search": False})
    while True:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=20)
        except asyncio.TimeoutError:
            return False
        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR):
            return False
        if msg.type is not aiohttp.WSMsgType.TEXT:
            continue
        import json
        event = json.loads(msg.data)
        # turn_start is emitted before the model is called, so this holds
        # offline. "error" is a fine outcome too: it means the turn ran and
        # failed to reach the provider, which is what offline looks like.
        if event.get("type") in ("turn_start", "error", "done"):
            return True


async def main():
    failures = []
    with Server() as server:
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as http:
            await sign_in(http, server)

            # One frame at a time, each on a fresh socket, so a failure
            # names the frame that caused it.
            for frame, why in BAD_FRAMES:
                async with http.ws_connect(f"{server.base}/ws") as ws:
                    await ws.receive()
                    await ws.send_str(frame)
                    ok = await still_alive(ws)
                print(f"  {'ok  ' if ok else 'FAIL'} survived: {why}")
                if not ok:
                    failures.append(why)

            # And all of them down one socket, which is closer to a client
            # that has simply gone wrong.
            async with http.ws_connect(f"{server.base}/ws") as ws:
                await ws.receive()
                for frame, _ in BAD_FRAMES:
                    await ws.send_str(frame)
                ok = await still_alive(ws)
            print(f"  {'ok  ' if ok else 'FAIL'} survived: all of them on one socket")
            if not ok:
                failures.append("all on one socket")

            # Oversized input. The turn must still start, on a trimmed
            # prompt, rather than a megabyte going to the model.
            async with http.ws_connect(f"{server.base}/ws") as ws:
                await ws.receive()
                await ws.send_json({"type": "prompt", "text": "x " * 300_000,
                                    "call": False, "search": False})
                ok = await still_alive(ws)
            print(f"  {'ok  ' if ok else 'FAIL'} survived: a 600,000 character prompt")
            if not ok:
                failures.append("huge prompt")

            # And a frame past the socket limit is refused without the
            # server falling over.
            try:
                async with http.ws_connect(f"{server.base}/ws",
                                           max_msg_size=0) as ws:
                    await ws.receive()
                    await ws.send_json({"type": "prompt",
                                        "text": "y" * (17 * 1024 * 1024)})
                    await asyncio.sleep(1.0)
            except Exception:
                pass
            async with http.ws_connect(f"{server.base}/ws") as ws:
                await ws.receive()
                ok = await still_alive(ws)
            print(f"  {'ok  ' if ok else 'FAIL'} still serving after an oversized frame")
            if not ok:
                failures.append("dead after oversized frame")

            # Attachments that cannot be used must be named, not dropped
            # in silence while the agent answers about the survivors.
            tiny = "data:image/jpeg;base64," + "A" * 100
            huge = "data:image/jpeg;base64," + "A" * 3_000_000
            for images, expect, label in [
                    ([tiny, huge, "nope"], True, "an oversized image and a non-image"),
                    ([tiny] * 12, True, "more frames than the limit"),
                    ([tiny, tiny], False, "two usable images (nothing to report)")]:
                async with http.ws_connect(f"{server.base}/ws") as ws:
                    await ws.receive()
                    await ws.send_json({"type": "prompt", "text": "what is this",
                                        "images": images, "call": False,
                                        "search": False})
                    told = False
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=20)
                        except asyncio.TimeoutError:
                            break
                        if msg.type is not aiohttp.WSMsgType.TEXT:
                            continue
                        import json as _json
                        event = _json.loads(msg.data)
                        if event.get("type") == "attachments_dropped":
                            told = bool(event.get("reasons"))
                        if event.get("type") in ("done", "error"):
                            break
                ok = told is expect
                print(f"  {'ok  ' if ok else 'FAIL'} the caller is told about: {label}")
                if not ok:
                    failures.append(label)

            # Hanging up. A tab closing is the commonest way a call ends,
            # and it used to raise "Cannot write to closing transport" out
            # of whichever write happened to be in flight.
            for when in (0.0, 0.05, 0.2, 0.5, 1.0, 2.0):
                ws = await http.ws_connect(f"{server.base}/ws")
                if when:
                    await ws.receive()
                    await ws.send_json({"type": "prompt", "text": "where is order 44812",
                                        "call": False, "search": False})
                    await asyncio.sleep(when)
                await ws.close()
            print("  ok   survived: six hang-ups, from instant to mid-reply")

            # And the server is still serving afterwards.
            async with http.ws_connect(f"{server.base}/ws") as ws:
                await ws.receive()
                ok = await still_alive(ws)
            print(f"  {'ok  ' if ok else 'FAIL'} still serving after the hang-ups")
            if not ok:
                failures.append("dead after hang-ups")

        crashes = server.tracebacks()
        print(f"  {'ok  ' if not crashes else 'FAIL'} server logged {crashes} tracebacks")
        if crashes:
            failures.append(f"{crashes} tracebacks")

    total = len(BAD_FRAMES) + 9
    print(f"\n  {total - len(failures)}/{total} correct")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
