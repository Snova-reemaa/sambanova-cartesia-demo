"""The side-by-side comparison, against fake providers with known timings.

Each fake speaks the OpenAI streaming format over real HTTP, so the real
client and the real timing code are what is tested: first token after a
set delay, then tokens at a set pace, then a usage frame. Others answer
429, 401 or 500, or never answer at all.

Checked: both requests leave at the same moment; the numbers come out as
planted; both sides get the same messages and settings; every failure is
shown as what it was, with no number and no quiet retry; five runs give
the median and the worst; and the app's own client is unchanged.

Offline: everything is on loopback.
"""

import asyncio
import json
import os
import sys
import time

from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import free_port                            # noqa: E402


class Fake:
    """One provider. `plan` says how the next request is answered."""

    def __init__(self, first=0.2, gap=0.03, tokens=10, status=200, hang=False,
                 usage_in_last=False, words=None, reasoning=False):
        self.port = free_port()
        self.plans = []
        self.default = dict(first=first, gap=gap, tokens=tokens, status=status,
                            hang=hang, usage_in_last=usage_in_last, words=words,
                            reasoning=reasoning)
        self.arrivals, self.bodies = [], []

    def plan(self, **kw):
        self.plans.append({**self.default, **kw})

    async def chat(self, request):
        self.arrivals.append(time.perf_counter())
        body = await request.json()
        self.bodies.append(body)
        p = self.plans.pop(0) if self.plans else self.default
        if p["hang"]:
            await asyncio.sleep(30)
            return web.Response(status=504)
        if p["status"] != 200:
            return web.json_response({"error": {"message": f"fake {p['status']}"}},
                                     status=p["status"])
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)

        def chunk(delta=None, usage=None):
            data = {"id": "x", "object": "chat.completion.chunk", "created": 0,
                    "model": body["model"],
                    "choices": [] if delta is None else
                    [{"index": 0, "delta": delta, "finish_reason": None}]}
            if usage:
                data["usage"] = usage
            return f"data: {json.dumps(data)}\n\n".encode()

        await asyncio.sleep(p["first"])
        usage = {"prompt_tokens": 50, "completion_tokens": p["tokens"],
                 "total_tokens": 50 + p["tokens"]}
        for n in range(p["tokens"]):
            if n:
                await asyncio.sleep(p["gap"])
            last = n == p["tokens"] - 1
            piece = p["words"][n] if p["words"] else f"word{n} "
            delta = {"reasoning_content": piece} if p["reasoning"] else {"content": piece}
            await resp.write(chunk(delta,
                                   usage if (last and p["usage_in_last"]) else None))
        if not p["usage_in_last"]:
            await resp.write(chunk(None, usage))
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    async def models(self, request):
        self.warmed = getattr(self, "warmed", 0) + 1
        return web.json_response({"object": "list", "data": [{"id": "m", "object": "model"}]})

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_get("/v1/models", self.models)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        await self.runner.cleanup()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v1"


def point(providers, name, fake):
    os.environ[f"{name.upper()}_BASE_URL"] = fake.url
    os.environ[providers.PROVIDERS[name]["key"]] = "fake-key"
    os.environ[f"COMPARE_MODEL_{name.upper()}"] = f"{name}-model"
    providers._clients.pop(name, None)


