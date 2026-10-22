"""What the agent remembers about customers between calls.

SQLite rather than a server: it ships with Python, needs nothing installed,
and the whole store is one file next to orders.json. Refreshing the page,
restarting the server or rebooting leaves it alone; only reset() or deleting
the file clears it. Swap this module for a CRM and nothing above it changes,
the same way orders.py stands in for an order API.

Identity is the email on an order, confirmed by the caller. Nobody signs up:
a support line knows you by your order or your number, not by an account.
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata

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


# How long a statement waits for another writer before giving up. One
# process, one lock, short writes — but a container restarting while a
# summary is being written is exactly when two want it at once.
BUSY_SECONDS = float(os.getenv("MEMORY_BUSY_SECONDS", "5"))


def _connect():
    db = sqlite3.connect(PATH, timeout=BUSY_SECONDS)
    db.row_factory = sqlite3.Row
    # Write-ahead logging, so a read never waits behind a write and a crash
    # between the two leaves a recoverable file rather than a corrupt one.
    # Set per connection because it is a property of the file, and setting
    # it is idempotent.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute(f"PRAGMA busy_timeout={int(BUSY_SECONDS * 1000)}")
    db.execute("PRAGMA foreign_keys=ON")
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
    # Columns added after the first databases existed, so they arrive by
    # migration rather than by schema. Adding one is additive and safe;
    # existing rows get NULL and existing history is kept.
    for table, column, kind in (
            ("calls", "unanswered", "TEXT"),
            ("calls", "orders", "TEXT"),
            ("customers", "language", "TEXT"),
            ("customers", "verified", "INTEGER NOT NULL DEFAULT 0"),
            ("customers", "first_seen", "TEXT")):
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    db.executescript("""
        -- One live code per email. Replaced, not accumulated: asking for a
        -- second code invalidates the first, which is what a caller who
        -- says "send it again" expects.
        CREATE TABLE IF NOT EXISTS codes (
            email        TEXT PRIMARY KEY,
            code_hash    TEXT NOT NULL,
            salt         TEXT NOT NULL,
            expires_at   REAL NOT NULL,
            attempts     INTEGER NOT NULL DEFAULT 0,
            locked_until REAL NOT NULL DEFAULT 0
        );
        -- When codes were asked for, so three an hour can be counted. Rows
        -- older than the window are swept rather than kept.
        CREATE TABLE IF NOT EXISTS code_sends (
            email TEXT NOT NULL,
            at    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS code_sends_email ON code_sends(email, at);
    """)

    # Seeded customers are known to be real; everyone created later has to
    # prove it once.
    db.execute("UPDATE customers SET verified = 1 WHERE verified IS NULL")


def _seed(db):
    db.executemany("INSERT OR IGNORE INTO customers (email, name) VALUES (?, ?)",
                   SEED_CUSTOMERS)
    db.executemany(
        "INSERT INTO calls (email, at, summary, outcome, actions, sentiment, seeded) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)", SEED_CALLS)


def init():
    """Create the store on first run and seed it. Existing memory is kept."""
    # MEMORY_FILE points at a mounted volume in a container, and sqlite will
    # not create the directory for you — it says "unable to open database
    # file" and the pod crash-loops on a message that names neither the path
    # nor the reason.
    folder = os.path.dirname(os.path.abspath(PATH))
    if folder and not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            raise SystemExit(
                f"cannot create the folder for MEMORY_FILE ({PATH}): {exc}. "
                "In a container, check the volume is mounted there.")
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
            "SELECT at, summary, outcome, actions, sentiment, unanswered, seeded "
            "FROM calls "
            "WHERE email = ? ORDER BY at DESC, id DESC LIMIT ?",
            (email.lower(), limit)).fetchall()
        return [dict(r) for r in rows]


def save_call(email, at, fields):
    """Store one finished call. `fields` is the parsed post-call write-up."""
    with _lock, _connect() as db:
        db.execute("INSERT OR IGNORE INTO customers (email, name) VALUES (?, ?)",
                   (email.lower(), email.split("@")[0]))
        db.execute(
            "INSERT INTO calls (email, at, summary, outcome, actions, sentiment, "
            "unanswered) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (email.lower(), at, fields.get("summary") or "Call with no summary.",
             fields.get("outcome"), fields.get("actions"), fields.get("sentiment"),
             fields.get("unanswered")))


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


# ============================================================ verification
#
# A name and an email are a claim. Many people share a name and anyone can
# type an address, so nothing personal is said until the caller reads back a
# code sent to that address. The rules below are the whole of the protection
# and each exists for a reason:
#
#   six digits          short enough to say out loud, 1 in a million to guess
#   ten minutes         long enough to find an email, short enough to matter
#   one use             a code read aloud in an open-plan office is spent
#   three attempts      across voice and typing together, not three each
#   fifteen minutes     the lockout, per email, after those three
#   three an hour       so the address cannot be used to send mail repeatedly
#   stored as a hash    the database never holds a live code
#
# The same answer goes back whether or not the address is on file, so this
# cannot be used to find out who is a customer.

CODE_LENGTH = 6
CODE_MINUTES = float(os.getenv("CODE_MINUTES", "10"))
CODE_ATTEMPTS = int(os.getenv("CODE_ATTEMPTS", "3"))
LOCKOUT_MINUTES = float(os.getenv("CODE_LOCKOUT_MINUTES", "15"))
CODES_PER_HOUR = int(os.getenv("CODES_PER_HOUR", "3"))


def _hash(code, salt):
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def make_code(email):
    """A fresh code for this address, or why not.

    Returns (code, reason). Exactly one is set. The caller is told the same
    thing either way — the reason is for the server's own decisions, never
    for the caller.
    """
    email = (email or "").lower().strip()
    if not email:
        return None, "no email"
    now = time.time()
    with _lock, _connect() as db:
        row = db.execute("SELECT locked_until FROM codes WHERE email = ?",
                         (email,)).fetchone()
        if row and row["locked_until"] > now:
            return None, "locked"

        db.execute("DELETE FROM code_sends WHERE at < ?", (now - 3600,))
        sent = db.execute("SELECT COUNT(*) FROM code_sends WHERE email = ?",
                          (email,)).fetchone()[0]
        if sent >= CODES_PER_HOUR:
            return None, "too many"

        code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
        salt = secrets.token_hex(8)
        db.execute(
            "INSERT INTO codes (email, code_hash, salt, expires_at, attempts, "
            "locked_until) VALUES (?, ?, ?, ?, 0, 0) "
            "ON CONFLICT(email) DO UPDATE SET code_hash = excluded.code_hash, "
            "salt = excluded.salt, expires_at = excluded.expires_at, attempts = 0",
            (email, _hash(code, salt), salt, now + CODE_MINUTES * 60))
        db.execute("INSERT INTO code_sends (email, at) VALUES (?, ?)", (email, now))
        return code, None


def check_code(email, given):
    """"ok", "wrong", "expired", "locked" or "none".

    One attempt counter for the address, shared by whatever route the code
    arrived on — saying it and typing it are the same three attempts.
    """
    email = (email or "").lower().strip()
    digits = re.sub(r"\D", "", given or "")
    now = time.time()
    with _lock, _connect() as db:
        row = db.execute(
            "SELECT code_hash, salt, expires_at, attempts, locked_until "
            "FROM codes WHERE email = ?", (email,)).fetchone()
        if not row:
            return "none"
        if row["locked_until"] > now:
            return "locked"
        if row["expires_at"] <= now:
            db.execute("DELETE FROM codes WHERE email = ?", (email,))
            return "expired"

        if hmac.compare_digest(row["code_hash"], _hash(digits, row["salt"])):
            # Single use: gone the moment it works.
            db.execute("DELETE FROM codes WHERE email = ?", (email,))
            db.execute("UPDATE customers SET verified = 1 WHERE email = ?", (email,))
            return "ok"

        attempts = row["attempts"] + 1
        if attempts >= CODE_ATTEMPTS:
            db.execute(
                "UPDATE codes SET attempts = ?, locked_until = ?, "
                "code_hash = '', expires_at = 0 WHERE email = ?",
                (attempts, now + LOCKOUT_MINUTES * 60, email))
            return "locked"
        db.execute("UPDATE codes SET attempts = ? WHERE email = ?", (attempts, email))
        return "wrong"


def attempts_left(email):
    """For the page, so it can say how many tries remain."""
    with _lock, _connect() as db:
        row = db.execute("SELECT attempts, locked_until FROM codes WHERE email = ?",
                         ((email or "").lower().strip(),)).fetchone()
    if not row or row["locked_until"] > time.time():
        return 0
    return max(0, CODE_ATTEMPTS - row["attempts"])


def forget(email):
    """Everything held against one address, gone. Returns what was removed."""
    email = (email or "").lower().strip()
    with _lock, _connect() as db:
        calls = db.execute("SELECT COUNT(*) FROM calls WHERE email = ?",
                           (email,)).fetchone()[0]
        known = db.execute("SELECT COUNT(*) FROM customers WHERE email = ?",
                           (email,)).fetchone()[0]
        db.execute("DELETE FROM calls WHERE email = ?", (email,))
        db.execute("DELETE FROM codes WHERE email = ?", (email,))
        db.execute("DELETE FROM code_sends WHERE email = ?", (email,))
        db.execute("DELETE FROM customers WHERE email = ?", (email,))
    return {"calls": calls, "customer": bool(known)}


def purge_old(days=None):
    """Drop calls past the retention period, and customers left with none."""
    days = float(os.getenv("RETENTION_DAYS", "30") if days is None else days)
    cutoff = time.strftime("%Y-%m-%d %H:%M",
                           time.gmtime(time.time() - days * 86400))
    with _lock, _connect() as db:
        gone = db.execute("DELETE FROM calls WHERE at < ? AND seeded = 0",
                          (cutoff,)).rowcount
        orphans = db.execute(
            "DELETE FROM customers WHERE verified = 0 AND email NOT IN "
            "(SELECT email FROM calls)").rowcount
        db.execute("DELETE FROM code_sends WHERE at < ?", (time.time() - 3600,))
    return {"calls": gone, "customers": orphans}


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
