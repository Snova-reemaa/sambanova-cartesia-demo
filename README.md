# SambaNova → Cartesia voice demo

Streams a reply out of SambaNova inference and straight into Cartesia speech
synthesis, so audio starts playing while the language model is still talking.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

`.env` needs:

```
SAMBANOVA_API_KEY=...
SAMBANOVA_MODEL=Meta-Llama-3.3-70B-Instruct
CARTESIA_API_KEY=...
CARTESIA_MODEL=sonic-2
CARTESIA_VOICE_ID=...
```

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
