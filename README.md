# VoxNova

A real-time voice support agent: SambaNova inference into
Cartesia speech.

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

## What it does with SambaNova's speed

Most voice agents make one slow model call per turn. SambaNova is fast enough
to run several models in parallel on every turn and still finish inside a human
pause. Every feature below is that idea, and each one shows itself on screen
as a line under the reply.

| feature | what you will see | switch |
| --- | --- | --- |
| Answer early | "Started answering 1.0 s before you finished" | Answer early |
| Fact-check | an unsupported sentence struck through, never spoken | Fact-check |
| Talker and thinker | a reasoning bar on the timeline beside the talker | Model: Auto |
| Per-turn routing | "Answered by Llama 3.1 8B: small talk" | Model: Auto |
| Mood | "You sound frustrated: slower, calmer voice" | always on |
| Interpreter | English in, Arabic out, and back | Interpreter |
| Memory | the caller's name and past calls in the left column | always on |
| Screen sharing | a bar saying the agent can see the shared window | the screen button |

### Answer early

When the caller pauses mid-sentence (320 ms of silence in the transcript) or
stops typing (450 ms), the browser sends what it has as a draft. The server
starts the whole turn on it: retrieval, translation, the model call. If the
final question has the same words, the turn adopts the draft and its first
token is already there; if not, the draft is cancelled and nothing it wrote is
ever heard. One draft runs at a time. Drafts are skipped for photos, the
camera, the screen and the interpreter, which need the real turn.

The cost is a model call for every draft that turns out not to match.

### Fact-check before speaking

Text now reaches Cartesia one sentence at a time. A sentence that makes a
checkable claim (a number, a date, a duration, a policy word) is checked
against what the agent actually has this turn: the policy documents, the order
record, the search results. The checks run in parallel and sentences are
released in order, so only the first claim costs a round trip; after that,
checking happens while earlier sentences are being spoken.

A sentence the checker cannot support is held back, shown struck through, and
replaced at the end with "let me double-check one detail on that". If the
checker itself fails or times out, the sentence goes out and that is logged:
a broken checker should not silence the agent. Set the model with
`SAMBANOVA_CHECK_MODEL`.

### A fast talker and a slow thinker, and routing

With Model set to Auto, every turn is routed before the model is called, by
a local rule (a model call to choose the model would cost a round trip):

