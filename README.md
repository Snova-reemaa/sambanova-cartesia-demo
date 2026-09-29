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
./run.sh
```

Creates the venv and installs dependencies if they are missing, checks your
keys are filled in, frees the port if an old server is still holding it, starts
the server and opens the browser.

**`python server.py` will not work.** There is no `python` on PATH on this
machine, only `python3`, and the system `python3` has none of the dependencies
— they live in `./venv`. If you would rather not use the script, the long form
is `venv/bin/python server.py`.

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

Tavily is the one to reach for: 1,000 credits a month, no card, and it returns
a synthesised `answer` alongside the raw results. That matters here, because its
per-result `content` is a raw page extract — one live query put an Instagram
caption at the top and fed the model text reading "NOW PLAYING video ###". The
`answer` field gives a clean sentence built from the same sources, which is what
a spoken reply needs. Brave dropped its free tier in February 2026 and now wants
a card on file.

Check a key without starting the app:

```bash
venv/bin/python search.py "who won the 2026 super bowl"
```

It prints the provider it chose and confirms the key was read — by length and
last four characters, never the key itself.

## Call mode

Press the **video button**. Camera up, mic open, captions on screen, a status
pill reading listening / thinking / speaking, and no button to press between
turns. Talk over it and it stops mid-word. Press it again to hang up.

Four controls inside the call: mute, camera on/off, **upload**, and hang up.
Upload takes photos, screenshots and video — a video is turned into frames the
same way it is outside a call, and the attachment waits for you to ask
something about it.

A refused camera or microphone costs the call that one thing, not the call. It
says which, and the rest keeps working.

**It knows it is on a video call.** Without being told, the phone-agent framing
made it answer "hi can you see me" with "I'm just a voice on the phone, so no,
I can't see you" — false the moment the camera is up, and it reads as broken.
Call turns carry a different system prompt, so it now says "yeah, I can see you
just fine — can you hold up what you're having trouble with?" Voice-only turns
still correctly say they cannot see.

### It answers before the model has answered

A plain turn took about two seconds to make any sound, and almost none of that
was the model:

```
SambaNova inference     152 ms    9%
network round trip      980 ms   49%
Cartesia first audio    700 ms   35%
```

A person asked a question does not sit in silence for two seconds; they say
"right, let me see" and then answer. On a call, synthesis now starts on a
holding phrase the instant the prompt lands, so first sound stops waiting on
the model at all.

| | first sound |
| --- | --- |
| before | 2018 / 2154 / 1682 ms |
| after | **855 / 570 / 600 ms** |

Three details make it bearable rather than irritating. Turns of three words or
fewer skip it, because "right, let me see" in front of "hi there" is absurd and
those answers arrive quickly anyway. The phrase already spoken is added to the
conversation with an instruction not to acknowledge twice, which stopped "Okay,
one second. Okay, so you're looking for a refund." And the phrases rotate,
never repeating within two turns.

The holding phrases are two beats rather than one deliberately: "Right." is
about half a second of speech against a round trip of one, which left an
audible gap. "Right, let me see." covers it, and playback measures continuous.

Switching Cartesia model does not help — sonic-3 came back at 1.08 s against
sonic-2's 1.11 s, and sonic-turbo was slower at 1.51 s.

### It starts looking before you finish talking

On a call the camera already shows whatever you are holding up, so there is no
reason to wait until you stop speaking to start reading it. The first word of an
utterance fires a look; by the time the sentence lands the description is
usually back.

Same image, same question, 1.6 s of speaking:

| | vision wait | search starts | total |
| --- | --- | --- | --- |
| wait until they finish | 3181 / 2291 ms | 3716 / 2795 ms | 14.4 / 13.8 s |
| start on the first word | **577 / 597 ms** | 2313 / 1092 ms | **10.2 / 11.0 s** |

About two seconds off the vision wait and three and a half off the turn. The
cost is a vision call on utterances that turn out not to need one, at roughly
$0.0002 each.

Two smaller wins came from measuring rather than guessing. The vision prompt
was 413 tokens and returned nine — "I see a pair of black oval glasses" — and a
terse version costs 312 and describes more. And a 768 px frame costs gemma
exactly the same 413 prompt tokens as a 384 px one, because the image is
normalised either way, so frames are sent at 512 px now: identical inference
cost, a third of the upload.

The camera is always on in a call, so attaching every frame would put ~2.5 s of
vision on every turn. A frame is attached only when the words point at
something — "what is this", "find one like mine", "is this cracked" — judged by
a deictic word plus a looking word, or by a seeing word on its own. That last
part matters: "can you see me" contains nothing that points, so it used to send
no frame at all. Outside a call, having the camera on at all
is taken as intent, so every turn gets a frame. The camera button in the call
sends the current frame regardless.

### Errors

Nothing from a provider exception reaches the browser verbatim. They quote the
credential that failed — "Incorrect API key provided: ba630f*****3cca" — along
with request ids and internal JSON, and a browser is the last place any of that
belongs.

The caller gets a sentence about what to do: credentials rejected, out of
credit, service busy, timed out, or a generic "the server log has the detail".
The console keeps the full exception, with anything key-shaped replaced by
`[redacted]` in case a provider puts a secret somewhere new.

### If a change does not seem to take effect

Static files are served with `Cache-Control: no-store`. aiohttp sends no cache
headers by default, so browsers hold on to `app.js` and `style.css` — which
means testing a fix against the previous version of the file and concluding it
did not work. If you are on an older build, hard-reload once (Cmd-Shift-R).

## Photos, video and the camera

Attach a photo, drop a video in, or turn the camera on. All three end up as the
same thing — still frames handed to a vision model — because neither vision
model on this account accepts video (`video_url` returns a 500).

| input | what happens |
| --- | --- |
| Photo | resized to 768 px, sent as one frame |
| Video | the browser seeks to evenly spaced timestamps and grabs up to 6 stills |
| Camera | live self-view; capture frames, or just ask and it grabs the current one |

**Camera notes.** No `facingMode` constraint is set — asking a laptop for the
rear camera asks for one it does not have — so the browser picks, and a device
dropdown appears when there is more than one (Continuity Camera counts). The
choice is remembered per browser.

The preview mirrors for a front-facing camera and does not for a rear one. The
**captured frame is never mirrored either way**: a serial number or error
message held up to the lens has to reach the model the right way round, even
though that makes the preview and the capture disagree.

Camera failures name their cause and what to do — permission denied, no device,
another app holding it, or a non-https origin.

Drag and drop anywhere on the page works, and so does pasting a screenshot.

**The voice does not wait for the image.** Same trick as web search: a bridge
phrase goes into the live synthesis context the instant an attachment arrives,
so the caller hears something while the vision model reads.

```
   1 ms  turn start, 1 attachment
 835 ms  FIRST SOUND — "Let me take a look at that."
