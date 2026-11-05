"""Record every LLM request VoxNova makes, exactly as sent.

Loaded only when BENCH_LOG names a file (sambanova.get_client attaches
it), so nothing in a normal run, or in the image, ever touches this.

It wraps the client's chat.completions.create: the keyword arguments
handed to the SDK are the request (model, messages, tools, temperature,
stream), and they are written down as they are, before the call. The
answer is written beside them, streamed or not: the text and any tool
calls, so a request's tool calls can be matched to the tool results in
the next one.

One JSON object per line. "caller" is the VoxNova function that made the
call, read off the stack, so a request can be told apart from a
fact-check or a call write-up without touching any call site.
"""

import inspect
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP = ("recorder.py", "sambanova.py", f"{os.sep}openai{os.sep}",
        f"{os.sep}asyncio{os.sep}", f"{os.sep}httpx{os.sep}")


def _caller():
    for frame in inspect.stack()[1:]:
        if not any(s in frame.filename for s in SKIP):
            return f"{os.path.basename(frame.filename)}:{frame.function}:{frame.lineno}"
    return "?"


def _plain(value):
    """Messages and tools are plain already; anything else as the SDK would."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


class _Tee:
    """Hands the stream on untouched and keeps a copy of what came back."""

    def __init__(self, stream, record, write):
        self._stream, self._record, self._write = stream, record, write
        self._text, self._calls, self._usage = [], {}, None

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            self._finish("complete")
            raise
        except BaseException:
            self._finish("interrupted")
            raise
        if getattr(chunk, "usage", None):
            self._usage = _plain(chunk.usage)
        for choice in chunk.choices or []:
            delta = choice.delta
            if getattr(delta, "content", None):
                self._text.append(delta.content)
            for part in getattr(delta, "tool_calls", None) or []:
                slot = self._calls.setdefault(part.index or 0, {"name": "", "arguments": ""})
                if part.function and part.function.name:
                    slot["name"] += part.function.name
                if part.function and part.function.arguments:
                    slot["arguments"] += part.function.arguments
        return chunk

    def _finish(self, how):
        if self._record.get("response") is not None:
            return
        self._record["response"] = {
            "how": how, "content": "".join(self._text),
            "tool_calls": list(self._calls.values()), "usage": self._usage,
        }
        self._record["finished"] = time.time()
        self._write(self._record)

    async def close(self):
        await self._stream.close()


def attach(client, path):
    """Record every chat completion this client makes into `path`."""
    completions = client.chat.completions
    original = completions.create

    def write(record):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def create(*args, **kwargs):
        # Who asked is read now, when create() is called, not when the call
        # runs: a fact-check is handed to asyncio.wait_for, which runs it in
        # a task of its own whose stack no longer leads back to the gate.
        return send(_caller(), args, kwargs)

    async def send(caller, args, kwargs):
        record = {"started": time.time(), "caller": caller,
                  "request": {k: _plain(v) for k, v in kwargs.items()},
                  "response": None}
        try:
            result = await original(*args, **kwargs)
        except BaseException as exc:
            record["response"] = {"how": "failed", "error": type(exc).__name__}
            record["finished"] = time.time()
            write(record)
            raise
        if kwargs.get("stream"):
            return _Tee(result, record, write)
        message = result.choices[0].message if result.choices else None
        record["response"] = {
            "how": "complete",
            "content": getattr(message, "content", None),
            "tool_calls": [_plain(t) for t in (getattr(message, "tool_calls", None) or [])],
            "usage": _plain(getattr(result, "usage", None)),
        }
        record["finished"] = time.time()
        write(record)
        return result

    completions.create = create
    return client
