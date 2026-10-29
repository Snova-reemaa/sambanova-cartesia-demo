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


async def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    proxy_port = free_port()
    proxy = await fake_proxy(proxy_port)
    try:
        with Server(AUTH_PROXY="1", EXPOSE_WHOAMI="1", TRUSTED_PROXIES="127.0.0.1",
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
                      "sign-in email" in notice and "Auth0" in notice)

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
