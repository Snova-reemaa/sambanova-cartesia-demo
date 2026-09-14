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
VOICE_LIMIT = 20  # how many Cartesia voices to offer in the picker
MAX_CONCURRENT = 4  # requests accepted in flight at once on one connection

# Cartesia caps concurrent synthesis per subscription (2 on the current plan);
# exceeding it fails the whole context rather than queueing. SambaNova has no
# such limit at this volume, so language-model calls all start immediately and
# only synthesis waits for a slot.
TTS_CONCURRENCY = int(os.getenv("CARTESIA_CONCURRENCY", "2"))
TTS_SLOTS = None  # asyncio.Semaphore, created once the loop exists

# Filled once at startup so every browser session shares one lookup.
CATALOG = {"models": [], "voices": []}


async def load_catalog(llm, tts):
    """Ask both providers what this account can actually use. Failures are not
    fatal — the picker just falls back to whatever .env specifies."""
    try:
        listing = await llm.models.list()
        CATALOG["models"] = sorted(m.id for m in listing.data)
    except Exception as exc:
        print(f"  ! could not list SambaNova models ({exc}); using .env default")
        CATALOG["models"] = [get_model()]

    try:
        page = await tts.voices.list(limit=VOICE_LIMIT)
        items = getattr(page, "data", None) or list(page)
        CATALOG["voices"] = [
            {
                "id": v.id,
                "name": v.name,
                "language": getattr(v, "language", "") or "",
                "description": (getattr(v, "description", "") or "")[:90],
            }
            for v in items
        ]
    except Exception as exc:
        print(f"  ! could not list Cartesia voices ({exc}); using .env default")
        CATALOG["voices"] = []


