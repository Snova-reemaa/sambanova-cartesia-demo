"""'Your own provider': a stranger's URL and key, called from inside the cluster.

Checked:

- the key never reaches a log, the bench recorder, an error shown, or the
  page, even when a provider echoes it back in its 401;
- private, loopback, link-local, shared and cluster addresses are blocked,
  in IPv4 and IPv6, by literal and by a name that resolves to one;
- a redirect to a private address is not followed, and the connection
  refuses any host but the one given;
- a normal https provider works: a local server with a throwaway
  certificate, reached through the same pinned, verified connection;
- bad URL, refused key, unknown model and timeout each say so plainly;
- a session can only run so many.

Offline. The local https server is the one thing allowed past the address
check, through a test-only parameter nothing on the page can set.
"""

import asyncio
import contextlib
import io
import ipaddress
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time

from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import free_port                            # noqa: E402

KEY = "sk-own-PROVIDERKEY-4f9c2e81d7a6b5c3"
HOST = "fake-provider.test"
LOCAL = [ipaddress.ip_network("127.0.0.1/32")]


def certificate(where):
    cert, key = os.path.join(where, "cert.pem"), os.path.join(where, "key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", key, "-out", cert, "-subj", f"/CN={HOST}",
                    "-addext", f"subjectAltName=DNS:{HOST}"],
                   check=True, capture_output=True)
    return cert, key


class Provider:
    """An OpenAI-compatible provider over https, answering as told."""

    def __init__(self, cert, key):
        self.port = free_port()
        self.mode = "ok"
        self.seen = []
        self.server_ssl = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.server_ssl.load_cert_chain(cert, key)

    async def chat(self, request):
        self.seen.append(dict(request.headers))
        body = await request.json()
        if self.mode == "redirect":
            raise web.HTTPTemporaryRedirect("https://10.0.0.5/v1/chat/completions")
        if self.mode == "401":
            # Providers really do this.
            return web.json_response({"error": {"message":
                f"Incorrect API key provided: {request.headers.get('Authorization', '')[7:]}"}},
                status=401)
        if self.mode == "404":
            return web.json_response({"error": {"message": "The model does not exist"}}, status=404)
        if self.mode == "400-model":
            return web.json_response({"error": {"message": f"Unknown model: {body['model']}"}}, status=400)
        if self.mode == "hang":
            await asyncio.sleep(30)
            return web.Response(status=504)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for n in range(5):
            await asyncio.sleep(0.02)
            data = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": f"word{n} "}, "finish_reason": None}]}
            await resp.write(f"data: {json.dumps(data)}\n\n".encode())
        usage = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
                 "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14}}
        await resp.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
        await resp.write_eof()
        return resp

    async def models(self, request):
        return web.json_response({"object": "list", "data": []})

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_get("/v1/models", self.models)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port, ssl_context=self.server_ssl).start()
        return self

    async def stop(self):
        await self.runner.cleanup()


