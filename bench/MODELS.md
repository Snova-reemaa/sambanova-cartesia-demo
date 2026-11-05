# Serverless models, 2026-11-05

Made by `bench/models.py`, which only lists models (free).

## sambanova (4)

- `Meta-Llama-3.3-70B-Instruct`
- `MiniMax-M3.1-Flash`
- `gemma-4-31B-it`
- `gpt-oss-120b`

## fireworks (17)

- `accounts/fireworks/models/deepseek-v4p1-flash`
- `accounts/fireworks/models/deepseek-v4p1-flash-1017`
- `accounts/fireworks/models/ember-1`
- `accounts/fireworks/models/glm-5p3`
- `accounts/fireworks/models/glm-5p3-flash`
- `accounts/fireworks/models/gpt-oss-120b`
- `accounts/fireworks/models/inkling`
- `accounts/fireworks/models/kimi-k3`
- `accounts/fireworks/models/kimi-k3p1-pro`
- `accounts/fireworks/models/minimax-m3`
- `accounts/fireworks/models/minimax-m3p1-flash`
- `accounts/fireworks/models/muse-spark-open`
- `accounts/fireworks/models/nemotron-3-ultra-nvfp4`
- `accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b`
- `accounts/fireworks/models/qwen3p8-2p4t-a95b`
- `accounts/fireworks/models/qwen3p8-max`
- `accounts/fireworks/models/rune`

## together

Not listed: HTTP 401 (key refused).

## novita

Not listed: no NOVITA_API_KEY in .env.

## Shared

| Model | sambanova | fireworks |
|---|---|---|
| gpt-oss-120b | `gpt-oss-120b` | `accounts/fireworks/models/gpt-oss-120b` |
| minimax-m3.1-flash | `MiniMax-M3.1-Flash` | `accounts/fireworks/models/minimax-m3p1-flash` |

All three share: can't tell yet (together: HTTP 401 (key refused), novita: no NOVITA_API_KEY in .env)
