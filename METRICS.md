# How VoxNova times a turn

Every number on the metrics panel comes from **one server-side stopwatch
per turn**. The browser shows those numbers; it does not time anything
itself. Line numbers are as of the commit that added this file.

## The clock

| | Where |
|---|---|
| Starts | `Turn.__init__`, `self.t0 = time.perf_counter()` ([server.py:837](server.py:837)), created as the first thing `run_turn` does ([server.py:2421](server.py:2421)) |
| Reads | `Turn.now()` = `perf_counter() - t0` ([server.py:860](server.py:860)) |
| Reported | the `first_token`, `text_done` and `first_audio` events carry `t = turn.now()`; the `done` event carries `turn.metrics()` ([server.py:3129](server.py:3129)) |

`run_turn` is started from the socket handler once a `prompt` frame has
arrived and been cleaned up ([server.py:3464](server.py:3464)). So the
clock **does not include**: speech recognition in the browser, the
browser-to-server network, or the transcript repair and option parsing
that run before the turn. It **does include** everything after:
identification, language detection, knowledge-base retrieval, routing,
waiting for a model slot, the model, tool calls, the fact-check gate and
synthesis.

## Time to first token

`turn.first_token`, set once by `mark_first()` ([server.py:2548](server.py:2548), the
assignment at [server.py:2550](server.py:2550)) from whichever of these
happens first:

| Trigger | Where |
|---|---|
| The model's first streamed text of the answer | [server.py:2693](server.py:2693) |
| The model's first sign of a tool call (`tool_start`) | [server.py:2695](server.py:2695) |
| The AI disclosure line, on the **first turn of every call**, before the model has said anything | [server.py:2612](server.py:2612) (the `mark_first` just above it) |
| Starting to look at attachments | [server.py:2645](server.py:2645) |
| The interpreter's first translated text | [server.py:2594](server.py:2594) |

So it is **not always a model token**. On the first turn of a call it is
the moment the fixed disclosure line is emitted, which is almost
immediate. On a turn that starts with a tool call it is the first chunk
of that call, not of any words. The holding phrase said on calls ("Right,
let me see…") does **not** set it ([server.py:2635](server.py:2635)).

Shown in the browser as **First token**, from the event
([static/app.js:1918](static/app.js:1918)).

### SambaNova's own time to first token

Separately, `inference_ttft_s` is SambaNova's `time_to_first_token` from
the usage frame at the end of the stream ([sambanova.py:46](sambanova.py:46),
fields from [sambanova.py:35](sambanova.py:35)). It is inference alone,
measured by SambaNova, with no network in it. It is for the turn's first
model round only: that round's stats dict ([server.py:2224](server.py:2224))
becomes `turn.server` ([server.py:2664](server.py:2664)). Tool follow-ups
keep their own and are not shown.

`network_overhead_s` = our first token minus SambaNova's
([server.py:870](server.py:870)). It is left out when the turn was answered
from an early draft that started before the turn did. On a first turn,
where our first token is the disclosure line, it is meaningless.

## Time to first sound

`turn.first_audio`: the moment the **first audio chunk arrives from
Cartesia at the server** ([server.py:2995](server.py:2995)).

The browser shows it as **First sound**, labelled "From sending the
question to hearing the reply" ([static/app.js:861](static/app.js:861),
value from [static/app.js:2001](static/app.js:2001)). That label overstates
it. The number starts at `t0` on the server, not when the question was
sent. It stops when Cartesia's first chunk reaches the server, not when
the caller hears it: the server-to-browser network and the browser's
audio buffer are not in it.

On a call it can be the holding phrase's audio, not the answer's, because
that phrase is synthesised first. In text-only mode there is no audio and
it stays empty.

Between the two: `turn.tts_start` is when a synthesis slot came free
([server.py:2949](server.py:2949)); `turn.last_token` is when the text
stream finished ([server.py:2931](server.py:2931), shown as **Text
complete** via `text_done`, [static/app.js:1933](static/app.js:1933)).

## Full turn time

`total_s` = `turn.last_audio`, the end of Cartesia's stream at the server
([server.py:3004](server.py:3004)), or `turn.now()` at the moment
`metrics()` is called if there was no audio ([server.py:865](server.py:865)).
That is computed for the `done` event ([server.py:3129](server.py:3129)),
so in text-only mode the full turn time is **t0 to `done`**. It is the
server's time to finish producing the reply, not the time until the caller
has heard it: playback of a long answer goes on after it.

The browser keeps it as `audioDone` ([static/app.js:2008](static/app.js:2008)).
**Synthesis rate** = seconds of audio received ÷ `total_s`
([static/app.js:864](static/app.js:864); the server's own
`realtime_factor` is the same, [server.py:898](server.py:898)).

## Tokens per second

**VoxNova does not count tokens per second.** The `tok/s` on the panel
([static/app.js:872](static/app.js:872)) is SambaNova's own
`completion_tokens_after_first_per_sec`, read from the usage frame of the
turn's first model round ([sambanova.py:46](sambanova.py:46)). That is
SambaNova's server-side generation rate after the first token: its token
count, its clock, no network. The usage frame also has
`completion_tokens_per_sec` and `total_tokens_per_sec`
([sambanova.py:35](sambanova.py:35)), which are kept but not shown.

Other providers do not send these fields, so this number cannot be
compared across providers as it stands. A like-for-like rate needs the
client's own clock: completion tokens divided by the time from the first
streamed token to the last.

## What this means for the provider comparison

- `first_token_s` is a VoxNova pipeline measure, not a model measure:
  retrieval, routing and the disclosure line are in it. For model speed,
  time each request from send to first streamed content on the client,
  which is what a single-prompt kit does with `bench/prompts/`.
- `inference_ttft_s` and `tok/s` are SambaNova's self-reported figures
  and have no equivalent from Fireworks or Together.
- Time to first sound and full turn time include Cartesia and are out of
  scope for a model-only benchmark (see `bench/README.md`).
