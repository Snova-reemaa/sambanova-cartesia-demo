"""The one-time code, and the rules that make it worth anything.

A name and an email are a claim: many people share a name and anyone can
type an address. Nothing personal is said until a code sent to that address
is read back. These are the rules that make that true, each checked against
the thing it is meant to prevent.

Offline, and fast — no server and no provider. It runs against its own
database in /tmp so it can never touch a real one.
"""

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def fresh(**settings):
    """A memory module with its own empty database and its own settings."""
    for key, value in settings.items():
        os.environ[key] = str(value)
    path = os.path.join(tempfile.mkdtemp(prefix="voxnova-codes-"), "m.sqlite3")
    os.environ["MEMORY_FILE"] = path
    for name in [n for n in list(sys.modules) if n == "memory"]:
        del sys.modules[name]
    import memory
    memory.init()
    return memory


def main():
    results = []

    def check(what, ok):
        results.append((what, bool(ok)))

    m = fresh()
    alice = "r.patel@example.com"

    code, why = m.make_code(alice)
    check("a code is six digits", code and len(code) == 6 and code.isdigit())
    check("nothing refused it", why is None)
    check("two codes in a row differ", m.make_code(alice)[0] != code)

    # The database must never hold a code anyone could read.
    m2 = fresh()
    bob = "j.okafor@example.com"
    code2, _ = m2.make_code(bob)
    with m2._connect() as db:
        stored = db.execute("SELECT code_hash, salt FROM codes WHERE email = ?",
                            (bob,)).fetchone()
    check("the code itself is not stored", code2 not in stored["code_hash"])
    check("it is stored salted", len(stored["salt"]) >= 8)

    check("the right code verifies", m2.check_code(bob, code2) == "ok")
    check("and the customer is marked verified",
          m2.customer(bob) and m2._connect().execute(
              "SELECT verified FROM customers WHERE email = ?", (bob,)).fetchone()[0] == 1)
    check("the same code a second time is refused",
          m2.check_code(bob, code2) == "none")

    # Spoken forms. A code read aloud arrives with spaces, dashes or words
    # around it; the digits are what matter.
    m3 = fresh()
    carol = "s.bergstrom@example.com"
    code3, _ = m3.make_code(carol)
    spoken = f"it's {' '.join(code3)}, I think"
    check("a code read out loud, spaced and padded, still verifies",
          m3.check_code(carol, spoken) == "ok")

    # Three attempts shared between however the code arrives — saying it and
    # typing it are the same three, which is what the caller key is for.
    m4 = fresh()
    dan = "a.novak@example.com"
    me = "one-session"
    code4, _ = m4.make_code(dan, caller=me)
    check("first wrong is just wrong",
          m4.check_code(dan, "000000", caller=me) == "wrong")
    check("two left", m4.attempts_left(dan, caller=me) == 2)
    check("second wrong is just wrong",
          m4.check_code(dan, "111111", caller=me) == "wrong")
    check("the third locks the caller",
          m4.check_code(dan, "222222", caller=me) == "locked")
    check("the right code is refused while they are locked",
          m4.check_code(dan, code4, caller=me) == "locked")
    check("and they cannot ask for another",
          m4.make_code(dan, caller=me) == (None, "caller locked"))

    # With no caller given there is no lockout to apply, so the protection
    # has to come from the code itself: three wrong guesses and it is gone.
    # Six digits with unlimited guesses would be no protection at all.
    m4b = fresh()
    code4b, _ = m4b.make_code(dan)
    for guess in ("000000", "111111", "222222"):
        m4b.check_code(dan, guess)
    check("without a caller, the code is spent after three guesses",
          m4b.check_code(dan, code4b) == "none")

    # Expiry.
    m5 = fresh(CODE_MINUTES=1 / 60)          # one second
    eve = "m.lindqvist@example.com"
    code5, _ = m5.make_code(eve)
    time.sleep(1.4)
    check("an expired code is refused", m5.check_code(eve, code5) == "expired")

    # Three an hour.
    m6 = fresh()
    frank = "k.yamamoto@example.com"
    outs = [m6.make_code(frank)[1] for _ in range(4)]
    check("three codes an hour, the fourth refused",
          outs[:3] == [None, None, None] and outs[3] == "too many")

    # A code for one address never opens another.
    m7 = fresh()
    one, two = "l.haddad@example.com", "d.ferreira@example.com"
    code_one, _ = m7.make_code(one)
    m7.make_code(two)
    check("a code for one address does not verify another",
          m7.check_code(two, code_one) in ("wrong", "none"))

    # Denial of service against a real customer. Failing three times on
    # somebody else's address must cost the person doing it, not the person
    # whose address it is.
    m10 = fresh()
    victim = "r.patel@example.com"
    attacker, real = "session-attacker", "session-the-actual-customer"
    m10.make_code(victim, caller=attacker)
    worst = [m10.check_code(victim, "000000", caller=attacker) for _ in range(3)]
    check("three wrong guesses lock the guesser", worst[-1] == "locked")
    check("the guesser cannot ask for another code",
          m10.make_code(victim, caller=attacker)[1] == "caller locked")
    code10, why10 = m10.make_code(victim, caller=real)
    check("the real customer is untouched — still gets a code", bool(code10))
    check("and it still verifies them",
          m10.check_code(victim, code10, caller=real) == "ok")

    # Spraying codes at many addresses. One caller, many targets.
    m11 = fresh()
    sprayer = "session-sprayer"
    everyone = ["a.novak@example.com", "j.okafor@example.com",
                "k.yamamoto@example.com", "l.haddad@example.com",
                "d.ferreira@example.com", "m.lindqvist@example.com",
                "s.bergstrom@example.com"]
    reasons = [m11.make_code(who, caller=sprayer)[1] for who in everyone]
    check("one caller is capped across all addresses",
          reasons[-1] == "caller asked too often")

    # And an address nobody has must never produce a code, or this becomes
    # a way to send mail to strangers the day SMTP is turned on.
    m12 = fresh()
    stranger, why12 = m12.make_code("nobody@elsewhere.example", caller="s")
    check("no code is made for an unknown address", stranger is None)
    check("and the reason is kept server-side, not told to the caller",
          why12 == "unknown")

    # Forgetting.
    m8 = fresh()
    gone = "r.patel@example.com"
    m8.save_call(gone, "2026-10-01 10:00", {"summary": "about an order"})
    before = len(m8.history(gone))
    removed = m8.forget(gone)
    check("forgetting removes the calls", before and not m8.history(gone))
    check("and says what it removed", removed["calls"] >= 1)
    check("and the customer with them", m8.customer(gone) is None)
    other = m8.history("j.okafor@example.com")
    check("and leaves everybody else alone", len(other) >= 1)

    # The demo customers are fixtures, not callers who have to prove
    # themselves, and retention deletes any customer left unverified with no
    # calls. Seeding them unverified means a fresh deployment quietly loses
    # the five who have no seeded call the first time retention runs. It was
    # invisible in development because the column arrived by migration: an
    # older database got NULL and a now-dead UPDATE caught it, while a new
    # one gets the column's DEFAULT 0 and nothing catches it.
    m10 = fresh()
    with m10._connect() as db:
        unproven = [row[0] for row in db.execute(
            "SELECT email FROM customers WHERE verified = 0")]
    check("seeded customers start out verified", not unproven)
    kept = m10.purge_old(days=30)
    check("so retention does not sweep them away",
          kept["customers"] == 0 and m10.customer("a.novak@example.com"))

    # Retention.
    m9 = fresh()
    old_email = "a.novak@example.com"
    m9.save_call(old_email, "2020-01-01 09:00", {"summary": "ancient"})
    m9.save_call(old_email, time.strftime("%Y-%m-%d %H:%M"), {"summary": "today"})
    swept = m9.purge_old(days=30)
    left = [c["summary"] for c in m9.history(old_email)]
    check("retention drops the old call", "ancient" not in left)
    check("and keeps the recent one", "today" in left)
    check("and says how many it dropped", swept["calls"] >= 1)

    # The caller key must not be forgeable. rke2-ingress-nginx runs with
    # use-forwarded-headers=true, so it appends the peer it saw to
    # whatever the client sent: the trustworthy entry is the rightmost,
    # and reading the leftmost — which this did at first — lets a client
    # mint a new identity per request and makes every limit decorative.
    import ipaddress                                           # noqa: F401
    import server as app

    class FakeRequest:
        def __init__(self, peer, forwarded=None, token="a-session"):
            self.remote = peer
            self.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
            self.cookies = {app.COOKIE_NAME: token}

    was_networks = app.TRUSTED_NETWORKS
    try:
        app.TRUSTED_NETWORKS = (ipaddress.ip_network("100.64.0.0/12"),)

        check("behind the ingress, the appended address is the caller",
              app.client_ip(FakeRequest("100.64.3.7", "198.51.100.5"))
              == "198.51.100.5")
        check("a spoofed entry in front of it is ignored",
              app.client_ip(FakeRequest("100.64.3.7", "1.2.3.4, 198.51.100.5"))
              == "198.51.100.5")
        check("so is a whole spoofed chain",
              app.client_ip(FakeRequest(
                  "100.64.3.7", "9.9.9.9, 8.8.8.8, 203.0.113.77"))
              == "203.0.113.77")
        check("and claiming to be the ingress does not help",
              app.client_ip(FakeRequest(
                  "100.64.3.7", "100.64.0.1, 203.0.113.77")) == "203.0.113.77")
        check("a direct connection ignores the header entirely",
              app.client_ip(FakeRequest("198.51.100.5", "1.2.3.4"))
              == "198.51.100.5")

        # The point of all of it: one caller, one key, whatever they send.
        forged = {app.caller_key(FakeRequest("100.64.3.7", xff))
                  for xff in ("198.51.100.5", "1.2.3.4, 198.51.100.5",
                              "7.7.7.7, 8.8.8.8, 198.51.100.5",
                              "100.64.9.9, 198.51.100.5")}
        check("one caller keeps one rate-limit key however they spoof",
              len(forged) == 1)
        # And two real callers are still told apart.
        check("two different callers still get different keys",
              app.caller_key(FakeRequest("100.64.3.7", "198.51.100.5"))
              != app.caller_key(FakeRequest("100.64.3.7", "203.0.113.9")))
    finally:
        app.TRUSTED_NETWORKS = was_networks

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
