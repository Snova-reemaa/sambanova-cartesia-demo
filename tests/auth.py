"""Auth0 sign-in: nobody gets in on a header they wrote themselves.

With AUTH_PROXY on, the passphrase goes and the consent box stays, and who
someone is comes only from asking oauth2-proxy with their own cookie. A
stand-in proxy here answers 202 for one cookie and 401 for anything else,
so the test can try every way round it: forged X-Auth-Request-* headers, a
made-up cookie, no cookie, the proxy down.

Offline: the stand-in proxy runs on loopback, and nothing leaves it.
"""

import asyncio
import hashlib
import os
import sqlite3
import sys

import aiohttp
from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import Server, free_port                   # noqa: E402

USER = "reema.test@sambanovasystems.com"
GOOD = "_oauth2_proxy=signed-in-by-auth0"
SLOW = "slow=1"
BROKEN = "broken=1"


async def fake_proxy(port):
    """oauth2-proxy's /oauth2/auth, as far as the app can tell."""
    async def auth(request):
        cookie = request.headers.get("Cookie") or ""
        if SLOW in cookie:
            await asyncio.sleep(8)        # longer than the app waits
        if BROKEN in cookie:
            return web.Response(status=500)
        if GOOD in cookie:
            return web.Response(status=202, headers={"X-Auth-Request-Email": USER})
        return web.Response(status=401)
    app = web.Application()
    app.router.add_get("/oauth2/auth", auth)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


FORGED = {"X-Auth-Request-Email": "ceo@sambanovasystems.com",
          "X-Auth-Request-User": "ceo"}


async def post(http, base, cookie=None, consent=True, headers=None):
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    data = {"consent": "yes"} if consent else {}
    async with http.post(f"{base}/login", data=data, headers=h,
                         allow_redirects=False) as r:
        session = next((m.value for n, m in r.cookies.items() if "session" in n), None)
        return r.status, r.headers.get("Location", ""), session


def db(server):
    """The server's own memory file, as the harness names it."""
    return sqlite3.connect(f"/tmp/voxnova-test-{server.port}.sqlite3")


def on_file(server, email):
    with db(server) as d:
        return d.execute("SELECT name, verified FROM customers WHERE email = ?",
                         (email,)).fetchone()


async def turn(ws, text):
    """Say something; every caller event the turn produced."""
    await ws.send_json({"type": "prompt", "text": text, "call": False,
                        "search": False})
    seen = []
    while True:
        try:
            m = await asyncio.wait_for(ws.receive_json(), 40)
        except asyncio.TimeoutError:
            break
        if m["type"] == "caller":
            seen.append(m)
        if m["type"] in ("done", "error") and m.get("turn_id"):
            break
    return seen


async def opening(ws):
    """The ready message, and the caller event after it if there is one."""
    ready = await ws.receive_json()
    try:
        nxt = await asyncio.wait_for(ws.receive_json(), 2)
    except asyncio.TimeoutError:
        nxt = None
    return ready, (nxt if nxt and nxt["type"] == "caller" else None)


async def signed_in_identity(check, http, s, session):
    """With Auth0 on, the sign-in is who they are."""
    cookie = {"Cookie": f"voxnova_session={session}"}

    async with http.ws_connect(f"{s.base}/ws", headers=cookie) as ws:
        _, hello = await opening(ws)
        check("signed in, the call starts knowing who it is",
              hello and hello["status"] == "verified" and hello["signed_in"]
              and hello["name"] == "Reema Test")
        check("their record is made on file, verified, with no code",
              on_file(s, USER) == ("Reema Test", 1))

    # Something remembered from an earlier call.
    with db(s) as d:
        d.execute("INSERT INTO calls (email, at, summary, outcome, actions, sentiment) "
                  "VALUES (?, '2026-10-28 10:00', 'Asked about a late kettle.', "
                  "'pending', 'none', 'calm')", (USER,))

    async with http.ws_connect(f"{s.base}/ws", headers=cookie) as ws:
        _, hello = await opening(ws)
        check("the next call has their history, tied to the sign-in email",
              hello and [c["summary"] for c in hello["calls"]]
              == ["Asked about a late kettle."])

        said = await turn(ws, f"hi, my email is {USER}")
        check("giving their own email asks for no code",
              not any(e["status"] == "code_sent" or e.get("demo_code") for e in said))

        # Read back first, like any address; the code after a yes.
        await turn(ws, "actually it's r.patel@example.com")
        said = await turn(ws, "yes")
        sent = next((e for e in said if e["status"] == "code_sent"), None)
        check("another email is a claim: a code, as without Auth0",
              sent and sent.get("demo_code"))
        check("and nothing from that record is shared before it checks out",
              sent and sent["name"] is None and sent["calls"] == [])

        said = await turn(ws, sent["demo_code"] if sent else "000000")
        got = next((e for e in said if e["status"] == "verified"), None)
        check("reading that code back then opens that record, as usual",
              got and got["name"] == "Riya Patel" and len(got["calls"]) == 2)

        said = await turn(ws, "please forget me")
        check("'forget me' says it is done",
              any(e["status"] == "forgotten" for e in said))
        check("and deletes the record for the signed-in email",
              on_file(s, USER) is None)
        check("not the other record this call was about",
              on_file(s, "r.patel@example.com") is not None)

        await turn(ws, f"my email is {USER}")
        check("nor is it made again for the rest of the call",
              on_file(s, USER) is None)


