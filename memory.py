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


def find_email(text):
    if not text:
        return None
    found = EMAIL.search(text)
    if found:
        return found.group(0).lower().rstrip(".")
    spoken = " " + text.lower() + " "
    for pattern, repl in SPOKEN:
        spoken = re.sub(pattern, repl, spoken)
    found = EMAIL.search(spoken.replace(" @", "@").replace("@ ", "@"))
    if found:
        return found.group(0).lower().rstrip(".")
    return None


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
    for said in ("it's r.patel@example.com", "r dot patel at example dot com",
                 "my email is s dot bergstrom at example dot com thanks", "no idea"):
        print(f"  {said!r:52} -> {find_email(said)}")
    print()
    print(as_context("Riya Patel", history("r.patel@example.com")))
