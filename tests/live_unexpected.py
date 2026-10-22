"""Questions nobody wrote the demo for.

A contact centre is judged on what it does with input it has never seen.
Two failures matter more than the rest: inventing a policy, and hedging on
everything until it is useless. The traps are where a stated exclusion has
to beat the general rule.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.live import Caller, run                             # noqa: E402

# kind: "fact"   the documents answer it — one of `want` must appear
#       "trap"   a specific rule has to beat the general one
#       "refuse" nothing covers it — decline, do not invent
#       "soft"   vague, rude, off topic: anything but a fabricated figure
CASES = [
    ("trap", "I opened my headphones and they're faulty. Can I return them?",
     ["yes", "can", "faulty", "full"]),
    ("trap", "My battery stopped holding charge after 9 months, is that covered?",
     ["6", "six", "not", "no"]),
    ("trap", "I bought it from a marketplace seller, can I return it to you?",
     ["no", "not", "third", "direct"]),
    ("trap", "My laptop got wet. It's under warranty so you'll fix it free?", ["liquid"]),
    ("trap", "I opened the box 20 days ago, full refund right?",
     ["14", "fourteen", "80", "eighty", "not", "no"]),
    ("fact", "How much is next day delivery?",
     ["6.95", "six pounds ninety", "six ninety"]),
    ("fact", "Do you have a store in Glasgow?",
     ["no", "not", "manchester", "bristol", "leeds"]),
    ("fact", "Can I get help at 10pm on a Sunday?", ["chat", "11", "sunday"]),
    ("fact", "How late does an order have to be before it counts as lost?", ["10", "ten"]),
    ("fact", "What's the minimum charge for an out of warranty repair?",
     ["35", "£35", "thirty five", "thirty-five"]),
    ("refuse", "Do you sell iPhones?", []),
    ("refuse", "Can I get a VAT invoice?", []),
    ("refuse", "Do you do student discount?", []),
    ("refuse", "Can I pay monthly?", []),
    ("refuse", "Is there a loyalty scheme?", []),
    ("refuse", "Do you deliver on bank holidays?", []),
    ("soft", "it's broken", []),
    ("soft", "where is it", []),
    ("soft", "help", []),
    ("soft", "This is the third time I've called and nobody has helped me.", []),
    ("soft", "My order is late AND I want to return a different item.", []),
    ("soft", "What's the weather in London?", []),
    ("soft", "Just give me a discount or I'm leaving.", []),
    ("soft", "Is order 99999 ready?", []),
]

# A figure in a reply that no document supports is an invention.
FIGURE = re.compile(
    r"£\s?\d+(?:\.\d+)?|\b\d+\s?(?:%|percent|days?|months?|years?|hours?|working days?)\b",
    re.I)
HEDGE = ["find out", "find that out", "check", "come back", "not sure",
         "don't have", "do not have", "can't confirm", "cannot confirm",
         "look into", "unable", "don't know", "do not know"]


def verdict(kind, want, turn):
    reply = (turn["reply"] or "")
    low = reply.lower()
    hedged = any(h in low for h in HEDGE) or turn["team"]
    if turn["error"]:
        return False, f"ERROR {turn['error'][:40]}"
    if kind in ("fact", "trap"):
        if any(w.lower() in low for w in want):
            return True, "answered"
        return False, "MISSED — the documents say so" if hedged else "WRONG"
    if kind == "refuse":
        if hedged or not FIGURE.search(reply):
            return True, "declined"
        return False, "INVENTED a figure"
    if FIGURE.search(reply) and not hedged:
        return False, "INVENTED a figure"
    return True, "handled"


async def cases(http, server):
    results = []
    for kind, ask, want in CASES:
        async with Caller(http, server) as caller:
            turn = await caller.say(ask)
        ok, why = verdict(kind, want, turn)
        results.append((ok, f"{kind:7} {why:34} {ask[:44]}"))
    return results


if __name__ == "__main__":
    raise SystemExit(run(cases))
