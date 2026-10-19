"""What the agent remembers about customers between calls.

SQLite rather than a server: it ships with Python, needs nothing installed,
and the whole store is one file next to orders.json. Refreshing the page,
restarting the server or rebooting leaves it alone; only reset() or deleting
the file clears it. Swap this module for a CRM and nothing above it changes,
the same way orders.py stands in for an order API.

Identity is the email on an order, confirmed by the caller. Nobody signs up:
a support line knows you by your order or your number, not by an account.
"""

import os
import re
import sqlite3
import unicodedata
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.getenv("MEMORY_FILE") or os.path.join(HERE, "memory.sqlite3")

_lock = threading.Lock()

# A few customers with history already in place, so the demo can show being
# remembered without first having to make a call to remember.
SEED_CUSTOMERS = [
    ("r.patel@example.com", "Riya Patel"),
    ("j.okafor@example.com", "James Okafor"),
    ("s.bergstrom@example.com", "Sofia Bergström"),
    ("m.lindqvist@example.com", "Maja Lindqvist"),
    ("a.novak@example.com", "Adam Novak"),
    ("d.ferreira@example.com", "Diego Ferreira"),
    ("l.haddad@example.com", "Layla Haddad"),
    ("k.yamamoto@example.com", "Kenji Yamamoto"),
]

SEED_CALLS = [
    ("r.patel@example.com", "2026-09-28 14:10",
     "Asked where order 44812, the Aurex X3 headphones, had got to.",
     "pending",
     "An update on the delivery within 2 working days.",
     "patient but tired of waiting"),
    ("r.patel@example.com", "2026-08-02 10:45",
     "Asked whether the Aurex X3 works with an Android phone.",
     "resolved", "none", "friendly"),
    ("j.okafor@example.com", "2026-09-06 09:20",
     "Reported the Kelo travel kettle from order 44815 leaks at the base.",
     "pending",
     "Sent a returns link by email; customer to post the kettle back.",
     "annoyed"),
    ("l.haddad@example.com", "2026-08-12 16:05",
     "Cancelled order 44688, the Marlo floor lamp, because it would not arrive in time.",
     "resolved", "Refund issued to the original card.", "calm"),
]


