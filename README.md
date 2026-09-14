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

**Model and voice pickers.** The server asks both providers what this account
can use — every SambaNova chat model, the first 20 Cartesia voices — and offers
them as dropdowns. Switching model between turns and watching the timeline
change is the most useful thing in the demo. Choices persist per browser; a
model or voice the account does not have falls back to the `.env` default rather
than being passed through to the API.

Measured from one warm session, same prompt and voice:

| model | first token | first sound |
| --- | --- | --- |
| Meta-Llama-3.3-70B-Instruct | 1162 ms | 1800 ms |
| gpt-oss-120b | 1554 ms | 1932 ms |
| gemma-4-31B-it | 1997 ms | 2462 ms |

Note the ordering: the 70B model is the *fastest* of the three. Parameter count
does not predict latency here — how well a given model is served does.

Speech-to-text is deliberately kept in the browser, using the built-in
`SpeechRecognition` API (Chrome, Edge, or Safari). That keeps the demo to two
API keys and puts transcription closest to the microphone, where it costs no
round trip.

To move it server-side, replace the `SpeechRecognition` block in
`static/app.js` with an upload to whichever STT endpoint you prefer and send the
resulting text as the existing `{"type": "prompt"}` message. Nothing else in the
pipeline changes — the server only ever sees text.

## Where the latency actually is

SambaNova returns its own inference timings in the usage frame that closes every
stream — the same choice-less frame that used to crash this demo. The server
reads them and the UI shows both clocks side by side:

```
browser first token      1103 ms
inference (SambaNova)     152 ms
network overhead          950 ms
```

**86% of what looks like model latency is the round trip from this laptop.**
Inference itself is about 150 ms, at roughly 240 tokens/sec after the first.
The timeline draws the inference slice in solid amber at the right-hand end of
the waiting bar, so the two are visually separable.

This matters for demos: a bad wifi connection makes SambaNova look slow when it
is not. Run the demo near the datacentre and the whole thing gets a second
faster without a line of code changing.

## Web search, without going quiet

Ask something current and the model calls `web_search` instead of answering from
memory. The interesting part is what happens to the voice while the search runs:

```
1146 ms  decided to search "latest news SambaNova"
1964 ms  FIRST SOUND — "One moment. Let me look that up for you."
2665 ms  4 sources returned
9805 ms  done
```

The bridge phrase is pushed into the **same Cartesia context** as the answer
that follows it, so synthesis simply carries on. There is no gap, no second
audio stream, and no silence to sit through — the user hears a voice 700 ms
before the search has returned anything.

Turn it off with the Web search toggle to get the plain one-round-trip path.

**Triggering.** Left alone the model searches for everything, including "what is
a GPU". The system prompt spells out when *not* to search, which took a small
trigger set from 5/10 to 6/6. It also has to be told never to narrate its tool
reasoning: because text streams straight to the speaker, a stray "this is not a
time-sensitive fact so no web search is needed" gets spoken aloud and cannot be
taken back.

**Providers.** Set one of `TAVILY_API_KEY`, `BRAVE_API_KEY` or `SERPER_API_KEY`
and it is picked up automatically. With no key the demo scrapes DuckDuckGo's
lite endpoint so it runs out of the box — that path parses HTML which can change
without notice and will rate limit under load, so add a key before showing
anyone. A failed search never kills a turn; the model just answers without it.

## Asking several things at once

Keep talking while it works. Each request becomes its own card with live status,
and up to four run at once.

What happens to the audio depends on how many are in flight:

- **One on its own** streams straight to the speaker as it is synthesised, so a
  single question still reaches first sound in about two seconds. Nothing about
  the common case got slower.
- **Two or more** all buffer silently. When the last one lands, the assistant
  says *"Your answers on a GPU, quantization, and a token are ready — which
  would you like to hear first?"* and shows a button per answer. Whichever you
  pick starts **instantly**, because it finished downloading while you were
  listening to the question.

Answers are named by what makes them different, not by their opening words —
three questions that all start "in one sentence, what is…" come out as
"a GPU", "quantization", "a token".

**Barge-in.** Pressing the mic stops whatever is playing, so you can talk over
an answer instead of waiting it out. There is also an explicit Stop button while
audio is playing.

### Two different concurrency limits

They are not the same, and the difference shapes the design:

| | limit | behaviour when exceeded |
| --- | --- | --- |
| SambaNova | none at this volume | three concurrent streams finished in 1837 ms |
| Cartesia | **2** on the current plan | the request fails outright — no queueing |

So language-model calls all start immediately and only *synthesis* waits for a
free slot. A queued request shows a `queued` chip, and its wait appears on the
timeline as a long pale Cartesia bar. Raise `CARTESIA_CONCURRENCY` in `.env` if
the plan is upgraded.

Three questions asked together: 11.1 s of work in 5.0 s of wall clock.

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
