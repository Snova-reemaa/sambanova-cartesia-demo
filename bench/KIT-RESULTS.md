# SambaNova vs Fireworks on gpt-oss-120b, measured with the benchmarking kit

Run 5 November 2026 with `sambanova/ai-starter-kit` (commit `8f58380`), cloned
outside this repo at `~/Desktop/ai-starter-kit`. The kit needed a local patch to
measure Fireworks at all (see [Bugs found in the kit](#bugs-found-in-the-kit));
the patch is saved as `findings/kit-local-patch.diff`.

## Settings (identical on both)

| | |
|---|---|
| Prompts | `prompts/core.jsonl`: 33 prompts, about 960–2,230 input tokens |
| Model | `gpt-oss-120b` / `accounts/fireworks/models/gpt-oss-120b` |
| `max_tokens` | 1500 (no reply reached it) |
| `reasoning_effort` | `low` |
| `temperature` | 0.3 |
| Concurrency | 1 request at a time, 1 warm-up request first |
| Command | `evaluator.py --mode custom --llm-api sncloud --num-concurrent-requests 1 --num-warmup-requests 1 --sampling-params '{"max_tokens_to_generate":1500,"reasoning_effort":"low","temperature":0.3}'` |

The kit sends each prompt as one user message: roles and tools are text inside
it (see `README.md`, "What flattening loses").

## Results

33 of 33 completed on each provider. Replies were the same length on both
(median 75 vs 76 output tokens, of which about 26–29 reasoning).

| | SambaNova median | SambaNova p90 | Fireworks median | Fireworks p90 |
|---|---|---|---|---|
| First token, client-side | 1.10 s | 3.42 s | **0.78 s** | **0.83 s** |
| End-to-end latency, client-side | 1.21 s | 3.42 s | **1.09 s** | **1.39 s** |
| Tokens/sec, provider's own figure | **622** | **699** | not reported | — |
| Tokens/sec, client-side | 695 * | unreliable * | 223 | 301 |
| First token, provider's own figure | 0.28 s | 2.46 s | not reported | — |

\* SambaNova streams each reply in about 3 large chunks (Fireworks: about 57), so
the time between first and last chunk is often near zero (under 50 ms on 6 of 33
requests) and the kit's client-side tokens/sec explodes (max 221,056). The median
roughly agrees with SambaNova's own figure; the p90 is meaningless. Use
SambaNova's server figure.

### Reading it

- **Generation speed:** SambaNova is about 2.8× Fireworks (622 vs 223 tok/s,
  median). But with ~75-token replies, generation is a small part of the wait.
- **First token:** Fireworks was faster and far steadier. SambaNova's own
  server-side first token had a p90 of 2.46 s and a maximum of 5.65 s in this run,
  so the time went inside SambaNova, not on the network. An earlier run of the
  same prompts (unpatched kit, same settings) had SambaNova at 0.85 s median /
  1.51 s p90, so this varies between runs; one run of 33 is not enough to call it.
- **End to end:** close at the median (1.21 vs 1.09 s); Fireworks better at p90
  because of SambaNova's slow first tokens.

## Three runs: how much it varies

Same patched kit and settings, three runs per provider: run 1 at 11:21–11:27,
run 2 at 11:30–11:32, then a 30-minute gap, run 3 at 12:03–12:04 (Fireworks
first, to swap the order). 99 of 99 requests completed on each provider.

| | Run | First token p50 | First token p90 | End to end p50 | End to end p90 | Tok/s p50 | Tok/s p90 |
|---|---|---|---|---|---|---|---|
| **SambaNova** | 1 | 1.10 s | 3.37 s | 1.21 s | 3.40 s | 622 | 699 |
| | 2 | 0.87 s | 2.45 s | 1.01 s | 2.53 s | 630 | 700 |
| | 3 | 1.02 s | 1.75 s | 1.15 s | 2.19 s | 593 | 692 |
| | **All 99** | **0.98 s** | **2.52 s** | **1.12 s** | **2.83 s** | **623** | **700** |
| **Fireworks** | 1 | 0.78 s | 0.83 s | 1.09 s | 1.37 s | 223 | 296 |
| | 2 | 0.83 s | 0.91 s | 1.16 s | 1.58 s | 244 | 286 |
| | 3 | 0.90 s | 0.96 s | 1.22 s | 1.44 s | 246 | 292 |
| | **All 99** | **0.83 s** | **0.95 s** | **1.19 s** | **1.47 s** | **239** | **294** |

First token and end to end are client-side. Tokens/sec is SambaNova's own server
figure and Fireworks' client-side figure (Fireworks reports no server figure, and
SambaNova's client-side one is unreliable; see above). p90 is interpolated.

- **Tokens/sec is stable** on both: SambaNova about 2.6× Fireworks in every run.
- **Medians are stable**: SambaNova's end-to-end median is slightly better, Fireworks'
  first-token median slightly better.
- **SambaNova's p90 swings** (first token 1.75–3.37 s across runs); Fireworks'
  stays within 0.83–0.96 s.

## SambaNova's slow first tokens: which prompts?

Random, not particular prompts. Checked across the three runs above plus the
earlier unpatched run (same prompts and settings), using SambaNova's own
server-side first-token time:

- **No prompt was slow in every run.** The slowest 4 of 33 (the p90) were 14
  different prompts across the four runs. Only #16
  (`045-05-handoff-t3-answer`) appeared three times, and twice only just (0.43 s
  and 0.61 s, against medians of 0.08–0.09 s).
- **Not input length.** Rank correlation between input tokens and first token is
  −0.06 to +0.12 in every run; the four longest inputs (#22, #27, #32, #33) were
  slow once between them.
- **Runs don't agree with each other.** Rank correlation of first-token times
  between any two runs is −0.12 to +0.14, i.e. no relationship.
- **Spread over each run, not one burst**: in run 1 the spikes were at :54, :00,
  :11 and :33 past the minute.
- **The spike is inside SambaNova**: server-side first token reached 5.65 s
  (run 1), 4.33 s (run 2) and 1.88 s (run 3) while the typical value was 0.08 s.
  Five requests in each of runs 1 and 2 took over 1 s; two in run 3; none in the
  unpatched run. That looks like occasional queueing or contention on the
  endpoint, varying with the time of the run, not with the request.
- SambaNova reports no cached-token counts here, so prefix caching can't be
  checked from these results.

**A separate, steady gap.** On every SambaNova request, the first text reached
the client about 0.8 s (median 0.76–0.88 s per run) after SambaNova's own
first-token time. Fireworks' whole client-side first token is 0.83 s, so this gap
is most of SambaNova's client-side first token. It covers network, connection
set-up (the kit opens a new connection per request, on both providers) and
anything between SambaNova's model and the client. Worth asking SambaNova about:
their model starts in ~0.08 s, but a client sees text after ~1 s.

## Spend

Estimated from the token counts in the result files, plus one warm-up request
per run, at list prices from third-party trackers (SambaNova gpt-oss-120b
$0.22 / $0.59 per million in / out; Fireworks $0.15 / $0.60; SambaNova Llama 3.3
70B $0.60 / $1.20):

| | Input tokens | Output tokens | Cost |
|---|---|---|---|
| SambaNova gpt-oss-120b, 4 runs | ~206k | ~10.6k | ~$0.05 |
| Fireworks gpt-oss-120b, 4 runs | ~206k | ~58k | ~$0.07 |
| SambaNova Llama 3.3 70B, kit example | ~7k | ~10.5k | ~$0.02 |
| **Total** | | | **~$0.14** |

Most of the Fireworks output (48k tokens) is the first, unpatched run, where
`ignore_eos` made every reply run to 1,500 tokens. The two failed 401 runs and
the few one-request checks cost nothing or under a cent.

## Result files

Under `~/Desktop/ai-starter-kit/benchmarking/data/results/`, each run a
`…_summary.json` and a `…_individual_responses.json`:

| Folder | What |
|---|---|
| (top level) | The kit's own example, Meta-Llama-3.3-70B-Instruct, 100 prompts, 16 at a time, unpatched |
| `core-sambanova/` | core.jsonl, SambaNova, unpatched kit |
| `core-fireworks/` | core.jsonl, Fireworks, unpatched kit: **not valid** (bug 1) |
| `core-sambanova-patched/` | core.jsonl, SambaNova, patched: the table above |
| `core-fireworks-patched/` | core.jsonl, Fireworks, patched: the table above (run 1) |
| `core-sambanova-run2/`, `core-sambanova-run3/` | core.jsonl, SambaNova, patched: runs 2 and 3 |
| `core-fireworks-run2/`, `core-fireworks-run3/` | core.jsonl, Fireworks, patched: runs 2 and 3 |

Runs 2 and 3 were started by a small wrapper that reads the key from VoxNova's
`.env` into the process only and turns off the kit's own `.env` loading, so the
kit's `.env` did not need swapping. Same evaluator, same flags.

## Bugs found in the kit

In `benchmarking_tools/kit/src/llmperf/sambanova_client.py`. These are worth
sending to the kit's maintainers.

1. **Fireworks streams are discarded.** Text is read only from chunks whose
   `usage` is null. Fireworks puts `usage` on every chunk, so the kit reads no
   text at all: first token is recorded when the stream ends (equal to
   end-to-end), and tokens/sec cannot be computed. Fix: read `usage` and the
   delta independently.
2. **`reasoning_content` is not read.** Only `content` and `reasoning` count.
   Fireworks (and others) send reasoning as `reasoning_content`, so their first
   token would be timed at the first answer word, not the first reasoning token.
3. **`ignore_eos: true` is always sent.** Fireworks honours it, SambaNova does
   not: on the unpatched run Fireworks wrote 1,500 tokens per reply and SambaNova
   about 70, so nothing was comparable. It should be optional.
4. **Empty deltas count as tokens.** The check is `is not None`, so a chunk with
   `content: ""` counts as a token event and can start the first-token clock
   early. The patch counts only non-empty text.
5. **Client-side tokens/sec breaks on chunky streams** (see the note under the
   table). Not patched; worth reporting, or falling back to tokens ÷ end-to-end
   time when the stream arrives in a few chunks.

The kit's install also fails on macOS as written: `vllm` ships Linux-only
packages. The kit's own evaluator doesn't need it (or `aiperf`); installing the
rest works.
