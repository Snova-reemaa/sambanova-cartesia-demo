# How VoxNova times a turn

Every number on the metrics panel comes from **one server-side stopwatch
per turn**. The browser shows those numbers; it does not time anything
itself. Line numbers are as of the commit that last changed this file.

## The clock

| | Where |
|---|---|
| Starts | `Turn.__init__`, `self.t0 = time.perf_counter()` ([server.py:837](server.py:837)), created as the first thing `run_turn` does ([server.py:2430](server.py:2430)) |
| Reads | `Turn.now()` = `perf_counter() - t0` ([server.py:860](server.py:860)) |
| Reported | the `first_token`, `text_done` and `first_audio` events carry `t = turn.now()`; the `done` event carries `turn.metrics()` ([server.py:3140](server.py:3140)) |

`run_turn` is started from the socket handler once a `prompt` frame has
arrived and been cleaned up ([server.py:3475](server.py:3475)). So the
clock **does not include**: speech recognition in the browser, the
browser-to-server network, or the transcript repair and option parsing
that run before the turn. It **does include** everything after:
identification, language detection, knowledge-base retrieval, routing,
waiting for a model slot, the model, tool calls, the fact-check gate and
synthesis.

## Time to first token

`turn.first_token` is **the model's first token**. It is set once, by
`mark_first()` ([server.py:2557](server.py:2557), the assignment at
[server.py:2559](server.py:2559)), from whichever of these comes first, and
all three are model output:

| Trigger | Where |
|---|---|
| The first streamed text of the answer | [server.py:2704](server.py:2704) |
| The first piece of a tool call (`tool_start`), when the model's first move is to call one | [server.py:2707](server.py:2707) |
| The interpreter's first translated text | [server.py:2603](server.py:2603) |

Fixed text said before the model has produced anything does **not** set
it:

- the AI disclosure line at the start of every call ([server.py:2620](server.py:2620));
- the "let me have a look" bridge before reading attachments ([server.py:2655](server.py:2655));
- the holding phrase said on calls ("Right, let me see…", [server.py:2645](server.py:2645));
- the fixed read-back sentences that confirm a name or email (`confirming.py`): those turns make no model call and have no first token.

On a turn that starts with a tool call, the first token is the start of
that call, not of any words: the words come in a second model round,
after the tool has run.

Shown in the browser as **First token**, from the event
([static/app.js:1924](static/app.js:1924)).

### SambaNova's own time to first token

Separately, `inference_ttft_s` is SambaNova's `time_to_first_token` from
the usage frame at the end of the stream ([sambanova.py:47](sambanova.py:47),
fields from [sambanova.py:36](sambanova.py:36)). It is inference alone,
measured by SambaNova, with no network in it. It is for the turn's first
model round only: that round's stats dict ([server.py:2233](server.py:2233))
becomes `turn.server` ([server.py:2675](server.py:2675)). Tool follow-ups
keep their own and are not shown.

`network_overhead_s` = our first token minus SambaNova's
([server.py:870](server.py:870)). It is left out when the turn was answered
from an early draft that started before the turn did.

## Time to first sound

`turn.first_audio`: the moment the **first audio chunk arrives from
Cartesia at the server** ([server.py:3006](server.py:3006)).

The browser shows it as **First sound**, labelled "Server: turn start to
Cartesia's first audio" ([static/index.html:45](static/index.html:45) and
[static/app.js:864](static/app.js:864); value from
[static/app.js:2007](static/app.js:2007)). It is not the time until the
caller hears it: the server-to-browser network and the browser's audio
buffer are not in it.

On a call it can be the holding phrase's audio, not the answer's, because
that phrase is synthesised first. On the first turn of a call it can be
the disclosure line's. In text-only mode there is no audio and it stays
empty.

Between the two: `turn.tts_start` is when a synthesis slot came free
([server.py:2960](server.py:2960)); `turn.last_token` is when the text
stream finished ([server.py:2942](server.py:2942), shown as **Text
complete** via `text_done`, [static/app.js:1939](static/app.js:1939)).

## Full turn time

`total_s` = `turn.last_audio`, the end of Cartesia's stream at the server
([server.py:3015](server.py:3015)), or `turn.now()` at the moment
`metrics()` is called if there was no audio ([server.py:865](server.py:865)).
That is computed for the `done` event ([server.py:3140](server.py:3140)),
so in text-only mode the full turn time is **t0 to `done`**. It is the
server's time to finish producing the reply, not the time until the caller
has heard it: playback of a long answer goes on after it.

