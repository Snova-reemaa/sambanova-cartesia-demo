# gpt-oss-120b on SambaNova: a tool-call fragment in streamed `content`

Captured 5 November 2026, 01:56 (local time). The request and both full responses are
saved beside this note: `findings/gpt-oss-leak-request.json` and
`findings/gpt-oss-leak-responses.json`.

## In one paragraph

Streamed from SambaNova, `gpt-oss-120b` puts its attempted tool call into the standard
`content` field: `"lookup_order 44812"`, labelled only by a non-standard
`"channel": "commentary"` field that OpenAI-compatible clients do not read. Any client
that streams and shows `content` (VoxNova, the OpenAI SDK's usual loop) shows it to the
user, and in a voice agent it is spoken. The same request **not** streamed comes back
clean. Fireworks, same model, same request, shows nothing of the kind in either mode.

## The request

Sent to both providers, identical except for the model id:

| | |
|---|---|
| SambaNova model | `gpt-oss-120b` (`https://api.sambanova.ai/v1`) |
| Fireworks model | `accounts/fireworks/models/gpt-oss-120b` |
| `temperature` | 0.3 |
| `max_tokens` | 1500 |
| `reasoning_effort` | `"low"` |
| `tools` | none: no tools are offered |
| `stream` | `true`, then again `false` |

Messages: five, in this order (full text in the request file):

1. `system` (1,978 chars): VoxNova's support-agent prompt. It tells the agent how to
   look orders up, which is why the model reaches for a tool.
2. `user`: `Where is my order 44812, and what can you do if it's late?`
3. `system` (454 chars): the order record, as a lookup would have returned it.
4. `system` (2,001 chars): the matching policy documentation.
5. `system` (215 chars): *"No tools are available on this turn and none are needed …
   Never write a tool call, a function name or JSON."*

So the model is told, in the last message, not to call a tool, and has no tool to call.

## What SambaNova sends, streamed

The first deltas, exactly as received (`role` dropped):

```json
{"channel": "analysis", "reasoning": "We need to lookup order."}
{"content": "lookup_order 44812", "channel": "commentary"}
{"content": "Ah, I see. Your order 44812 is marked as late. I can either send you a replacement right away or process a full refund. Which option would you"}
{"content": " prefer?"}
```

What a standard client reads as the reply (`content` joined):

> **lookup_order 44812**Ah, I see. Your order 44812 is marked as late. I can either send
> you a replacement right away or process a full refund. Which option would you prefer?

- The fragment is in `content`, the field every OpenAI-compatible client treats as the
  answer. Only `"channel": "commentary"` marks it as something else, and that field is not
  part of the OpenAI API.
- It is not returned as `tool_calls`.
- `finish_reason` is `stop`.

## What SambaNova sends, not streamed

Same request with `stream: false`: `content` starts *"Ah, I see. Your order 44812 is
marked as delayed and is currently considered lost…"*, with no fragment and no
`tool_calls`. Reasoning comes back separately (`"We need to lookup order."`, 7 reasoning
tokens of 81). So the non-streamed path already drops the commentary channel; the
streamed path does not.

## What Fireworks sends

Same request, same model. Streamed, reasoning arrives in its own field,
`reasoning_content`, and `content` holds only the answer: *"Ah, I see it's been quite a
while. Your headphones are marked as in transit…"*. Not streamed, the same. No fragment
in either.

## How often

In the five-run comparison just before this capture (same request), **5 of 5** streamed
SambaNova replies began with `lookup_order 44812`; **0 of 5** Fireworks replies did.

With `reasoning_effort` left at its default, and a 400-token cap, it was worse: SambaNova
often returned **only** the tool call as text (`{"order_number":"44812"}`) or an empty
answer, while Fireworks answered in words.

## Suggested fix (for the serving team)

On the streamed path, do what the non-streamed path already does: keep the `commentary`
channel out of `content`. Either drop it, or, if it is a tool call, return it as
`tool_calls`, as OpenAI-compatible clients expect. Reasoning (`analysis`) is already sent
apart from `content`, under `reasoning`.

## What VoxNova does meanwhile

The comparison page shows SambaNova's output exactly as sent, fragment included: hiding a
provider's output would make the comparison dishonest. (A client that reads SambaNova's
`channel` field could filter it out, but that is SambaNova-specific and would not help
any standard client.)

## How to reproduce

```bash
venv/bin/python - <<'PY'
import json, os
from openai import OpenAI
req = json.load(open("bench/findings/gpt-oss-leak-request.json"))
body = {k: req[k] for k in ("messages", "temperature", "max_tokens", "reasoning_effort")}
c = OpenAI(api_key=os.environ["SAMBANOVA_API_KEY"], base_url="https://api.sambanova.ai/v1")
for chunk in c.chat.completions.create(model="gpt-oss-120b", stream=True, **body):
    if chunk.choices:
        d = chunk.choices[0].delta.model_dump()
        print({k: v for k, v in d.items() if v})
PY
```