class Turn:
    """Timings for one prompt, in seconds from the moment the prompt was sent."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.first_token = None
        self.last_token = None
        self.tts_start = None    # when a synthesis slot came free
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
            "tts_start_s": self.tts_start,
            "first_audio_s": self.first_audio,
            "total_s": total,
            "audio_seconds": audio_seconds,
            "realtime_factor": (audio_seconds / total) if total else None,
        }


def default_voice():
    return os.getenv("CARTESIA_VOICE_ID", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")


def pick_model(requested):
    """Only ever hand the API a model this account was told it has."""
    if requested and (not CATALOG["models"] or requested in CATALOG["models"]):
        return requested
    return get_model()


def pick_voice(requested):
    known = {v["id"] for v in CATALOG["voices"]}
    if requested and (not known or requested in known):
        return requested
    return default_voice()


class Channel:
    """Serialises writes to one browser socket.

    Turns run concurrently, so without this two tasks can interleave halves of
    a frame. Every audio frame is prefixed with its turn id, because binary
    frames carry no other way to say which answer they belong to.
    """

    def __init__(self, ws):
        self.ws = ws
        self.lock = asyncio.Lock()

    async def event(self, payload):
        async with self.lock:
            await self.ws.send_json(payload)

    async def audio(self, turn_id, pcm):
        async with self.lock:
            await self.ws.send_bytes(turn_id.to_bytes(2, "big") + pcm)


async def synthesise(chan, conn, turn_id, text, voice_id):
    """Speak one fixed string — used for the assistant's own 'which first?'
    question, which has no language model in front of it."""
    ctx = conn.context(
        model_id=os.getenv("CARTESIA_MODEL", "sonic-2"),
        voice={"mode": "id", "id": voice_id},
        output_format=output_format(),
        language="en",
    )
    await ctx.push(text)
    await ctx.no_more_inputs()
    async for res in ctx.receive():
        if res.type == "chunk" and res.audio:
            await chan.audio(turn_id, res.audio)
        elif res.type == "error":
            raise RuntimeError(res.message or res.title or "Cartesia error")


async def run_turn(chan, conn, llm, history, turn_id, prompt, model=None, voice=None):
    """Stream one prompt through the LLM into its own Cartesia context, relaying
    text events and raw PCM frames to the browser as they arrive."""
    turn = Turn()
    voice_id = pick_voice(voice)
    llm_model = pick_model(model)
    tts_model = os.getenv("CARTESIA_MODEL", "sonic-2")
    reply = ""

    # Snapshot history now: a turn started alongside others should not see
    # replies that land while it is still running.
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(history) + [
        {"role": "user", "content": prompt}
    ]

    await chan.event({
        "type": "turn_start", "turn_id": turn_id, "prompt": prompt,
        "model": llm_model, "voice_id": voice_id,
    })

    # The language model runs right away; text waits here for a synthesis slot.
    pending_text = asyncio.Queue()

    async def generate():
        nonlocal reply
        async for delta in stream_chat(llm, messages, model=llm_model):
            if turn.first_token is None:
                turn.first_token = turn.now()
                await chan.event({"type": "first_token", "turn_id": turn_id, "t": turn.first_token})
            reply += delta
            await chan.event({"type": "token", "turn_id": turn_id, "text": delta, "t": turn.now()})
            await pending_text.put(delta)
        turn.last_token = turn.now()
        await chan.event({"type": "text_done", "turn_id": turn_id, "t": turn.last_token})
        await pending_text.put(None)

    async def synthesize():
        async with TTS_SLOTS:
            turn.tts_start = turn.now()
            await chan.event({"type": "tts_start", "turn_id": turn_id, "t": turn.tts_start})

            ctx = conn.context(
                model_id=tts_model,
                voice={"mode": "id", "id": voice_id},
                output_format=output_format(),
                language="en",
            )

            async def pump():
                while True:
                    delta = await pending_text.get()
                    if delta is None:
                        break
                    await ctx.push(delta)
                await ctx.no_more_inputs()

            async def drain():
                async for res in ctx.receive():
                    if res.type == "chunk" and res.audio:
                        if turn.first_audio is None:
                            turn.first_audio = turn.now()
                            await chan.event({
                                "type": "first_audio", "turn_id": turn_id,
                                "t": turn.first_audio, "sample_rate": SAMPLE_RATE,
                            })
                        turn.audio_bytes += len(res.audio)
                        await chan.audio(turn_id, res.audio)
                    elif res.type == "error":
                        raise RuntimeError(res.message or res.title or "Cartesia error")
                turn.last_audio = turn.now()

            await asyncio.gather(pump(), drain())

    await asyncio.gather(generate(), synthesize())

    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": reply})
    del history[:-MAX_HISTORY]

    await chan.event({
        "type": "done", "turn_id": turn_id, "reply": reply, "metrics": turn.metrics(),
    })


async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    llm = get_llm()
    tts = get_tts()
    history = []
    chan = Channel(ws)
    running = {}       # turn_id -> task
    next_turn_id = 1

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
            "models": CATALOG["models"],
            "voices": CATALOG["voices"],
            "default_voice": default_voice(),
            "max_concurrent": MAX_CONCURRENT,
        }
    )

    async def supervise(turn_id, coro):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await chan.event({"type": "error", "turn_id": turn_id, "message": str(exc)})
        finally:
            running.pop(turn_id, None)
            await chan.event({"type": "idle", "turn_id": turn_id, "running": len(running)})

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue

            kind = data.get("type")

            if kind == "prompt":
                prompt = (data.get("text") or "").strip()
                if not prompt:
                    continue
                if len(running) >= MAX_CONCURRENT:
                    await chan.event({
                        "type": "rejected",
                        "message": f"Already working on {MAX_CONCURRENT} requests.",
                    })
                    continue

                turn_id = next_turn_id
                next_turn_id += 1
                coro = run_turn(
                    chan, conn, llm, history, turn_id, prompt,
                    model=data.get("model"),
                    voice=data.get("voice_id"),
                )
                running[turn_id] = asyncio.ensure_future(supervise(turn_id, coro))

            elif kind == "say":
                # The assistant's own "which would you like first?" question.
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                try:
                    await chan.event({"type": "say_start", "turn_id": 0})
                    await synthesise(chan, conn, 0, text, pick_voice(data.get("voice_id")))
                    await chan.event({"type": "say_done", "turn_id": 0})
                except Exception as exc:
                    await chan.event({"type": "error", "turn_id": 0, "message": str(exc)})
    finally:
        for task in list(running.values()):
            task.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
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


async def on_startup(app):
    global TTS_SLOTS
    TTS_SLOTS = asyncio.Semaphore(TTS_CONCURRENCY)

    llm = get_llm()
    tts = get_tts()
    try:
        await load_catalog(llm, tts)
    finally:
        await tts.close()
    print(f"  {len(CATALOG['models'])} models · {len(CATALOG['voices'])} voices available")
    print(f"  up to {MAX_CONCURRENT} requests in flight, {TTS_CONCURRENCY} synthesising at once")


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
    app.on_startup.append(on_startup)

    port = int(os.getenv("PORT", "8080"))
    print(f"\n  listening on http://localhost:{port}")
    print(f"  passphrase: {PASSPHRASE}")
    if GENERATED_PASSPHRASE:
        print("  (generated for this run — set DEMO_PASSPHRASE in .env to fix it)")
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