The browser keeps it as `audioDone` ([static/app.js:2014](static/app.js:2014)).
**Synthesis rate** = seconds of audio received ÷ `total_s`
([static/app.js:867](static/app.js:867); the server's own
`realtime_factor` is the same, [server.py:907](server.py:907)).

## Tokens per second

**Timed by us, from the stream, the same way for any provider**
(`StreamClock`, [sambanova.py:57](sambanova.py:57)):

    tokens_per_sec = output tokens / (time of last streamed token − time of first)

- **The clock** is this machine's `perf_counter`, ticked on every chunk
  that carries model output, text or a tool-call piece
  ([sambanova.py:75](sambanova.py:75)). The span runs from the first such
  chunk to the last. The closing usage frame is not output and does not
  stretch it.
- **The output tokens** are the provider's own `completion_tokens` from
  its usage frame, read from any chunk that carries one, with or without
  choices, because providers differ on where they put it
  ([sambanova.py:217](sambanova.py:217), [sambanova.py:272](sambanova.py:272)).
  A stream with no usage at all falls back to characters ÷ 4 and is
  labelled `estimate` instead of `usage`.
- **Computed** at the end of the stream ([sambanova.py:82](sambanova.py:82),
  called at [sambanova.py:243](sambanova.py:243) and
  [sambanova.py:283](sambanova.py:283)), into the same stats dict as
  SambaNova's own fields.
- **Reported** in the `done` event's metrics as `tokens_per_sec`, with
  `tokens_per_sec_from`, `output_tokens` and `stream_seconds`
  ([server.py:896](server.py:896)). Like everything in `turn.server`, it is
  for the turn's first model round.

By this definition the first token is in the count but not in the time,
so a very short reply reads slightly high. On a turn whose first round is
only a tool call, it is the rate of producing that call.

**SambaNova's own figure** is kept beside it for reference, never mixed
in: `sambanova_tokens_per_sec` in the metrics, SambaNova's
`completion_tokens_after_first_per_sec`, its token count on its clock with
no network. The panel shows ours first and SambaNova's in brackets:
"`312 tok/s (SambaNova says 330)`" ([static/app.js:882](static/app.js:882),
value from [static/app.js:2019](static/app.js:2019)).

## The side-by-side comparison (`/compare`)

The comparison page does not use the turn clock above. It has its own
start line, in `compare.race` ([compare.py](compare.py)):

- **Before the clock:** each side computes the SDK's headers and opens its
  connection with a free models list (`warm`). Measured on loopback, the
  SDK's first-use set-up alone is about 0.27 s; a cold connection also
  pays for TCP and TLS. Neither is the model, so neither is timed.
- **t0:** taken once both sides are lined up; both are released together,
  and each records how long after t0 its request left (`sent_after`,
  a few milliseconds).
- **Time to first token:** t0 to the first streamed token, reasoning
  included where a model streams it separately (`StreamClock.tick_reasoning`).
- **Tokens per second:** the same `StreamClock` figure as above, from the
  same `stream_chat` code, for every provider.
- **Full reply time:** t0 to the end of the stream.
- **No retries** (`max_retries=0`): a 429 is shown as a rate limit, not
  hidden inside a slow time. Rate limits, refused keys, missing models,
  timeouts, empty replies and replies that are only a tool call are each
  shown as what they were, with no number.
- **Five runs:** median and worst per side, and how many of the five got
  through (`compare.summarise`).

## What this means for the provider comparison

- `first_token_s` now starts at the model's first token, but the turn
  clock it is measured on still includes VoxNova's pipeline: retrieval,
  routing, waiting for a slot. For model speed alone, time each request
  from send to first streamed content on the client, which is what a
  single-prompt kit does with `bench/prompts/core/`.
- `tokens_per_sec` is comparable across providers: same clock, same
  definition, each provider's own token count.
- `inference_ttft_s` and `sambanova_tokens_per_sec` are SambaNova's
  self-reported figures and have no equivalent from Fireworks or Together.
- Time to first sound and full turn time include Cartesia and are out of
  scope for a model-only benchmark (see `bench/README.md`).
