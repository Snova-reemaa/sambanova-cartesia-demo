# Benchmark prompts

Prompts for a speed comparison of SambaNova, Fireworks and Together, taken
from VoxNova itself: every LLM request it made while ten scripted support
calls were played through it, flattened into single prompts for Rodrigo's
benchmarking kit.

**These are a proxy for the real calls.** They measure model generation
only. Voice is excluded: there is no speech recognition, no Cartesia
synthesis, and nothing about the time to first sound. They are the
requests VoxNova really sends, with the history, documentation and tool
definitions it really sends, but stripped of the roles and settings that a
single-prompt benchmark cannot take (see "What flattening loses").

They were recorded on 4 November 2026 against SambaNova
(`Meta-Llama-3.3-70B-Instruct`; `MiniMax-M3` for the one call write-up),
with VoxNova at `4565a21` plus the recorder hook in `sambanova.py`. Fireworks and Together have
not been called.

## The files

| Path | What it is |
|---|---|
| `scenarios/*.json` | The ten scripted calls: what the caller says each turn |
| **`prompts/core/`** | **The main comparison set**: the 33 `answer` and `answer-after-tools` prompts, with their own `index.csv` / `index.json` |
| `prompts/fact-check/` | The extra set: the 51 `fact-check` prompts, with their own index |
| `prompts/NNN-<scenario>-t<turn>-<kind>.txt` | All 93 requests, flattened, one each (the two sets above are copies of some of these) |
| `prompts/index.csv`, `prompts/index.json` | What each of the 93 is (below) |
| `raw/requests.jsonl` | Each request exactly as handed to the SDK, with the model's reply beside it: text, tool calls, SambaNova's usage frame |
| `raw/turns.jsonl` | When each turn was played and what came back, which places each request in its turn |
| `recorder.py`, `run.py`, `export.py` | How they were made |

## The scenarios

| | Scenario | Turns | Covers |
|---|---|---|---|
| 01 | order-lookup | 3 | where an order is, its dates, carrier and tracking |
| 02 | refund-replacement | 3 | a faulty item: replacement or refund, refund time |
| 03 | policy | 2 | return window, opened headphones |
| 04 | uncovered | 2 | gift wrapping, price matching: should decline |
| 05 | handoff | 3 | a double charge, moved to Maya in billing |
| 06 | language-switch | 3 | English, then Spanish mid-call |
| 07 | returning-caller | 4 | Riya Patel: email read back, code, "what did we talk about last time" |
| 08 | frustrated-caller | 4 | an escalating caller, order stuck in "preparing" |
| 09 | reasoning | 2 | partial return arithmetic, a fault at 40 days |
| 10 | tool-heavy | 2 | two order lookups in one turn, then a web search |

All of them use the demo data: the eight orders in `orders.json` and the
seeded customers. In 07, turns 1 and 2 are VoxNova's code-driven email
read-back, which makes no model call, so they have no prompts.

## The prompt format

Each `.txt` file is **one line of JSON**, the request's messages and tools
exactly as sent:

```json
{"messages":[{"role":"system","content":"You are a support agent on a phone call…"},{"role":"user","content":"Hi, where is my order 44812?"},{"role":"system","content":"Company documentation relevant to what they asked: …"}],"tools":[{"type":"function","function":{"name":"lookup_order",…}}]}
```

- `messages` is the whole conversation so far, every time: each turn's
  request carries every earlier turn (`export.py` checks this and it holds
  for all of them).
- `tools` is the tool definitions offered on that request, or `[]`.
- Tool **results** are in the messages as `system` messages ("Order lookup
  result…", "Web search results…"). That is how VoxNova feeds them back:
  it does not use `role: "tool"` messages, so there are none.
- Tool **calls** the model made are in `raw/requests.jsonl`, under each
  request's `response.tool_calls` (7 of them: `lookup_order` 5,
  `transfer_to_specialist` 1, `web_search` 1).

## The index

One row per prompt file:

| Column | |
|---|---|
| `file` | the prompt file |
| `scenario`, `turn` | which call and which turn (`hang-up` for the write-up after the call) |
| `kind` | what the request was for (below) |
| `caller` | the VoxNova function that made it, `file:function:line` |
| `model`, `stream` | as sent |
| `messages`, `tools` | how many of each |
| `chars` | length of the flattened prompt |
| `approx_input_tokens` | `chars / 4`: an estimate, not a count |
| `sambanova_prompt_tokens` | SambaNova's own count for the original chat request, from its usage frame |

The two token columns differ on purpose: the flattened prompt carries JSON
punctuation the chat request does not, and SambaNova's count includes its
chat template.

## Kinds of request

A single caller turn makes several requests, and they are not alike. Pick
the kinds that match what you want to compare.

| Kind | Count | What it is | Sent with |
|---|---|---|---|
| `answer` | 27 | the turn's first model call: system prompt, history, documentation, tools offered | streamed; temperature 0.3 or default |
| `answer-after-tools` | 6 | the same turn again, with the tool result added | streamed |
| `answer-retry` | 6 | said nothing after its tools, or a sentence the fact-check held back rewritten | streamed |
| `fact-check` | 51 | one sentence checked against the sources before it is spoken | not streamed; temperature 0; `max_tokens` 30 |
| `translate` | 2 | a non-English question put into English for retrieval | not streamed; `max_tokens` 120 |
| `write-up` | 1 | the call summarised after hang-up | `MiniMax-M3`; not streamed; `max_tokens` 400 |

**Send `prompts/core/` for the main comparison.** It is what a caller
waits on: the answer, and the answer again once a tool has run. About
1,000–2,430 input tokens each.

**`prompts/fact-check/` goes as a separate, extra set.** The fact-checks
run alongside the answer, sentence by sentence, and are small (about
590–1,610 tokens in, 30 out) and alike, so mixed into the main set they
would outnumber and skew it.

The other kinds (`answer-retry`, `translate`, `write-up`) are in the full
set only.

## What flattening loses

A single prompt with no roles cannot carry:

- **The roles themselves.** They are inside the JSON as text, so the
  model reads `"role":"system"` as words, not as a system turn. A provider
  will tokenise and attend to it differently from the real chat request.
- **Tools as tools.** The definitions are text in the prompt; the model
  cannot actually call one. Answers that would have called a tool will
  carry on in prose instead.
- **Settings.** Model, temperature and `max_tokens` are in the index and
  in `raw/`, not in the prompt; set them in the kit.

If the kit can ever take a chat request, `raw/requests.jsonl` holds every
request as it was, ready to replay.

## Making them again

```bash
venv/bin/python bench/run.py          # plays all ten against SambaNova
venv/bin/python bench/export.py       # writes prompts/ and the index
```

`run.py` starts VoxNova from this checkout on a free port, text only, with
the demo inbox on and its own fresh copy of the demo data, and sets
`BENCH_LOG`, which is the only thing that switches the recorder on
(`sambanova.get_client`). It spends SambaNova tokens only, plus one web
search in scenario 10. Model replies differ run to run, so the later
turns' prompts will too.