async def code_flow_without_auth0(check):
    """Auth0 off: nobody is anybody until a code says so."""
    from tests.harness import PASSPHRASE
    with Server(DEMO_INBOX="1") as s:
        async with aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            await http.post(f"{s.base}/login",
                            data={"passphrase": PASSPHRASE, "consent": "yes"})
            async with http.ws_connect(f"{s.base}/ws") as ws:
                _, hello = await opening(ws)
                check("without Auth0, a call starts knowing nobody", hello is None)
                await turn(ws, "my email is fresh.person@example.org")
                said = await turn(ws, "yes")
                sent = next((e for e in said if e["status"] == "code_sent"), None)
                check("and an email still gets a code",
                      sent and sent.get("demo_code"))
                said = await turn(ws, sent["demo_code"] if sent else "000000")
                check("which still verifies them",
                      any(e["status"] == "verified" for e in said))


async def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    proxy_port = free_port()
    proxy = await fake_proxy(proxy_port)
    try:
        with Server(AUTH_PROXY="1", EXPOSE_WHOAMI="1", TRUSTED_PROXIES="127.0.0.1",
                    DEMO_INBOX="1",
                    AUTH_CHECK_URL=f"http://127.0.0.1:{proxy_port}/oauth2/auth") as s:
            async with aiohttp.ClientSession(
                    cookie_jar=aiohttp.DummyCookieJar()) as http:
                async with http.get(f"{s.base}/login") as r:
                    page = await r.text()
                check("the sign-in page asks for no passphrase",
                      r.status == 200 and 'name="passphrase"' not in page)
                check("and still has the consent box", 'name="consent"' in page)

                status, _, session = await post(http, s.base, headers=FORGED)
                check("forged X-Auth-Request headers alone get nothing",
                      status == 401 and not session)
                status, _, session = await post(
                    http, s.base, cookie="_oauth2_proxy=made-up", headers=FORGED)
                check("nor with a made-up oauth2 cookie",
                      status == 401 and not session)
                check("nor does the passphrase route still work",
                      (await post(http, s.base, headers={}))[0] == 401)

                status, where, session = await post(http, s.base, cookie=GOOD,
                                                    consent=False)
                check("signed in but not agreeing is still refused",
                      not session and "noconsent" in where)

                status, where, session = await post(http, s.base, cookie=GOOD,
                                                    headers=FORGED)
                check("signed in and agreeing gets a session",
                      status == 302 and session)
                signed = session
                async with http.get(f"{s.base}/whoami",
                                    headers={"Cookie": f"voxnova_session={session}",
                                             **FORGED}) as r:
                    key = await r.text()
                want = hashlib.sha256(f"user|{USER}".encode()).hexdigest()[:32]
                check("and is keyed on the user the proxy named, not the header",
                      key == want)

                # Fail closed: the proxy taking too long, or failing, is not
                # a sign-in, even with the good cookie, and it is logged.
                status, _, session = await post(http, s.base, cookie=f"{GOOD}; {SLOW}")
                check("a check that times out lets nobody in",
                      status == 401 and not session)
                status, _, session = await post(http, s.base, cookie=f"{GOOD}; {BROKEN}")
                check("a check the proxy fails lets nobody in",
                      status == 401 and not session)
                log = s.output()
                check("and both are logged",
                      "could not ask oauth2-proxy who this is (TimeoutError)" in log
                      and "oauth2-proxy answered 500" in log)

                async with http.get(f"{s.base}/privacy") as r:
                    notice = await r.text()
                check("/privacy says the sign-in email is recorded with consent",
                      "sign-in email" in notice and "Auth0 (Okta)" in notice
                      and "login activity" in notice)

                await signed_in_identity(check, http, s, signed)

        # The proxy down: shut, not open.
        await proxy.cleanup()
        proxy = None
        with Server(AUTH_PROXY="1", TRUSTED_PROXIES="127.0.0.1",
                    AUTH_CHECK_URL=f"http://127.0.0.1:{proxy_port}/oauth2/auth") as s:
            async with aiohttp.ClientSession() as http:
                status, _, session = await post(http, s.base, cookie=GOOD)
            unreachable_logged = "could not ask oauth2-proxy" in s.output()
        check("with the proxy unreachable nobody gets in, and it is logged",
              status == 401 and not session and unreachable_logged)

        # And off, which is the default: the notice says nothing about it.
        with Server() as s:
            async with aiohttp.ClientSession() as http:
                async with http.get(f"{s.base}/privacy") as r:
                    notice = await r.text()
        check("without Auth0, /privacy does not mention a sign-in email",
              "sign-in email" not in notice and "Auth0" not in notice)

        await code_flow_without_auth0(check)
    finally:
        if proxy:
            await proxy.cleanup()

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
