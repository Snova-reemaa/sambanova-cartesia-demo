"""Play the scenarios through VoxNova and record every LLM request.

    venv/bin/python bench/run.py              all ten
    venv/bin/python bench/run.py 05 07        just these

A real server from this checkout, in TEXT_ONLY mode (no Cartesia
synthesis), against SambaNova with the keys in .env, the demo inbox on
and its own fresh copy of the demo data. The recorder (bench/recorder.py)
writes each request as sent to bench/raw/requests.jsonl; this script
writes when each turn started and ended to bench/raw/turns.jsonl, which
is how export.py knows which request belongs to which turn.

Spends SambaNova tokens only. Web search runs only where a scenario turn
says "search": true. Fireworks and Together are not called.
"""

import asyncio
import glob
import json
import os
import sys
import time

import aiohttp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from tests.harness import PASSPHRASE, Server                   # noqa: E402

RAW = os.path.join(HERE, "raw")
REQUESTS = os.path.join(RAW, "requests.jsonl")
TURNS = os.path.join(RAW, "turns.jsonl")
# After hang-up the call is written up in the background; wait for it, so
# that request lands in this scenario's window and not the next one's.
WRITE_UP_SECONDS = 12


async def play(http, base, scenario, log):
    demo_code = None
    async with http.ws_connect(f"{base}/ws", max_msg_size=0) as ws:
        await ws.receive_json()                         # ready
        for n, turn in enumerate(scenario["turns"], 1):
            text = turn["say"].replace("{code}", demo_code or "000000")
            started = time.time()
            await ws.send_json({"type": "prompt", "text": text, "call": False,
                                "search": bool(turn.get("search")),
                                "check": True,
                                "language": turn.get("language") or scenario.get("language")})
            replies, waiting = [], 1
            while waiting:
                try:
                    m = await asyncio.wait_for(ws.receive_json(), 120)
                except asyncio.TimeoutError:
                    replies.append("(timed out)")
                    break
                if m.get("type") == "caller" and m.get("demo_code"):
                    demo_code = m["demo_code"]
                if m.get("type") in ("done", "error") and m.get("turn_id"):
                    replies.append(m.get("reply") or m.get("message") or "")
                    waiting -= 1
                    if m.get("type") == "done" and not waiting:
                        # A handoff: the specialist speaks next without being
                        # asked. Give that turn a moment to start.
                        try:
                            nxt = await asyncio.wait_for(ws.receive_json(), 3)
                        except asyncio.TimeoutError:
                            nxt = None
                        if nxt and nxt.get("type") == "turn_start":
                            waiting += 1
            log({"scenario": scenario["id"], "turn": n, "said": text,
                 "started": started, "finished": time.time(), "replies": replies})
            print(f"    {n}. {text[:60]!r} -> {(replies[-1] if replies else '')[:70]!r}")
    started = time.time()
    await asyncio.sleep(WRITE_UP_SECONDS)
    log({"scenario": scenario["id"], "turn": "hang-up", "said": None,
         "started": started, "finished": time.time(), "replies": []})


async def main(only):
    os.makedirs(RAW, exist_ok=True)
    files = sorted(glob.glob(os.path.join(HERE, "scenarios", "*.json")))
    if only:
        files = [f for f in files if any(os.path.basename(f).startswith(o) for o in only)]
    for path in (REQUESTS, TURNS):
        if os.path.exists(path):
            os.remove(path)

    def log(row):
        with open(TURNS, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with Server(live=True, TEXT_ONLY="1", DEMO_INBOX="1", BENCH_LOG=REQUESTS) as s:
        for path in files:
            scenario = json.load(open(path, encoding="utf-8"))
            print(f"  {scenario['id']}: {scenario['title']}")
            # A fresh browser per scenario: new session, new conversation.
            async with aiohttp.ClientSession(
                    cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
                await http.post(f"{s.base}/login",
                                data={"passphrase": PASSPHRASE, "consent": "yes"})
                await play(http, s.base, scenario, log)
        print(f"\n  server log tracebacks: {s.tracebacks()}")
    n = sum(1 for _ in open(REQUESTS, encoding="utf-8")) if os.path.exists(REQUESTS) else 0
    print(f"  {n} requests recorded in {os.path.relpath(REQUESTS, ROOT)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