async def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    import providers
    import compare
    import sambanova

    # The app's own client: no provider named, SambaNova, as always.
    os.environ.setdefault("SAMBANOVA_API_KEY", "offline-test")
    default = sambanova.get_client()
    check("the app's client is still SambaNova with no provider named",
          str(default.base_url).startswith(os.getenv("SAMBANOVA_BASE_URL",
                                                     "https://api.sambanova.ai/v1")))
    check("and a fresh one each time, as before (not the comparison's cache)",
          sambanova.get_client() is not default)

    slow = await Fake(first=0.30, gap=0.05, tokens=10).start()
    quick = await Fake(first=0.10, gap=0.02, tokens=10, usage_in_last=True).start()
    point(providers, "sambanova", slow)
    point(providers, "fireworks", quick)
    messages = [{"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Where is order 44812?"}]
    events = []
    try:
        rounds = await compare.race(["sambanova", "fireworks"], messages, events.append)
        by = {r["side"]: r for r in rounds}
        a, b = by["sambanova"], by["fireworks"]

        check("both requests reach their providers at the same moment",
              abs(slow.arrivals[0] - quick.arrivals[0]) < 0.02)
        check("and each left within a few ms of the shared start",
              a["sent_after"] < 0.01 and b["sent_after"] < 0.01)
        check("each side's connection was warmed before the start line",
              getattr(slow, "warmed", 0) >= 1 and getattr(quick, "warmed", 0) >= 1)
        check("time to first token comes out as planted (0.30 s and 0.10 s)",
              abs(a["ttft"] - 0.30) < 0.08 and abs(b["ttft"] - 0.10) < 0.08)
        check("tokens/sec is ours: 10 tokens over first-to-last (≈22 and ≈56)",
              abs(a["tps"] - 10 / 0.45) / (10 / 0.45) < 0.3
              and abs(b["tps"] - 10 / 0.18) / (10 / 0.18) < 0.35)
        check("full reply time comes out as planted (≈0.75 s and ≈0.28 s)",
              abs(a["total"] - 0.75) < 0.15 and abs(b["total"] - 0.28) < 0.12)
        check("usage is found on either kind of frame",
              a["tokens"] == 10 and b["tokens"] == 10
              and a["tokens_from"] == b["tokens_from"] == "usage")
        same = [{k: v for k, v in body.items() if k not in ("model", "stream_options")}
                for body in (slow.bodies[0], quick.bodies[0])]
        check("both get the same messages, temperature and max tokens", same[0] == same[1]
              and same[0]["max_tokens"] == compare.SETTINGS["max_tokens"])
        check("and each its own model", slow.bodies[0]["model"] == "sambanova-model"
              and quick.bodies[0]["model"] == "fireworks-model")
        kinds = [e["type"] for e in events]
        check("the page is told go, then first tokens, text, and each side done",
              kinds[0] == "go" and kinds.count("first_token") == 2
              and kinds.count("side_done") == 2 and "text" in kinds)
        summary = compare.summarise(["sambanova", "fireworks"], [rounds])
        check("the faster side is named, and by how much",
              summary["faster"]["side"] == "fireworks" and 2 < summary["faster"]["by"] < 3.5)

        # Failures, each as what it was, and no number for it.
        compare_timeout = compare.TIMEOUT_SECONDS
        compare.TIMEOUT_SECONDS = 1.0
        for status, kind in ((429, "rate_limited"), (401, "auth"), (500, "error")):
            slow.plan(status=status)
            before = len(slow.arrivals)
            out = {r["side"]: r for r in await compare.race(
                ["sambanova", "fireworks"], messages, lambda e: None)}
            bad, good = out["sambanova"], out["fireworks"]
            check(f"a {status} is shown as {kind}, with no number",
                  not bad["ok"] and bad["kind"] == kind and "ttft" not in bad
                  and "total" not in bad)
            check(f"the {status} was not quietly retried", len(slow.arrivals) - before == 1)
            check(f"and the other side still finishes ({status})", good["ok"])
        slow.plan(hang=True)
        out = {r["side"]: r for r in await compare.race(
            ["sambanova", "fireworks"], messages, lambda e: None)}
        check("a provider that never answers is shown as timed out",
              out["sambanova"]["kind"] == "timeout"
              and "Timed out" in out["sambanova"]["message"])
        compare.TIMEOUT_SECONDS = compare_timeout

        # A tool call written out as text is not an answer, and must not
        # win on time against one.
        quick.plan(tokens=3, words=['{"order_', 'number": ', '"44812"}'])
        out = {r["side"]: r for r in await compare.race(
            ["sambanova", "fireworks"], messages, lambda e: None)}
        check("a reply that is only a tool call is not timed as an answer",
              not out["fireworks"]["ok"] and out["fireworks"]["kind"] == "tool_call"
              and "total" not in out["fireworks"])

        # A reasoning model that spends every token thinking has said
        # nothing; the clock ticked, but it is not an answer.
        quick.plan(tokens=compare.SETTINGS["max_tokens"], gap=0, reasoning=True)
        out = {r["side"]: r for r in await compare.race(
            ["sambanova", "fireworks"], messages, lambda e: None)}
        check("a run that only reasoned, no words, is not counted as an answer",
              not out["fireworks"]["ok"] and out["fireworks"]["kind"] == "no_words"
              and "used all" in out["fireworks"]["message"])
        quick.plan(tokens=compare.SETTINGS["max_tokens"], gap=0)
        out = {r["side"]: r for r in await compare.race(
            ["sambanova", "fireworks"], messages, lambda e: None)}
        check("a reply stopped by the token limit is marked as cut off",
              out["fireworks"]["ok"] and out["fireworks"]["truncated"]
              and not out["sambanova"]["truncated"])

        # Five runs: the median and the worst, and a failure counted, not
        # averaged in.
        for first in (0.10, 0.30, 0.20, 0.50, 0.40):
            quick.plan(first=first, tokens=4, gap=0.01)
        for n in range(5):
            slow.plan(first=0.05, tokens=4, gap=0.01, status=429 if n == 2 else 200)
        events = []
        summary = await compare.compare(["sambanova", "fireworks"], messages,
                                        events.append, runs=5, pause=0)
        fw, sn = summary["sides"]["fireworks"], summary["sides"]["sambanova"]
        check("five runs give each side's median time to first token",
              abs(fw["ttft"]["median"] - 0.30) < 0.08)
        check("and its worst, not its best",
              abs(fw["ttft"]["worst"] - 0.50) < 0.08 and fw["ttft"]["best"] < 0.2)
        check("a failed run is counted as failed, not averaged in",
              sn["runs"] == 5 and sn["ok"] == 4 and sn["failures"] == ["Rate limited (429)"])
        check("the page is told each round, then the summary",
              [e["n"] for e in events if e["type"] == "round"] == [1, 2, 3, 4, 5]
              and events[-1]["type"] == "summary")
        check("never more than five runs, whatever is asked",
              compare.compare.__defaults__ is not None and compare.MAX_RUNS == 5)

        # Readiness: no model chosen means no run, and says why.
        for name in ("sambanova", "fireworks"):
            os.environ.pop(f"COMPARE_MODEL_{name.upper()}", None)
        os.environ.pop("COMPARE_MODEL", None)
        d = providers.describe("fireworks")
        check("with no model chosen, a provider is not ready and says why",
              not d["ready"] and "COMPARE_MODEL" in d["why"])
        os.environ["COMPARE_MODEL"] = "gpt-oss-120b"
        check("one line picks the model for every provider in the table",
              providers.model_for("sambanova") == "gpt-oss-120b"
              and providers.model_for("fireworks") == "accounts/fireworks/models/gpt-oss-120b")
        check("every result is labelled serverless unless told it is dedicated",
              providers.deployment("fireworks") == "serverless")
        os.environ["COMPARE_DEPLOYMENT_FIREWORKS"] = "dedicated"
        check("and dedicated when it is", providers.describe("fireworks")["deployment"] == "dedicated")
    finally:
        await slow.stop()
        await quick.stop()

    # Leave nothing of the fakes behind for the server the page checks start.
    for name in ("sambanova", "fireworks"):
        for key in (f"{name.upper()}_BASE_URL", f"COMPARE_MODEL_{name.upper()}",
                    f"COMPARE_DEPLOYMENT_{name.upper()}"):
            os.environ.pop(key, None)
    os.environ.pop("COMPARE_MODEL", None)
    os.environ.pop("FIREWORKS_API_KEY", None)
    await page_checks(check)

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


