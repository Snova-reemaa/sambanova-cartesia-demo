"""Names and emails are confirmed before they are saved, in eight languages.

For every language: confirmed the first time and saved; a no, a spelling
in that language's letter names, confirmed and saved; two misses, the box,
and what is typed saved exactly; accents and Arabic script intact; the
same three tries for the email; and a returning caller's corrected name
kept, never a demo customer's.

Offline: the server's own functions on their own store, then twice
through the socket of a real server in TEXT_ONLY mode.
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import confirming                                             # noqa: E402

# Per language: how a caller gives a name and an email, the yes and the
# no, the name as misheard and as spelled in that language's letter names,
# a name only typing gets right, and an email spelled with its symbols.
CASES = {
    "en": dict(
        intro="Hi, my name is Rima Alotabi", yes="yes, that's right", no="no",
        heard="Rima Alotabi",
        spelled="ar ee ee em ay space ay el oh tee ay eye bee eye",
        name="Reema Alotaibi", typed="Zoë O'Brien",
        says_email="my email is reema.a@example.org",
        email_spelled="ar ee ee em ay dot ay at ee ex ay em pee el ee dot oh ar gee",
        email="reema.a@example.org"),
    "es": dict(
        intro="Hola, me llamo Lucia Nunes", yes="sí, correcto", no="no",
        heard="Lucia Nunes",
        spelled="ele u ce i con tilde a espacio ene u eñe e zeta",
        name="Lucía Nuñez", typed="Lucía Núñez",
        says_email="mi correo es lucia.n@example.org",
        email_spelled="ele u ce i a punto ene arroba e equis a eme pe ele e punto o erre ge",
        email="lucia.n@example.org"),
    "fr": dict(
        intro="Bonjour, je m'appelle Elodie Francoise", yes="oui, c'est ça", no="non",
        heard="Elodie Francoise",
        spelled="e accent aigu elle o dé i e espace effe erre a enne c cédille o i esse e",
        name="Élodie Françoise", typed="Élodie Françoise-Lefèvre",
        says_email="mon adresse est elodie.f@example.org",
        email_spelled="e elle o dé i e point effe arobase e iks a emme pé elle e point o erre gé",
        email="elodie.f@example.org"),
    "de": dict(
        intro="Guten Tag, ich heiße Jurgen Weis", yes="ja, genau", no="nein",
        heard="Jurgen Weis",
        spelled="jot ü er ge e en leerzeichen we e i eszett",
        name="Jürgen Weiß", typed="Jürgen Weiß",
        says_email="meine E-Mail ist juergen.w@example.org",
        email_spelled="jot u e er ge e en Punkt we at e iks a em pe el e Punkt o er ge",
        email="juergen.w@example.org"),
    "it": dict(
        intro="Buongiorno, mi chiamo Nicolo Davila", yes="sì, esatto", no="no",
        heard="Nicolo Davila",
        spelled="enne i ci ci o con accento grave spazio di a vu i elle a",
        name="Niccò Davila", typed="Niccolò D'Àvila",
        says_email="la mia email è nicco.d@example.org",
        email_spelled="enne i ci ci o punto di chiocciola e ics a emme pi elle e punto o erre gi",
        email="nicco.d@example.org"),
    "pt": dict(
        intro="Olá, meu nome é Joao Conceicao", yes="sim, correto", no="não",
        heard="Joao Conceicao",
        spelled="jota ó a til ó espaço cê ó ene cê é i cê cedilha a til ó",
        name="João Conceição", typed="João Conceição",
        says_email="o meu email é joao.c@example.org",
        email_spelled="jota ó á ó ponto cê arroba é xis á eme pê ele é ponto ó erre gê",
        email="joao.c@example.org"),
    "nl": dict(
        intro="Goedemiddag, ik heet Danielle Bijl", yes="ja, klopt", no="nee",
        heard="Danielle Bijl",
        spelled="dee aa en ie ee met trema el el ee spatie bee ie jee el",
        name="Daniëlle Bijl", typed="Daniëlle van der Bijl",
        says_email="mijn e-mail is danielle.b@example.org",
        email_spelled="dee aa en ie ee el el ee punt bee apenstaartje ee iks aa em pee el ee punt oo er gee",
        email="danielle.b@example.org"),
    "ar": dict(
        intro="مرحبا، اسمي ريما العتيبي", yes="نعم صحيح", no="لا",
        heard="ريما العتيبي",
        spelled="راء ياء ميم مسافة ألف لام عين تاء ياء باء ياء",
        name="ريم العتيبي", typed="ريم العُتيبي",
        says_email="بريدي هو reem.o@example.org",
        email_spelled="ar ee ee em نقطة oh آت ee ex ay em pee el ee نقطة oh ar gee",
        email="reem.o@example.org"),
}


def fresh_server():
    """The server module on its own store, with the demo inbox on."""
    os.environ["MEMORY_FILE"] = os.path.join(tempfile.mkdtemp(prefix="voxnova-confirm-"), "m.sqlite3")
    os.environ.setdefault("SAMBANOVA_API_KEY", "offline-test")
    os.environ.setdefault("CARTESIA_API_KEY", "offline-test")
    os.environ["TEXT_ONLY"] = "1"
    for name in ("memory", "server"):
        sys.modules.pop(name, None)
    import memory
    import server as app
    memory.init()
    app.delivery.DEMO_INBOX = True
    app.memory = memory
    return app, memory


def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    app, memory = fresh_server()

    def session(key):
        s = app.Session()
        s.key = key
        return s

    def go(s, text, code, typed=None):
        out = app.confirmation(s, text, code, typed)
        return out if out else ([], None)

    def stages(events):
        return [(e.get("field"), e.get("stage")) for e in events if e.get("type") == "confirm"]

    def claimed(events):
        return next((e for e in events if e.get("type") == "caller"), None)

    for code, c in CASES.items():
        lang = f"[{code}]"

        # 1. Right the first time: read back, a yes, saved.
        s = session(f"{code}-1")
        ev, said = go(s, c["intro"], code)
        check(f"{lang} the name is read back in the caller's language",
              stages(ev) == [("name", "readback")]
              and said == confirming.say("readback_name", code, v=c["heard"]))
        ev, said = go(s, c["yes"], code)
        check(f"{lang} a yes confirms it, and the email is asked for next",
              ("name", "done") in stages(ev) and confirming.say("ask_email", code) in said)
        ev, said = go(s, c["says_email"], code)
        check(f"{lang} the email is read back letter by letter",
              stages(ev) == [("email", "readback")]
              and confirming.say_spelled(c["email"], "email", code) in said)
        ev, said = go(s, c["yes"], code)
        check(f"{lang} confirmed, a code goes out",
              (claimed(ev) or {}).get("status") == "code_sent"
              and said == confirming.say("code_sent", code))
        check(f"{lang} and the confirmed name is the one on file",
              (memory.customer(c["email"]) or {}).get("name") == c["heard"])
        memory.forget(c["email"])

        # 2. A no, spelled, confirmed, saved.
        s = session(f"{code}-2")
        go(s, c["intro"], code)
        ev, said = go(s, c["no"], code)
        check(f"{lang} a no asks for it letter by letter",
              stages(ev) == [("name", "spell")]
              and said == confirming.say("ask_spell_name", code))
        ev, said = go(s, c["spelled"], code)
        check(f"{lang} the spelling is read back, letters and name",
              stages(ev) == [("name", "readback")] and c["name"] in said
              and confirming.say_spelled(c["name"], "name", code) in said)
        go(s, c["yes"], code)
        go(s, c["says_email"], code)
        go(s, c["yes"], code)
        check(f"{lang} the spelled name is the one saved",
              (memory.customer(c["email"]) or {}).get("name") == c["name"])
        memory.forget(c["email"])

        # 3. Two misses, the box, typed, saved exactly as typed.
        s = session(f"{code}-3")
        go(s, c["intro"], code)
        go(s, c["no"], code)
        go(s, c["spelled"], code)
        ev, said = go(s, c["no"], code)
        box = next((e for e in ev if e.get("stage") == "typed"), {})
        check(f"{lang} the third time, the box, asked for in the caller's language",
              box.get("field") == "name"
              and said == confirming.say("ask_type_name", code)
              and box.get("label") == confirming.say("box_name", code))
        ev, said = go(s, "this is spoken, not typed", code)
        check(f"{lang} a spoken reply while the box is up does not fill it",
              not ev and s.confirming and s.confirming.stage == "typed")
        ev, said = go(s, f"  {c['typed']}  ", code, typed="name")
        check(f"{lang} what is typed is confirmed exactly, accents and script and all",
              ("name", "done") in stages(ev)
              and next(e["value"] for e in ev if e.get("stage") == "done") == c["typed"])
        go(s, c["says_email"], code)
        go(s, c["yes"], code)
        check(f"{lang} and saved exactly as typed",
              (memory.customer(c["email"]) or {}).get("name") == c["typed"])
        memory.forget(c["email"])

        # 4. The email: the same three tries.
        s = session(f"{code}-4")
        go(s, c["says_email"].replace(c["email"], "wrong.one@example.org"), code)
        ev, said = go(s, c["no"], code)
        check(f"{lang} a no to the email asks for it spelled",
              stages(ev) == [("email", "spell")]
              and said == confirming.say("ask_spell_email", code))
        ev, said = go(s, c["email_spelled"], code)
        check(f"{lang} the spelled email, symbols and all, is read back",
              stages(ev) == [("email", "readback")] and s.confirming.value == c["email"])
        ev, said = go(s, c["no"], code)
        check(f"{lang} a second no to the email opens its box",
              ("email", "typed") in stages(ev)
              and said == confirming.say("ask_type_email", code))
        ev, said = go(s, "not an address", code, typed="email")
        check(f"{lang} something that is not an address is asked for again",
              said == confirming.say("bad_typed_email", code) and s.confirming)
        ev, said = go(s, c["email"], code, typed="email")
        check(f"{lang} a typed address is taken as typed and a code goes out",
              (claimed(ev) or {}).get("status") == "code_sent"
              and memory.customer(c["email"]) is not None)
        memory.forget(c["email"])

    # A returning caller correcting their name, and a demo customer never.
    s = session("returning")
    memory.add_caller("reema.returning@example.org", "Rema", verified=True)
    s.caller = {"email": "reema.returning@example.org", "verified": True,
                "name": "Rema", "calls": [], "order": None, "mismatch": False}
    go(s, "my name is Reema Alotaibi", "en")
    ev, _ = go(s, "yes", "en")
    check("a returning caller's corrected name replaces the saved one",
          memory.customer("reema.returning@example.org")["name"] == "Reema Alotaibi"
          and s.caller["name"] == "Reema Alotaibi")
    s = session("demo")
    s.caller = {"email": "r.patel@example.com", "verified": True,
                "name": "Riya Patel", "calls": [], "order": None, "mismatch": False}
    go(s, "my name is Somebody Else", "en")
    go(s, "yes", "en")
    check("but a demo customer's name is never changed",
          memory.customer("r.patel@example.com")["name"] == "Riya Patel")

    # "It's Patel": only what was said is read back, never her first name.
    s = session("surname")
    ev, said = go(s, "hi, it's Patel", "en")
    check("a surname alone is read back as said, not as the name on file",
          said == confirming.say("readback_name", "en", v="Patel") and "Riya" not in said)
    ev, said = go(s, "yes", "en")
    check("and on a yes, the code goes to her address, as before",
          (claimed(ev) or {}).get("status") == "code_sent")

    # A question in the middle of a read-back is answered, not refused.
    s = session("aside")
    go(s, "my name is Reema Alotaibi", "en")
    out = app.confirmation(s, "wait, how long do refunds usually take?", "en")
    check("a question mid read-back goes to the agent, the read-back still open",
          out is None and s.confirming and s.confirm_aside)

    asyncio.run(through_the_socket(check))

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


async def through_the_socket(check):
    """The same, as the page does it: events, the sentence said, the box."""
    import aiohttp
    from tests.harness import PASSPHRASE, Server

    async def turn(ws, text, typed=None, language=None):
        msg = {"type": "prompt", "text": text, "call": False, "search": False}
        if typed:
            msg["typed_field"] = typed
        if language:
            msg["language"] = language
        await ws.send_json(msg)
        seen = {"confirm": [], "caller": [], "reply": None}
        while True:
            m = await asyncio.wait_for(ws.receive_json(), 40)
            if m["type"] in ("confirm", "caller"):
                seen[m["type"]].append(m)
            if m["type"] in ("done", "error") and m.get("turn_id"):
                seen["reply"] = m.get("reply")
                return seen

    with Server(DEMO_INBOX="1") as s:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            await http.post(f"{s.base}/login", data={"passphrase": PASSPHRASE, "consent": "yes"})
            async with http.ws_connect(f"{s.base}/ws") as ws:
                await ws.receive_json()
                got = await turn(ws, "Hi, my name is Rima")
                check("socket: the read-back is the reply, said without the model",
                      got["reply"] == confirming.say("readback_name", "en", v="Rima"))
                await turn(ws, "no")
                # A spelling that cannot be read as letters is the second
                # miss, so the box comes straight after it.
                got = await turn(ws, "um well it is hard to say really")
                box = [e for e in got["confirm"] if e["stage"] == "typed"]
                check("socket: the box is opened by an event the page acts on",
                      box and box[0]["field"] == "name" and box[0]["label"])
                got = await turn(ws, "  Zoë O'Brien-Núñez ", typed="name")
                check("socket: a typed name comes back exactly, not 'repaired'",
                      [e["value"] for e in got["confirm"] if e["stage"] == "done"]
                      == ["Zoë O'Brien-Núñez"])
                await turn(ws, "my email is zoe.ob@example.org")
                got = await turn(ws, "yes")
                check("socket: then the email, and a code",
                      any(e["status"] == "code_sent" for e in got["caller"]))

            async with http.ws_connect(f"{s.base}/ws") as ws:
                await ws.receive_json()
                await turn(ws, "مرحبا، اسمي ريما", language="ar")
                await turn(ws, "لا", language="ar")
                got = await turn(ws, "كلام غير مفهوم أبدًا هنا", language="ar")
                box = [e for e in got["confirm"] if e["stage"] == "typed"]
                check("socket: in Arabic the box is labelled in Arabic",
                      box and box[0]["label"] == "اكتب اسمك" and box[0]["language"] == "ar")
                got = await turn(ws, "ريم العُتيبي", typed="name", language="ar")
                check("socket: and Arabic script typed into it is kept exactly",
                      [e["value"] for e in got["confirm"] if e["stage"] == "done"]
                      == ["ريم العُتيبي"])


if __name__ == "__main__":
    raise SystemExit(main())
