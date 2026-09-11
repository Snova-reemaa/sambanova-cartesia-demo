import os

from openai import AsyncOpenAI

DEFAULT_MODEL = "Meta-Llama-3.3-70B-Instruct"


def get_client():
    return AsyncOpenAI(
        api_key=os.environ["SAMBANOVA_API_KEY"],
        base_url=os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1"),
    )


def get_model():
    return os.getenv("SAMBANOVA_MODEL", DEFAULT_MODEL)


async def stream_chat(client, messages, model=None):
    """Stream deltas for a full message list. Reuses a caller-owned client so the
    TLS connection stays warm between turns."""
    stream = await client.chat.completions.create(
        model=model or get_model(),
        messages=messages,
        stream=True,
    )

    async for chunk in stream:
        # SambaNova closes the stream with a usage frame that carries no choices.
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


async def stream_reply(prompt, system_prompt="Answer clearly and concisely."):
    client = get_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    async for delta in stream_chat(client, messages):
        yield delta