async def page_checks(check):
    """The page and its socket, on a real server."""
    import aiohttp
    from tests.harness import PASSPHRASE, Server

    with Server() as s:
        async with aiohttp.ClientSession() as stranger:
            async with stranger.get(f"{s.base}/compare", allow_redirects=False) as r:
                check("the compare page needs a sign-in like the rest", r.status in (302, 401))
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            await http.post(f"{s.base}/login", data={"passphrase": PASSPHRASE, "consent": "yes"})
            async with http.get(f"{s.base}/compare") as r:
                page = await r.text()
            check("signed in, it loads", r.status == 200 and "compare.js" in page)
            async with http.ws_connect(f"{s.base}/compare/ws") as ws:
                hello = await ws.receive_json()
                check("its socket lists the providers, each with model and label",
                      hello["type"] == "providers"
                      and {p["name"] for p in hello["providers"]} >= {"sambanova", "fireworks", "together"}
                      and all("deployment" in p for p in hello["providers"]))
                await ws.send_json({"type": "run", "question": "hi",
                                    "sides": ["sambanova", "fireworks"], "runs": 1})
                answer = await asyncio.wait_for(ws.receive_json(), 10)
                check("a run that cannot be fair is refused with the reason, not started",
                      answer["type"] == "refused" and answer["why"])

    # What each side is sent: VoxNova's own prompt for that question.
    os.environ.setdefault("CARTESIA_API_KEY", "offline-test")
    os.environ["TEXT_ONLY"] = "1"
    import server as app
    messages = app.compare_messages("Where is my order 44812?")
    roles = [m["role"] for m in messages]
    check("the comparison sends VoxNova's own system prompt and the question",
          roles[:2] == ["system", "user"] and "support agent" in messages[0]["content"])
    check("with the order record a lookup would have added",
          any("Order lookup result" in m["content"] and "44812" in m["content"] for m in messages))
    check("and a last word that there are no tools, so none is written out as text",
          messages[-1]["content"] == app.COMPARE_NO_TOOLS)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
