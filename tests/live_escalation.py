"""The front agent answers what it can and passes on what it cannot.

A needless transfer is not free: it ends the turn, spawns a second one for
the specialist's introduction, puts a new voice in the caller's ear, and
usually asks for something the caller was about to be asked for anyway.
Refusing a transfer somebody needs is worse.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.live import Caller, run                             # noqa: E402

# (question, should a specialist take this?)
CASES = [
    ("How do I return a product?", False),
    ("What are your opening hours?", False),
    ("Where is my order 44812?", False),
    ("Do you deliver to Scotland?", False),
    ("How long does a refund usually take?", False),
    ("What's the warranty on your monitors?", False),
    ("Can I change the delivery address on an order?", False),
    ("My order is a week late, what happens now?", False),
    ("Hi there, are you open?", False),
    ("Do you price match with other retailers?", False),
    ("Can I return something after six weeks?", False),
    ("I've been charged twice for the same order.", True),
    ("There's a payment on my card from you I don't recognise.", True),
    ("My refund was approved three weeks ago and still hasn't reached my account.", True),
    ("The screen flickers and shows error E04 every time I turn it on.", True),
    ("I can't get the speaker to pair with my phone, I've tried everything.", True),
    ("The laptop won't boot past the logo screen.", True),
]


async def cases(http, server):
    results = []
    for ask, should_move in CASES:
        # A fresh socket each time: a transfer changes who is on the call.
        async with Caller(http, server) as caller:
            turn = await caller.say(ask)
        moved = turn["team"] is not None
        where = f"-> {turn['team']}" if moved else "kept"
        results.append((moved == should_move, f"{where:14} {ask[:54]}"))
    return results


if __name__ == "__main__":
    raise SystemExit(run(cases))