2459 ms  vision finished
```

First sound lands *faster* than a normal turn (0.85 s against 2.0 s), because
the bridge phrase needs no language model at all.

### Which model reads the image

`gemma-4-31B-it`, and the choice is not close. Both it and MiniMax-M3 pass a
single-image test, but on batches:

| frames | gemma-4-31B-it | MiniMax-M3 |
| --- | --- | --- |
| 3 solid colours | 3/3, in order | **0/3** — answered "Black, Gray, White" |
| 6 solid colours | 6/6, in order | 3/6 |
| prompt tokens, 3 → 6 images | 810 → 1584 | 233 → 266 |

M3's token count barely moves as images are added, which is the tell: it is not
ingesting the batch. Use it for one image and a long history, never for frames.
Set `SAMBANOVA_VISION_MODEL` to override.

Given four frames of a line growing across a screen, gemma tracked the change
across them — and, correctly, pointed out that a perfectly straight line is not
how glass actually cracks.

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

**Hands-free and barge-in.** Press the mic once and it stays open. Talk and it
sends when you stop; talk over an answer and it stops mid-word and listens.
Press again to turn it off. A single-shot mic defeats the point of barge-in —
by the time you have reached for the button, the answer has finished.

Chrome ends a recognition session on its own every so often, so it is restarted
automatically until you switch it off.

The mic hears the speaker, so a transcript that is mostly the words we are
currently saying is discarded as echo. That test only applies to utterances of
four words or more, deliberately: mistaking echo for speech just stops audio the
user can restart, while mistaking speech for echo means they cannot interrupt at
all. Short interruptions — "stop", "no, blue" — always count.

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
