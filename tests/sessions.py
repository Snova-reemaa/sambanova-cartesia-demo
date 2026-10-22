"""Sign-in tokens expire, and expired ones are forgotten.

The store was a set that only ever grew: one entry per sign-in, kept for the
life of the process, and a cookie that had expired in the browser was still
honoured by the server if anyone held a copy of it.

Offline — this never leaves the login page.
"""

import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import PASSPHRASE, Server                   # noqa: E402


async def sign_in(http, server):
    """Returns the session cookie, or None if the passphrase was refused."""
    async with http.post(f"{server.base}/login", data={"passphrase": PASSPHRASE},
                         allow_redirects=False) as r:
        for name, morsel in r.cookies.items():
            if "session" in name:
                return name, morsel.value
    return None


async def reaches(http, server, cookie):
    name, value = cookie
    async with http.get(f"{server.base}/", allow_redirects=False,
                        headers={"Cookie": f"{name}={value}"}) as r:
        return r.status == 200


async def main():
    results = []

    # A token good for one second: valid now, refused shortly after, and
    # gone from the store rather than lingering in it.
    with Server(SESSION_HOURS=str(1 / 3600)) as server:
        async with aiohttp.ClientSession() as http:
            cookie = await sign_in(http, server)
            results.append(("a fresh token is accepted", bool(cookie) and
                            await reaches(http, server, cookie)))
            await asyncio.sleep(2.5)
            results.append(("an expired token is refused",
                            not await reaches(http, server, cookie)))

    with Server(SESSION_HOURS="12") as server:
        async with aiohttp.ClientSession() as http:
            cookie = await sign_in(http, server)
            results.append(("a 12 hour token is accepted",
                            await reaches(http, server, cookie)))
            results.append(("a made-up token is refused",
                            not await reaches(http, server, ("voxnova_session", "nonsense"))))
            # Signing in many times must not leave many entries behind once
            # they have aged out.
            for _ in range(25):
                await sign_in(http, server)
            results.append(("repeated sign-ins still work",
                            await reaches(http, server, cookie)))

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
