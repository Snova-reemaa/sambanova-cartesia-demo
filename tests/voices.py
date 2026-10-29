"""Every language spoken only by a native voice, whoever is speaking.

Offline: the table, the specialists, the switch of voice with language,
and that a voice configured in the wrong language stops the server from
starting. What Cartesia itself says each voice's language is gets checked
by tests/live_voices.py, which asks it (free: no synthesis).
"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for key in [k for k in os.environ if k.startswith("VOICE_")]:
    del os.environ[key]

import httpx                                                  # noqa: E402
import cartesia                                               # noqa: E402
import voices                                                 # noqa: E402


async def through_the_socket(check):
    import aiohttp
    from tests.harness import PASSPHRASE, Server

    skylar = voices.VOICES["front"]["en"][1]
    reem = voices.VOICES["front"]["ar"][1]
    fatima = voices.VOICES["billing"]["ar"][1]

    async def turn_voice(ws, language, picked):
        await ws.send_json({"type": "prompt", "text": "مرحبا", "call": False,
                            "search": False, "language": language,
                            "voice_id": picked})
        while True:
            m = await asyncio.wait_for(ws.receive_json(), 30)
            if m["type"] == "turn_start":
                return m["voice_id"], m["language"]

    with Server() as s:
        # unsafe: the default jar drops cookies set by a bare IP address.
        async with aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            async with http.post(f"{s.base}/login", data={
                    "passphrase": PASSPHRASE, "consent": "yes"}) as r:
                assert r.status == 200
            async with http.ws_connect(f"{s.base}/ws") as ws:
                ready = await ws.receive_json()
                offered = ready.get("voices") or []
                check("the picker is offered only the eight languages",
                      offered and {v.get("language") for v in offered}
                      == set(voices.LANGUAGES))
                check("three voices each, labelled main / billing / technical",
                      all(sorted(v.get("role", "?") for v in offered if v.get("language") == c)
                          == ["billing", "front", "technical"]
                          for c in voices.LANGUAGES))
                check("and every one is a voice from the native table",
                      all(v["id"] == voices.for_role(v.get("role"), v.get("language"))
                          for v in offered))

                used, lang = await turn_voice(ws, "ar", skylar)
                check("Arabic with Skylar picked is spoken by Reem",
                      lang == "ar" and used == reem)
                used, lang = await turn_voice(ws, "ar", fatima)
                check("a picked Arabic voice is used for Arabic",
                      used == fatima)
                used, lang = await turn_voice(ws, "en", fatima)
                check("and not when the turn is in English",
                      lang == "en" and used == skylar)
                used, _ = await turn_voice(ws, "ar", "a-voice-nobody-has")
                check("an id the server does not know is ignored", used == reem)


def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    # Whose voice is whose language, according to the table itself.
    native = {code: {voices.VOICES[r][code][1] for r in voices.ROLES}
              for code in voices.LANGUAGES}

    for code in voices.LANGUAGES:
        three = [voices.for_role(r, code) for r in voices.ROLES]
        check(f"{code}: main, billing and technical all have a voice", all(three))
        check(f"{code}: and they are three different people", len(set(three)) == 3)
        check(f"{code}: every one of them is a {code} voice",
              all(v in native[code] for v in three))
        others = set().union(*(native[c] for c in voices.LANGUAGES if c != code))
        check(f"{code}: none of them belongs to another language",
              not (set(three) & others))

    check("a language not in the eight gets no voice at all",
          voices.for_role("front", "fi") is None
          and voices.for_role("billing", "fi") is None)

    # The old one-voice-for-every-language settings apply to English only.
    os.environ["VOICE_BILLING"] = voices.VOICES["front"]["en"][1]
    check("VOICE_BILLING is Maya's English voice and nothing else",
          voices.for_role("billing", "en") == voices.VOICES["front"]["en"][1]
          and voices.for_role("billing", "ar") == voices.VOICES["billing"]["ar"][1])
    del os.environ["VOICE_BILLING"]

    # A role switched off in a language falls to that language's main
    # voice; the language switched off entirely is text only.
    os.environ["VOICE_TECHNICAL_AR"] = ""
    check("a role with no voice uses the language's main voice",
          voices.for_role("technical", "ar") == voices.VOICES["front"]["ar"][1])
    os.environ.update(VOICE_AR="", VOICE_BILLING_AR="")
    check("a language with no voice is text only, never another voice",
          all(voices.for_role(r, "ar") is None for r in voices.ROLES))
    for key in ("VOICE_AR", "VOICE_BILLING_AR", "VOICE_TECHNICAL_AR"):
        del os.environ[key]

    # Startup, against a Cartesia that answers from a table we hold.
    import server as app

    class FakeVoices:
        def __init__(self, langs, fail=None):
            self.langs, self.fail = langs, fail

        async def get(self, vid):
            if self.fail:
                raise self.fail
            if vid not in self.langs:
                request = httpx.Request("GET", f"https://api.cartesia.ai/voices/{vid}")
                raise cartesia.NotFoundError(
                    "not found", response=httpx.Response(404, request=request),
                    body=None)
            return type("Voice", (), {"language": self.langs[vid]})()

    class FakeTTS:
        def __init__(self, langs, fail=None):
            self.voices = FakeVoices(langs, fail)

    truth = {voices.VOICES[r][c][1]: c for r in voices.ROLES for c in voices.LANGUAGES}

    def starts(langs, fail=None):
        try:
            asyncio.run(app.check_voices(FakeTTS(langs, fail)))
            return True, ""
        except SystemExit as exc:
            return False, str(exc)

    ok, _ = starts(truth)
    check("the configured table starts", ok)

    os.environ["VOICE_AR"] = voices.VOICES["front"]["en"][1]   # Skylar, English
    printed = []
    app.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
    try:
        ok, why = starts(truth)
    finally:
        del app.print
    check("an English voice set for Arabic refuses to start", not ok)
    check("and says which setting is wrong",
          any("VOICE_AR=" in line and "not ar" in line for line in printed))
    del os.environ["VOICE_AR"]

    os.environ["VOICE_TECHNICAL_FR"] = "00000000-0000-0000-0000-000000000000"
    app.print = lambda *a, **k: None
    try:
        ok, _ = starts(truth)
    finally:
        del app.print
    check("a voice Cartesia does not have refuses to start", not ok)
    del os.environ["VOICE_TECHNICAL_FR"]

    request = httpx.Request("GET", "https://api.cartesia.ai/voices/x")
    app.print = lambda *a, **k: None
    try:
        ok, _ = starts(truth, fail=cartesia.APIConnectionError(request=request))
    finally:
        del app.print
    check("not being able to reach Cartesia is not a reason to stop", ok)

    # Mid-call: the caller switches from English to Arabic, and the voice
    # with them, on the next sentence.
    app.VOICE_LANGUAGE.update(truth)
    session = app.Session()
    en, ar = app.voice_for(session, "en"), app.voice_for(session, "ar")
    check("switching en to ar changes the voice",
          en == voices.VOICES["front"]["en"][1]
          and ar == voices.VOICES["front"]["ar"][1])
    picked = voices.VOICES["technical"]["en"][1]           # an English voice from the picker
    check("a voice chosen in the picker is used in its own language",
          app.voice_for(session, "en", picked) == picked)
    check("and never for another language",
          app.voice_for(session, "ar", picked) == voices.VOICES["front"]["ar"][1])

    # The specialists, in every language, with the picker trying its luck.
    foreign = []
    for persona in ("billing", "technical"):
        session.persona = persona
        for code in voices.LANGUAGES:
            got = app.voice_for(session, code, picked)
            if got not in native[code]:
                foreign.append(f"{persona}/{code}")
            elif got != voices.VOICES[persona][code][1]:
                foreign.append(f"{persona}/{code} not their own")
    check("Maya and Omar speak every language in their own native voice",
          not foreign)

    # Through the socket, as the page uses it. The picker cannot force a
    # foreign voice: the voice a turn reports in turn_start is the one its
    # sentences are synthesised in.
    asyncio.run(through_the_socket(check))

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
