"""Nobody gets in without agreeing, and the box is not what enforces it.

A checkbox is a convenience. The check that matters is the one on the
server, because the form can be posted directly — and the one that is
easiest to forget is re-asking when the wording changes, since a signed-in
session carries on happily agreeing to text nobody can read any more.

Offline — none of this leaves the login page.
"""

import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import PASSPHRASE, Server                   # noqa: E402


async def post(http, server, **fields):
    async with http.post(f"{server.base}/login", data=fields,
                         allow_redirects=False) as r:
        cookie = None
        for name, morsel in r.cookies.items():
            if "session" in name:
                cookie = (name, morsel.value)
        return r.status, r.headers.get("Location", ""), cookie


async def get(http, server, path, cookie=None):
    headers = {"Cookie": f"{cookie[0]}={cookie[1]}"} if cookie else {}
    async with http.get(f"{server.base}{path}", allow_redirects=False,
                        headers=headers) as r:
        return r.status


async def ws_ok(http, server, cookie):
    headers = {"Cookie": f"{cookie[0]}={cookie[1]}"} if cookie else {}
    try:
        async with http.ws_connect(f"{server.base}/ws", headers=headers):
            return True
    except Exception:
        return False


async def main():
    results = []

    with Server(POLICY_VERSION="v1") as server:
        async with aiohttp.ClientSession() as http:
            # Readable before signing in — they describe what signing in agrees to.
            for path in ("/terms", "/privacy"):
                results.append((f"{path} loads without login",
                                await get(http, server, path) == 200))

            # The form posted directly, without the box.
            status, where, cookie = await post(http, server, passphrase=PASSPHRASE)
            results.append(("posting without consent is refused",
                            cookie is None and "noconsent" in where))

            # Wrong passphrase with consent is still wrong.
            status, where, cookie = await post(http, server, passphrase="nope",
                                               consent="yes")
            results.append(("a wrong passphrase is still refused",
                            cookie is None and "bad" in where))

            # Both together.
            status, where, good = await post(http, server, passphrase=PASSPHRASE,
                                             consent="yes")
            results.append(("passphrase plus consent gets in", good is not None))
            results.append(("the app loads",
                            good and await get(http, server, "/", good) == 200))
            results.append(("the websocket opens",
                            good and await ws_ok(http, server, good)))

        # Without consent there is no cookie at all, so the websocket has
        # nothing to present.
        async with aiohttp.ClientSession() as http:
            results.append(("the websocket is refused with no session",
                            not await ws_ok(http, server, None)))

    # The wording changing mid-life is the case that matters and cannot be
    # staged with two servers — a token from one is simply unknown to the
    # other, which proves nothing about the version check. So the check
    # itself is exercised directly, against a session that really did agree.
    import server as app

    class FakeRequest:
        def __init__(self, token):
            self.cookies = {app.COOKIE_NAME: token} if token else {}

    was = app.policies.VERSION
    try:
        app.policies.VERSION = "v1"
        token = "a-token-that-agreed-to-v1"
        app.SESSIONS[token] = {"until": __import__("time").time() + 3600,
                               "consent_at": __import__("time").time(),
                               "policy": "v1"}
        results.append(("a session that agreed to the current wording is let through",
                        app.consented(FakeRequest(token))))

        app.policies.VERSION = "v2"
        results.append(("the same session is asked again once the wording changes",
                        not app.consented(FakeRequest(token))))
        results.append(("it is still signed in, just not consented",
                        app.authorised(FakeRequest(token))))

        app.SESSIONS[token]["policy"] = "v2"
        results.append(("agreeing to the new wording lets it through again",
                        app.consented(FakeRequest(token))))

        results.append(("a session that never existed is not consented",
                        not app.consented(FakeRequest("never-seen"))))
        results.append(("no cookie at all is not consented",
                        not app.consented(FakeRequest(None))))
    finally:
        app.policies.VERSION = was
        app.SESSIONS.pop("a-token-that-agreed-to-v1", None)

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