- greetings and thanks on their own go to the smallest model on the account
- questions that need working out ("why", "difference between", "what's
  wrong", "walk me through") go to the talker and a reasoning model together
- everything else goes to the default model

On a hard question Llama says one or two sentences framing the problem while
`gpt-oss-120b` reasons in parallel; its answer is spoken straight after. The
timeline's reasoning lane shows the two running side by side. Override with
`SAMBANOVA_QUICK_MODEL` and `SAMBANOVA_THINK_MODEL`.

### The voice follows the caller's mood

Each utterance is read for frustration, confusion or hurry (words, capitals,
exclamation marks). The reply is told how to respond, and Cartesia is asked to
speak slower and calmer, or faster, through its experimental voice controls.
Frustration fades over two turns rather than snapping back. If the account's
Cartesia model refuses the controls, the first refusal turns them off for the
session and the turn is spoken without them. `CARTESIA_VOICE_CONTROLS=off`
disables them outright.

### Interpreter

Turn on Interpreter and pick the other language (Arabic on Auto). Anything
said in English is spoken in that language, and anything in it is spoken in
English. Nothing is answered and nothing goes into history. After each
utterance the microphone switches to the language the other person will reply
in; the "Next speaker" button switches it by hand.

### Memory

Nobody signs up. The caller gives an order number, the agent looks it up, and
the email on the order is who they are, once they confirm it. Until then,
nothing from their history is used; a wrong email is told it does not match.

History lives in `memory.sqlite3` next to `orders.json` (gitignored). It is
written when a call ends: on hang-up, and also when the page is refreshed or
closed. Refreshing clears the conversation, not the memory. **Reset memory**
in the left column puts it back to the seeded customers.

To see it work without making a call first:

1. Type or say "Hi, it's about order 44812."
2. When asked, give the email: "r.patel@example.com", or say "r dot patel at
   example dot com".
3. It brings up the call on the 28th, when an update was promised in 2 days.

To see it learn:

1. Give order 44901 and confirm "s.bergstrom@example.com". Say the lamps
   haven't arrived and you need them by Friday.
2. Refresh the page.
3. Give 44901 and the email again. It asks whether this is about the lamps.

The demo is a stand-in for a CRM, the same way `orders.py` stands in for an
order system: swap `memory.py` and nothing above it changes.

### Screen sharing

The screen button (also in the call controls) shares a window instead of the
camera. Frames are sent at up to 1440 px so dialog text stays readable, read
with a screen-specific prompt, and answered one step at a time, naming the
button and where it is. A bar stays at the top of the page while sharing.

What the agent sees: a frame is captured only when you ask something, and
goes to SambaNova's vision model. Nothing is stored. Share a single window
rather than the whole screen, and close anything private first: the model
cannot tell a password from any other text.

## Cards, receipts, handoffs, cost and replay

None of these add model calls of their own, except a short follow-up when an
action is actually carried out.

**Cards on screen.** While the agent talks, what it is talking about appears
under the caption: the order it looked up (status, carrier, last scan, how
late), the policy section it is answering from, the receipt for anything it
just did, and the colleague who just joined. They are built from data the
agent already has.

**It does what it promises.** The model has a `resolve_order` tool for a
replacement, a refund or a returns label, mocked the same way as the order
lookup. It only works on a real order that is not already refunded or
cancelled. It returns a reference such as `RP-44812-7Q2F`, which the agent reads
out and the receipt card shows. Swap `carry_out` in server.py for a real
order system call.

**Specialist handoff.** On "I was charged twice" or "my screen shows an
error", the agent can call `transfer_to_specialist`: it says who it is
handing to, and Maya (billing) or Omar (technical support) speaks next in a
different voice, with the whole conversation. Their voices are the second and
third voices on your Cartesia account, or set `VOICE_BILLING` and
`VOICE_TECHNICAL`. The specialist stays for the rest of the call.

**Cost meter.** Every model call is counted: the reply, fact-checks,
translation, the reasoning model, vision, and the post-call summary, plus the
characters Cartesia speaks. The left column shows this turn and the whole
call. The prices are estimates in `pricing.json`. Check them against your
SambaNova and Cartesia pricing and edit the file. Set
`human_agent_cost_per_call` to a figure you can stand behind to show the
comparison. Drafts that were thrown away are not counted.

**Replay.** Every reply has a Replay button, and "Replay call" plays the whole
call in order, with its transcript line, cards and timeline. Only the agent's
side has audio: the caller's speech was transcribed in the browser and never
recorded.

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
Set `SAMBANOVA_VISION_MODEL` to override — the server warns at startup if it is
pointed at a MiniMax model, since video frames degrade silently rather than
failing.

**Do not pick a MiniMax model as the conversational model for anything
visual.** Given the same photo and the same description from gemma, Llama used
it 3 times out of 3 — "one of the lights on your box is red" — and M3 used it 0
times out of 3, answering "I can't quite see it from this angle, can you hold it
up to the camera" with the description sitting in its context. It discards the
system message carrying what was seen. The model picker labels them.

M3 also confabulated once when the grounding was poor: handed a vague
description and an unhelpful search, it answered "those look like vintage Pyrex
Cinderella bowls". Llama, given the same bad input, said it could not find a
value. For voice-only turns M3 is fine.

MiniMax models also narrate their reasoning inside `<mm:think>` tags. The vision call strips those
from finished text, but a streamed reply goes straight to the speaker, where
"the user wants me to describe the glasses" cannot be taken back. Both streaming
paths now filter the tags as they arrive, holding back any trailing fragment
that could still become one — tags split across deltas as `<mm`, `:thi`, `nk>`.

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

## Looking up orders

The agent used to say "I'm checking on the status" and then invent one. It now
has a `lookup_order` tool over `orders.json`, a stand-in for a CRM. Swap
`look_up` in orders.py for a real API call and nothing above it changes.

Lateness is worked out in the lookup, in working days, rather than left for the
model to derive mid-sentence — the policy documents turn on 2 days for late and
10 for lost, and getting that wrong out loud is worse than not answering.

Order numbers are pulled out of what the caller said, so "my order number is
4 4 9 0 1" works as well as "order 44901". An unknown number is reported as
unknown rather than guessed at.

Four lookups, all answered from the record: carrier and last scan, the actual
delivery date, spoken digits, and a number that does not exist. 4/4.

Where it gets interesting is the two features together. Order 44812 is 11
working days past its promised date, so the lookup flags it past the 10 day
threshold, the documents say an order that late is treated as lost, and the
agent offers a replacement or a refund — then quotes the real refund timing, 5
working days plus 3 to 5 for the bank, from the documents.

## After the call

Hanging up sends the transcript to MiniMax-M3 for a summary, an outcome, the
actions promised, the customer's sentiment and a QA note on how the agent
handled it. M3 is the wrong model for the live turn — it ignores what the
camera saw — but it holds 1.05M tokens and latency does not matter once the
caller has gone, so wrap-up is the job it suits.

It is not a rubber stamp. On a three-turn test call it wrote: "the agent was
polite and empathetic but prematurely promised a delivery charge refund before
verifying the order status."

## Talking over it, properly

Barge-in used to be browser-only: the speaker stopped, but the server kept
generating and synthesising, held one of the two Cartesia slots until it
finished, and recorded the whole reply in history as if it had been heard. The
next answer then built on sentences the caller never got.

Now stopping playback — talking over it, or the Stop button — sends a `cancel`
for that turn. The server cancels the task, which frees the synthesis slot, and
records only what was heard: the browser estimates how far playback got, and
the history entry ends with `[the caller cut you off here]`. A reply that had
finished generating but not finished playing is trimmed the same way.

## Languages, including Arabic

Speech recognition has to be told the language before it hears a word — an
en-US recogniser turns French into English-sounding nonsense, so detecting the
language from its transcript can never work for speech. The **Language** picker
sets both the recogniser and the reply language. On **Auto**, the reply follows
whatever was typed, and the recogniser follows along after.

Arabic is detected by script, replies right to left, and is synthesised with
`CARTESIA_MODEL_AR` (sonic-3 by default), because sonic-2 does not speak it.
Check your Cartesia plan has access to sonic-3 and pick a voice that suits it.

Questions the documents do not cover are only translated for retrieval when
they might not be English. Before, every uncovered English turn — greetings,
general questions — waited on a Llama round trip that could never help it.

## Staying up

A dropped socket used to end the session and take the buffered answers with it.
It now reconnects with backoff — 1s, 2s, 4s, then every 8s — and says plainly
that the agent has lost the thread, because a new socket is a new conversation
server-side. After five failed attempts it reloads instead: a brief network
drop reconnects on the same session, but a restarted server has thrown the
session away and the socket will keep returning 401 for ever.

## Limits

`MAX_TURNS_PER_SESSION` (60) and `MAX_SESSION_MINUTES` (45) bound what one
browser session can spend. The turn limit used to be counted from the trimmed
history, which topped out at four, so it never fired; it is a real counter now. One passphrase shared on a call, and a link that
outlives it, is otherwise somebody else spending your credits.

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
