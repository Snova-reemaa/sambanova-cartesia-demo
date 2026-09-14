# SambaNova → Cartesia voice demo

Streams a reply out of SambaNova inference and straight into Cartesia speech
synthesis, so audio starts playing while the language model is still talking.

## Setup

```bash
git clone https://github.com/Snova-reemaa/sambanova-cartesia-demo.git
cd sambanova-cartesia-demo
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and paste in **your own** two API keys:

| variable | where to get it |
| --- | --- |
| `SAMBANOVA_API_KEY` | https://cloud.sambanova.ai/apis |
| `CARTESIA_API_KEY` | https://play.cartesia.ai/keys |

The model, voice, and port defaults in `.env.example` all work as-is, so those
two lines are the only ones you have to fill in.

`.env` is gitignored and must stay that way. Get your own keys rather than
copying someone else's — they are per-account credentials, and a key pasted
into chat or email should be considered burned.

## Live demo — mic or typing

```bash
venv/bin/python server.py
```

Open http://localhost:8080. Type a prompt, or press the mic and speak.
Shows a live timeline of where the latency goes, and keeps conversation
context across turns.

Speech-to-text is deliberately kept in the browser, using the built-in
`SpeechRecognition` API (Chrome, Edge, or Safari). That keeps the demo to two
API keys and puts transcription closest to the microphone, where it costs no
round trip.

To move it server-side, replace the `SpeechRecognition` block in
`static/app.js` with an upload to whichever STT endpoint you prefer and send the
resulting text as the existing `{"type": "prompt"}` message. Nothing else in the
pipeline changes — the server only ever sees text.

## Showing it to someone remote

The demo is passphrase-gated: `/` redirects to a login page and the websocket
returns 401 without a valid session cookie. Set `DEMO_PASSPHRASE` in `.env`, or
let the server generate one and print it at startup.

To put it on a temporary public URL:

```bash
brew install cloudflared          # once
cloudflared tunnel --url http://localhost:8080
```

That prints a `https://<random>.trycloudflare.com` address. Share it along with
the passphrase; both are needed. The URL dies the moment you stop `cloudflared`,
which is the point — there is nothing left exposed after the call.

Keep in mind the whole time the tunnel is up, every visitor's turns bill to
**your** API keys. Stop the tunnel when the demo ends, and change the passphrase
between audiences.

## One-shot CLI

```bash
venv/bin/python main.py "explain quantum entanglement like I'm 5"
venv/bin/python main.py --no-play "same thing, silently"
```

Writes `output/reply.wav` and `output/timings.json`.

## Reading the numbers

Two things matter more than total time:

- **first sound** — when the user stops waiting. This is the headline metric.
- **synthesis rate** — audio-seconds produced per wall-second. Above 1.0x means
  speech can stream straight to a speaker without buffering first.

The Cartesia WebSocket handshake costs ~2 s and is deliberately **not** counted.
`server.py` opens one socket per browser session and reuses it for every turn,
which is what a production service does; `main.py` measures it as a separate
setup phase. Folding it into the request makes first-token latency look about
twice as bad as it is.
