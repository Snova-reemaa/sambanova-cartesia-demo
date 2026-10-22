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

    # Three attempts, shared, then locked — and the lock beats a right code.
    m4 = fresh()
    dan = "a.novak@example.com"
    code4, _ = m4.make_code(dan)
    check("first wrong is just wrong", m4.check_code(dan, "000000") == "wrong")
    check("two left", m4.attempts_left(dan) == 2)
    check("second wrong is just wrong", m4.check_code(dan, "111111") == "wrong")
    check("third wrong locks it", m4.check_code(dan, "222222") == "locked")
    check("the right code is refused while locked",
          m4.check_code(dan, code4) == "locked")
    check("and a new code cannot be sent while locked",
          m4.make_code(dan) == (None, "locked"))

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

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
