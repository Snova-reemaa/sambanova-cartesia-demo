"""Live SambaNova -> Cartesia voice demo.

Serves static/ and a websocket at /ws. One Cartesia socket is opened per browser
session and kept warm across turns, so "time to first sound" reflects what a real
user would feel rather than including a cold handshake.

    python server.py            # then open http://localhost:8080
"""

import asyncio
import hmac
import json
import os
import secrets
import time

from aiohttp import web, WSMsgType
from dotenv import load_dotenv

load_dotenv()

from sambanova import get_client as get_llm, get_model, stream_chat
from cartesia_tts import get_client as get_tts, output_format

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

COOKIE_NAME = "voiceloop_session"
SESSIONS = set()

# Every turn spends real API credit, so the app is never served ungated. Set
# DEMO_PASSPHRASE to something you can read out on a call; otherwise one is
# generated at startup and printed to the console.
PASSPHRASE = os.getenv("DEMO_PASSPHRASE", "").strip()
GENERATED_PASSPHRASE = not PASSPHRASE
if GENERATED_PASSPHRASE:
    PASSPHRASE = secrets.token_urlsafe(9)

SAMPLE_RATE = 44100
SYSTEM_PROMPT = (
    "You are a voice assistant. Reply in two or three short spoken sentences. "
    "Plain words only: no lists, no markdown, no emoji, no stage directions."
)
MAX_HISTORY = 8  # user+assistant messages retained for context


class Turn:
    """Timings for one prompt, in seconds from the moment the prompt was sent."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.first_token = None
        self.last_token = None
        self.first_audio = None
        self.last_audio = None
        self.audio_bytes = 0

    def now(self):
        return time.perf_counter() - self.t0

    def metrics(self):
        audio_seconds = self.audio_bytes / (SAMPLE_RATE * 2)
        total = self.last_audio or self.now()
        return {
            "first_token_s": self.first_token,
            "last_token_s": self.last_token,
            "first_audio_s": self.first_audio,
            "total_s": total,
            "audio_seconds": audio_seconds,
            "realtime_factor": (audio_seconds / total) if total else None,
        }


async def run_turn(ws_out, conn, llm, history, prompt):
    """Stream one prompt through the LLM into a fresh Cartesia context, relaying
    text events and raw PCM frames to the browser as they arrive."""
    turn = Turn()
    voice_id = os.getenv("CARTESIA_VOICE_ID", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")
    tts_model = os.getenv("CARTESIA_MODEL", "sonic-2")

    ctx = conn.context(
        model_id=tts_model,
        voice={"mode": "id", "id": voice_id},
        output_format=output_format(),
        language="en",
    )
    reply = ""

    await ws_out.send_json({"type": "turn_start", "prompt": prompt})

    async def send_text():
        nonlocal reply
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
            {"role": "user", "content": prompt}
        ]
        async for delta in stream_chat(llm, messages):
            if turn.first_token is None:
                turn.first_token = turn.now()
                await ws_out.send_json({"type": "first_token", "t": turn.first_token})
            reply += delta
            await ws_out.send_json({"type": "token", "text": delta, "t": turn.now()})
            await ctx.push(delta)
        turn.last_token = turn.now()
        await ws_out.send_json({"type": "text_done", "t": turn.last_token})
        await ctx.no_more_inputs()

    async def receive_audio():
        async for res in ctx.receive():
            if res.type == "chunk" and res.audio:
                if turn.first_audio is None:
                    turn.first_audio = turn.now()
                    await ws_out.send_json(
                        {
                            "type": "first_audio",
                            "t": turn.first_audio,
                            "sample_rate": SAMPLE_RATE,
                        }
                    )
                turn.audio_bytes += len(res.audio)
                await ws_out.send_bytes(res.audio)
            elif res.type == "error":
                raise RuntimeError(res.message or res.title or "Cartesia error")
        turn.last_audio = turn.now()

    await asyncio.gather(send_text(), receive_audio())

    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": reply})
    del history[:-MAX_HISTORY]

    await ws_out.send_json({"type": "done", "reply": reply, "metrics": turn.metrics()})


async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    llm = get_llm()
    tts = get_tts()
    history = []
    busy = False

    t_setup = time.perf_counter()
    manager = tts.tts.websocket_connect()
    try:
        conn = await manager.__aenter__()
    except Exception as exc:
        await ws.send_json({"type": "error", "message": f"Cartesia connect failed: {exc}"})
        await ws.close()
        return ws
    setup_ms = (time.perf_counter() - t_setup) * 1000

    await ws.send_json(
        {
            "type": "ready",
            "socket_setup_ms": setup_ms,
            "llm_model": get_model(),
            "tts_model": os.getenv("CARTESIA_MODEL", "sonic-2"),
            "sample_rate": SAMPLE_RATE,
        }
    )

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            if data.get("type") != "prompt":
                continue

            prompt = (data.get("text") or "").strip()
            if not prompt or busy:
                continue

            busy = True
            try:
                await run_turn(ws, conn, llm, history, prompt)
            except Exception as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
            finally:
                busy = False
    finally:
        try:
            await manager.__aexit__(None, None, None)
        except Exception:
            pass
        await tts.close()

    return ws


async def index(request):
    return web.FileResponse(os.path.join(STATIC, "index.html"))


# ------------------------------------------------------------------ access

def is_https(request):
    """True when the visitor reached us over TLS, including via a tunnel."""
    return request.headers.get("X-Forwarded-Proto", request.scheme) == "https"


def authorised(request):
    token = request.cookies.get(COOKIE_NAME)
    return bool(token) and token in SESSIONS


@web.middleware
async def gate(request, handler):
    # /static holds no secrets and the login page needs the stylesheet.
    if request.path.startswith("/static") or request.path == "/login":
        return await handler(request)

    if authorised(request):
        return await handler(request)

    if request.path == "/ws":
        return web.Response(status=401, text="unauthorized")

    raise web.HTTPFound("/login")


async def login(request):
    if request.method == "GET":
        return web.FileResponse(os.path.join(STATIC, "login.html"))

    data = await request.post()
    given = (data.get("passphrase") or "").strip()

    # Constant-time compare so the response time leaks nothing about the value.
    if not hmac.compare_digest(given, PASSPHRASE):
        raise web.HTTPFound("/login?bad=1")

    token = secrets.token_urlsafe(32)
    SESSIONS.add(token)

    response = web.HTTPFound("/")
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite="Lax",
        secure=is_https(request),
        max_age=60 * 60 * 8,
    )
    raise response


def main():
    for key in ("SAMBANOVA_API_KEY", "CARTESIA_API_KEY"):
        if not os.getenv(key):
            raise SystemExit(f"{key} is missing from .env")

    app = web.Application(middlewares=[gate])
    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_post("/login", login)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC)

    port = int(os.getenv("PORT", "8080"))
    print(f"\n  listening on http://localhost:{port}")
    print(f"  passphrase: {PASSPHRASE}")
    if GENERATED_PASSPHRASE:
        print("  (generated for this run — set DEMO_PASSPHRASE in .env to fix it)")
    print()
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