def _connect():
    db = sqlite3.connect(PATH, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def _create(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            email TEXT PRIMARY KEY,
            name  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calls (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            email     TEXT NOT NULL REFERENCES customers(email),
            at        TEXT NOT NULL,
            summary   TEXT NOT NULL,
            outcome   TEXT,
            actions   TEXT,
            sentiment TEXT,
            seeded    INTEGER NOT NULL DEFAULT 0
        );
    """)


def _seed(db):
    db.executemany("INSERT OR IGNORE INTO customers (email, name) VALUES (?, ?)",
                   SEED_CUSTOMERS)
    db.executemany(
        "INSERT INTO calls (email, at, summary, outcome, actions, sentiment, seeded) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)", SEED_CALLS)


def init():
    """Create the store on first run and seed it. Existing memory is kept."""
    with _lock, _connect() as db:
        _create(db)
        if not db.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
            _seed(db)


def reset():
    """Back to the seeded state, so 'it remembered me' works fresh for every
    audience."""
    with _lock, _connect() as db:
        db.executescript("DROP TABLE IF EXISTS calls; DROP TABLE IF EXISTS customers;")
        _create(db)
        _seed(db)


def customer(email):
    if not email:
        return None
    with _lock, _connect() as db:
        row = db.execute("SELECT email, name FROM customers WHERE email = ?",
                         (email.lower(),)).fetchone()
        return dict(row) if row else None


def history(email, limit=20):
    """Past calls, most recent first."""
    with _lock, _connect() as db:
        rows = db.execute(
            "SELECT at, summary, outcome, actions, sentiment, seeded FROM calls "
            "WHERE email = ? ORDER BY at DESC, id DESC LIMIT ?",
            (email.lower(), limit)).fetchall()
        return [dict(r) for r in rows]


def save_call(email, at, fields):
    """Store one finished call. `fields` is the parsed post-call write-up."""
    with _lock, _connect() as db:
        db.execute("INSERT OR IGNORE INTO customers (email, name) VALUES (?, ?)",
                   (email.lower(), email.split("@")[0]))
        db.execute(
            "INSERT INTO calls (email, at, summary, outcome, actions, sentiment) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (email.lower(), at, fields.get("summary") or "Call with no summary.",
             fields.get("outcome"), fields.get("actions"), fields.get("sentiment")))


def as_context(name, calls):
    """The customer's history, written for the model rather than for a report."""
    if not calls:
        return f"The caller is {name}. This is their first call."
    lines = [f"The caller is {name}, verified. Their previous calls, most recent first:"]
    for c in calls:
        agreed = (c.get("actions") or "none").rstrip(".")
        outcome = (c.get("outcome") or "unknown").rstrip(".")
        lines.append(f"- {c['at'][:10]}: {c['summary']} Outcome: {outcome}. "
                     f"Agreed actions: {agreed}. They sounded {c.get('sentiment') or 'neutral'}.")
    return "\n".join(lines)


# ------------------------------------------------------------------ emails

EMAIL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)

# Speech recognition writes "r dot patel at example dot com", or drops the
# dots altogether. Turn the spoken form back into an address.
SPOKEN = (
    (r"\s+(?:at|@)\s+", "@"),
    (r"\s+(?:dot|point)\s+", "."),
    (r"\s+(?:underscore)\s+", "_"),
    (r"\s+(?:dash|hyphen)\s+", "-"),
)


# A letter said out loud and what the recogniser writes down instead. Reading
# "r.patel@example.com" aloud gives "our dot patel at example dot com" — "r"
# is a letter, "our" is a word, and the recogniser picks the word every time.
# Addresses here are nearly all a single initial and a surname, so the initial
# is the character most often spoken on its own and most often lost.
LETTER_WORDS = {
    "a": "a", "ay": "a", "eh": "a",
    "b": "b", "bee": "b", "be": "b",
    "c": "c", "see": "c", "sea": "c", "cee": "c",
    "d": "d", "dee": "d",
    "e": "e", "ee": "e",
    "f": "f", "ef": "f", "eff": "f",
    "g": "g", "gee": "g", "jee": "g",
    "h": "h", "aitch": "h", "haitch": "h",
    "i": "i", "eye": "i", "aye": "i",
    "j": "j", "jay": "j",
    "k": "k", "kay": "k", "cay": "k",
    "l": "l", "el": "l", "ell": "l",
    "m": "m", "em": "m", "emm": "m",
    "n": "n", "en": "n", "enn": "n",
    "o": "o", "oh": "o", "owe": "o",
    "p": "p", "pee": "p", "pea": "p",
    "q": "q", "cue": "q", "queue": "q",
    "r": "r", "are": "r", "our": "r", "ar": "r", "arr": "r", "or": "r",
    "ore": "r", "oar": "r", "err": "r",
    "s": "s", "ess": "s", "es": "s",
    "t": "t", "tee": "t", "tea": "t",
    "u": "u", "you": "u", "yoo": "u", "ewe": "u",
    "v": "v", "vee": "v",
    "w": "w", "dubya": "w",
    "x": "x", "ex": "x", "eks": "x",
    "y": "y", "why": "y", "wye": "y",
    "z": "z", "zed": "z", "zee": "z",
}

SPLIT = re.compile(r"([.@_\-])")


def spell_out(address):
    """Turn any part of an address that is the name of a letter into it.

    Only the pieces between the dots and the at sign are considered, so an
    ordinary word in a surname is never touched — "sea" is only read as "c"
    where it stands alone as a whole segment.
    """
    parts = SPLIT.split(address)
    return "".join(LETTER_WORDS.get(bit, bit) if i % 2 == 0 else bit
                   for i, bit in enumerate(parts))


def _known():
    """The addresses on the order book, if there is one. Imported lazily so
    this module stays usable without it."""
    try:
        import orders
    except Exception:
        return frozenset()
    return frozenset(
        (o.get("email") or "").lower() for o in orders.ORDERS.values() if o.get("email"))


def find_email(text):
    """The address the caller gave, spoken or typed.

    A candidate that matches a real customer always wins, which is what makes
    the letter reading safe: it is only preferred when it lands on someone.
    """
    if not text:
        return None
    candidates = []
    found = EMAIL.search(text)
    if found:
        candidates.append(found.group(0).lower().rstrip("."))
    spoken = " " + text.lower() + " "
    for pattern, repl in SPOKEN:
        spoken = re.sub(pattern, repl, spoken)
    found = EMAIL.search(spoken.replace(" @", "@").replace("@ ", "@"))
    if found:
        candidates.append(found.group(0).lower().rstrip("."))
    if not candidates:
        return None

    known = _known()
    spelled = [spell_out(c) for c in candidates]
    for candidate in candidates + spelled:
        if candidate in known:
            return candidate
    # Nobody on the books. Keep what they actually said rather than reading
    # letters into a surname that happens to sound like one.
    return candidates[0]


def fold(word):
    """Bergström and bergstrom are the same person. Speech recognition writes
    plain ASCII whatever the spelling on the account says."""
    stripped = unicodedata.normalize("NFD", (word or "").lower())
    return "".join(c for c in stripped if not unicodedata.combining(c))


def _people():
    """Everyone on the books, as (email, first, last), accents folded."""
    out = []
    with _lock, _connect() as db:
        for row in db.execute("SELECT email, name FROM customers"):
            parts = fold(row["name"] or "").split()
            out.append((row["email"].lower(), parts[0] if parts else "",
                        parts[-1] if parts else ""))
    return out


def find_customer(text):
    """Who is on the phone, from an address or just from their name.

    Spelling an address down a microphone is miserable — "R" comes back as
    "or", so the caller says it again, and again, and the transcript reads
    "or or or or or". A surname is a word, which is the one thing speech
    recognition is good at, and these are unique. "It's Patel" is enough.
    """
    email = find_email(text)
    if email:
        return email
    words = set(re.findall(r"[a-z']+", fold(text)))
    if not words:
        return None
    hits = {e for e, first, last in _people()
            if (last and last in words) or (first and first in words)}
    return hits.pop() if len(hits) == 1 else None


def mask(email):
    """r.patel@example.com -> r•••••l@example.com, for the screen."""
    if not email or "@" not in email:
        return email
    user, domain = email.split("@", 1)
    if len(user) <= 2:
        return f"{user[0]}•@{domain}"
    return f"{user[0]}{'•' * (len(user) - 2)}{user[-1]}@{domain}"


if __name__ == "__main__":
    init()
    # What the recogniser writes when an address is read out. The initial is
    # the character most often spoken alone and the one it most often loses:
    # "r" comes back as "our", "s" as "ess", "a" as "eh".
    HEARD = [
        ("it's r.patel@example.com", "r.patel@example.com"),
        ("r dot patel at example dot com", "r.patel@example.com"),
        ("our dot patel at example dot com", "r.patel@example.com"),
        ("are dot patel at example dot com", "r.patel@example.com"),
        ("its our dot patel at example dot com thanks", "r.patel@example.com"),
        ("my email is s dot bergstrom at example dot com thanks", "s.bergstrom@example.com"),
        ("ess dot bergstrom at example dot com", "s.bergstrom@example.com"),
        ("jay dot okafor at example dot com", "j.okafor@example.com"),
        ("kay dot yamamoto at example dot com", "k.yamamoto@example.com"),
        ("eh dot novak at example dot com", "a.novak@example.com"),
        ("em dot lindqvist at example dot com", "m.lindqvist@example.com"),
        ("el dot haddad at example dot com", "l.haddad@example.com"),
        ("dee dot ferreira at example dot com", "d.ferreira@example.com"),
        # Not on the order book, so it is left exactly as spoken rather than
        # having "sea" read as the letter c.
        ("sea.breeze@somewhere.com", "sea.breeze@somewhere.com"),
        ("no idea", None),
        ("our team will call you back", None),
        ("can you see the order", None),
    ]
    wrong = 0
    for said, want in HEARD:
        got = find_email(said)
        wrong += got != want
        print(f"  {'ok  ' if got == want else 'FAIL'} {str(got):26} {said[:46]}")
    print(f"\n  {len(HEARD) - wrong}/{len(HEARD)} heard correctly")
    print()
    print(as_context("Riya Patel", history("r.patel@example.com")))
