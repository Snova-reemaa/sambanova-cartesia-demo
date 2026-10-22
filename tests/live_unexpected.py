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
    ("trap", "My laptop got wet. It's under warranty so you'll fix it free?",
     ["liquid", "water", "accidental", "not covered", "isn't covered"]),
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

# A figure in a reply that no document supports is an invention. One that
# is in the documents is the agent doing its job — "a full refund at any
# point within 12 months" was being counted as a fabrication when it is
# written down in warranty.md, which flattered nothing and hid nothing but
# did send me looking for a bug that was not there.
FIGURE = re.compile(
    r"£\s?\d+(?:\.\d+)?|\b\d+\s?(?:%|percent|days?|months?|years?|hours?|working days?)\b",
    re.I)
NUMBER = re.compile(r"\d+")


def _documented_numbers():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder = os.path.join(root, "knowledge")
    found = set()
    for name in sorted(os.listdir(folder)):
        if name.endswith((".md", ".txt")):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                found |= set(NUMBER.findall(handle.read()))
    # Spoken aloud, so the words count as the same fact.
    found |= {"one", "two", "three", "four", "five", "six", "seven", "eight",
              "nine", "ten", "twelve", "fourteen", "thirty"}
    return found


DOCUMENTED = _documented_numbers()


def invented_figure(reply):
    """A figure in the reply that appears nowhere in the documents."""
    for hit in FIGURE.findall(reply):
        digits = NUMBER.findall(hit)
        if digits and not all(d in DOCUMENTED for d in digits):
            return hit
    return None
# Honest: says it does not have it, or offers the colleague who would.
HONEST = ["don't have", "do not have", "haven't got", "not something i have",
          "don't hold", "no information", "not in", "can't confirm",
          "cannot confirm", "don't know", "do not know", "not sure",
          "put you through", "transfer", "pass you", "colleague", "team"]
# A promise nobody keeps. There is no queue, no ticket and no callback, so
# "I'll find out and come back to you" is a thing that will never happen.
EMPTY_PROMISE = ["find out", "find that out", "come back to you",
                 "get back to you", "look into it and", "let you know"]


def verdict(kind, want, turn):
    reply = (turn["reply"] or "")
    low = reply.lower()
    honest = any(h in low for h in HONEST) or turn["team"]
    promised = any(p in low for p in EMPTY_PROMISE)
    hedged = honest or promised
    if turn["error"]:
        return False, f"ERROR {turn['error'][:40]}"
    if kind in ("fact", "trap"):
        if any(w.lower() in low for w in want):
            return True, "answered"
        return False, "MISSED — the documents say so" if hedged else "WRONG"
    if kind == "refuse":
        made_up = invented_figure(reply)
        if made_up and not hedged:
            return False, f"INVENTED a figure: {made_up}"
        if promised and not honest:
            return False, "PROMISED to come back — nobody will"
        if not honest:
            return False, "neither said it does not know nor offered a transfer"
        return True, "declined honestly"
    made_up = invented_figure(reply)
    if made_up and not hedged:
        return False, f"INVENTED a figure: {made_up}"
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