async def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    import providers
    import compare

    # ---- addresses
    inside = ["10.0.0.1", "10.43.0.10", "172.16.5.4", "172.31.255.255", "192.168.1.1",
              "127.0.0.1", "169.254.169.254", "100.64.0.1", "100.127.255.254", "0.0.0.0",
              "::1", "fe80::1", "fc00::1", "fd12:3456::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1",
              "224.0.0.1"]
    check("private, loopback, link-local, shared, cluster and their IPv6 forms are blocked",
          all(providers.blocked(ip) for ip in inside))
    check("public addresses are not", not any(providers.blocked(ip) for ip in
                                               ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111")))

    async def refused(url, key=KEY, model="m"):
        try:
            await providers.custom_side(url, key, model)
        except providers.Refused as why:
            return str(why)
        return None

    check("http is refused", "https" in (await refused("http://api.example.com/v1") or ""))
    check("a user:password in the URL is refused",
          "password" in (await refused("https://u:p@api.example.com/v1") or ""))
    check("a URL with no host is refused", await refused("https:///v1"))
    check("an empty URL asks for one", "base URL" in (await refused("") or ""))
    check("a private IP written in the URL is blocked",
          "private network" in (await refused("https://10.0.0.5/v1") or ""))
    check("so is localhost", "private network" in (await refused("https://localhost/v1") or ""))
    check("and the cloud metadata address",
          "private network" in (await refused("https://169.254.169.254/latest") or ""))
    check("and an IPv6 loopback", "private network" in (await refused("https://[::1]/v1") or ""))
    disguised = [await refused(u) or "" for u in
                 ("https://2130706433/v1", "https://0177.0.0.1/v1", "https://0x7f.0.0.1/v1",
                  "https://127.1/v1")]
    check("an IP in an unusual form (number, octal, hex, short) is refused, on any platform",
          all("unusual form" in why for why in disguised))
    check("localhost with a trailing dot is still blocked",
          "private network" in (await refused("https://localhost./v1") or ""))
    check("nor is a real host name made only of a-f letters (cafe.be)",
          "unusual form" not in (await refused("https://cafe.be/v1", key="") or ""))
    check("a plain public IP is not mistaken for one",
          "unusual form" not in (await refused("https://203.0.113.9/v1", key="") or ""))
    real_resolve = providers.resolve

    async def inside_dns(host, port):
        return ["93.184.216.34", "10.1.2.3"] if host == "sneaky.example" else await real_resolve(host, port)
    providers.resolve = inside_dns
    check("a name that resolves to any private address is blocked",
          "private network" in (await refused("https://sneaky.example/v1") or ""))
    providers.resolve = real_resolve
    check("a host that does not exist says so",
          "Could not find" in (await refused("https://no-such-host.invalid/v1") or ""))
    check("no key asks for one", "API key" in (await refused("https://api.example.com/v1", key="") or ""))
    check("no model asks for one", "model" in (await refused("https://api.example.com/v1", model="") or ""))

    # ---- the pinned transport itself refuses any other host
    import httpx
    pinned = providers.PinnedTransport(HOST, "127.0.0.1")
    try:
        await pinned.handle_async_request(httpx.Request("GET", "https://10.0.0.5/elsewhere"))
        other = False
    except providers.Refused:
        other = True
    await pinned.aclose()
    check("the connection refuses any host but the one given", other)

    # ---- a real https provider, end to end
    where = tempfile.mkdtemp(prefix="voxnova-own-")
    cert, key_file = certificate(where)
    trust = ssl.create_default_context(cafile=cert)
    theirs = await Provider(cert, key_file).start()

    async def resolve_fake(host, port):
        return ["127.0.0.1"] if host == HOST else await real_resolve(host, port)
    providers.resolve = resolve_fake

    messages = [{"role": "user", "content": "hi"}]
    printed = io.StringIO()
    os.environ["BENCH_LOG"] = os.path.join(where, "requests.jsonl")
    try:
        async def own():
            return await providers.custom_side(f"https://{HOST}:{theirs.port}/v1", KEY, "their-model",
                                               allow=LOCAL, verify=trust)

        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            mine = await own()
            check("the page is shown no key: label, model, 'serverless/unknown' and a caveat",
                  KEY not in json.dumps(mine.public())
                  and mine.public()["label"] == "Your provider (their-model)"
                  and mine.public()["deployment"] == "serverless/unknown"
                  and "can't verify" in mine.public()["note"])
            events = []
            [r] = await compare.race([mine], messages, events.append)
            check("a normal https provider works, through the pinned, verified connection",
                  r["ok"] and r["tokens"] == 5 and r["tps"])
            check("its key is sent to it, and only as the Authorization header",
                  theirs.seen[-1].get("Authorization") == f"Bearer {KEY}")
            await mine.close()
            check("and dropped when the run is over", mine.secret is None and mine.client is None)

            for mode, kind, says in (("401", "auth", "Key refused (401)"),
                                     ("404", "not_found", "Model not found"),
                                     ("400-model", "not_found", "Unknown model"),
                                     ("redirect", "redirect", "redirects are not followed")):
                theirs.mode = mode
                before = len(theirs.seen)
                mine = await own()
                [r] = await compare.race([mine], messages, events.append)
                await mine.close()
                check(f"{mode}: shown as {says!r}", not r["ok"] and r["kind"] == kind
                      and says in r["message"])
                if mode == "401":
                    check("a 401 that echoes the key shows it nowhere",
                          KEY not in json.dumps(r) and KEY[:8] not in json.dumps(r))
                if mode == "redirect":
                    check("the redirect to a private address was not followed",
                          len(theirs.seen) - before == 1)
            theirs.mode = "hang"
            saved = compare.TIMEOUT_SECONDS
            compare.TIMEOUT_SECONDS = 1.0
            mine = await own()
            [r] = await compare.race([mine], messages, events.append)
            await mine.close()
            compare.TIMEOUT_SECONDS = saved
            check("a provider that never answers is shown as timed out", r["kind"] == "timeout")
            check("no event sent to the page carries the key", KEY not in json.dumps(events))
        check("nothing printed carries the key", KEY not in printed.getvalue())
        log = os.environ["BENCH_LOG"]
        check("the bench recorder never sees the user's provider",
              not os.path.exists(log) or KEY not in open(log).read())
    finally:
        os.environ.pop("BENCH_LOG", None)
        providers.resolve = real_resolve
        await theirs.stop()

    await through_the_page(check, where)

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


async def through_the_page(check, where):
    """A real server, as the page drives it, with the recorder switched on."""
    import aiohttp
    from tests.harness import PASSPHRASE, Server

    bench_log = os.path.join(where, "server-requests.jsonl")
    sent = []
    with Server(BENCH_LOG=bench_log, COMPARE_MODEL_SAMBANOVA="sambanova-model",
                COMPARE_CUSTOM_RUNS="4") as s:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            await http.post(f"{s.base}/login", data={"passphrase": PASSPHRASE, "consent": "yes"})
            async with http.ws_connect(f"{s.base}/compare/ws") as ws:
                hello = await ws.receive_json()
                sent.append(hello)
                novita = next(p for p in hello["providers"] if p["name"] == "novita")
                check("Novita stays in the list, not ready, as 'no key set'",
                      not novita["ready"] and novita["why"] == "no key set")

                async def ask(base_url):
                    await ws.send_json({"type": "run", "question": "hi", "runs": 1,
                                        "sides": ["sambanova", "custom"],
                                        "custom": {"base_url": base_url, "key": KEY, "model": "m"}})
                    m = await asyncio.wait_for(ws.receive_json(), 15)
                    sent.append(m)
                    return m

                m = await ask("http://api.example.com/v1")
                check("the page is told plainly: https only", m["type"] == "refused" and "https" in m["why"])
                m = await ask("https://localhost/v1")
                check("the page is told plainly: private addresses are blocked",
                      m["type"] == "refused" and "private network" in m["why"])
                m = await ask("https://10.0.0.5/v1")
                m = await ask("https://192.168.0.10/v1")
                m = await ask("https://172.20.0.1/v1")
                check("and after a few, the session limit says so",
                      m["type"] == "refused" and "limit" in m["why"])
        server_log = s.output()
    check("the key is not in the server's log", KEY not in server_log)
    check("nor in the bench recorder's file",
          not os.path.exists(bench_log) or KEY not in open(bench_log).read())
    check("nor in anything sent back to the page", KEY not in json.dumps(sent))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
