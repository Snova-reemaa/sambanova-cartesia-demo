"""Order lookup.

A JSON file standing in for a CRM. The point is not the storage — it is that
the agent stops saying "I'm checking on the status" and then inventing one.
Swap `look_up` for a real API call and nothing above it changes.

It also computes how late an order is, because that is the question actually
being asked, and because the policy documents turn on working days: late at 2,
treated as lost at 10. Making the agent derive that from two dates mid-sentence
is how you get it wrong out loud.
"""

import json
import os
import re
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.getenv("ORDERS_FILE", os.path.join(HERE, "orders.json"))

# The demo data is fixed, so "today" is pinned to keep the lateness arithmetic
# stable. Unset it to use the real date against a real CRM.
TODAY = os.getenv("ORDERS_TODAY", "2026-10-08")


def _today():
    try:
        return datetime.strptime(TODAY, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


def load():
    try:
        with open(PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


ORDERS = load()


# Dictating a number is the one thing every caller does and the one thing
# browser speech recognition is worst at. "Four four eight one two" comes back
# as "for for eight one to": every short digit word has a commoner homophone,
# and the recogniser picks the commoner one. The docstring here used to claim
# this was handled; nothing in the code did, so reading an order number aloud
# never once found an order.
SPOKEN = {
    "zero": "0", "oh": "0", "nought": "0", "nil": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

# What the recogniser writes instead. Only ever read as digits next to another
# number — "for" is far too common a word to convert on sight.
HOMOPHONES = {
    "for": "4", "fore": "4", "ford": "4",
    "to": "2", "too": "2", "tu": "2",
    "ate": "8", "eat": "8", "late": "8",
    "won": "1", "wan": "1",
    "tree": "3", "free": "3", "thee": "3",
    "fife": "5", "hive": "5",
    "sicks": "6", "sics": "6",
    "niner": "9", "nein": "9",
    "o": "0", "owe": "0",
}

TEENS = {"ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
         "fourteen": "14", "fifteen": "15", "sixteen": "16",
         "seventeen": "17", "eighteen": "18", "nineteen": "19"}

TENS = {"twenty": "2", "thirty": "3", "forty": "4", "fourty": "4",
        "fifty": "5", "sixty": "6", "seventy": "7", "eighty": "8",
        "ninety": "9"}

REPEATS = {"double": 2, "triple": 3, "treble": 3}

ORDER_LENGTH = 5


def _spoken_digits(words):
    """Turn a run of words into the digits they stand for, or None.

    Returns (digits, used_homophone). Everything here is one token to one or
    two digits — "forty four" is 44, "twelve" is 12, "double four" is 44.
    """
    out = []
    homophone = False
    i = 0
    while i < len(words):
        word = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else None
        if word in REPEATS and nxt:
            digit = SPOKEN.get(nxt) or HOMOPHONES.get(nxt) or (nxt if nxt.isdigit() else None)
            if digit and len(digit) == 1:
                out.append(digit * REPEATS[word])
                homophone = homophone or nxt in HOMOPHONES
                i += 2
                continue
            return None, False
        if word in TENS:
            # "forty four" is 44; "forty" alone is 40.
            unit = SPOKEN.get(nxt) or HOMOPHONES.get(nxt) if nxt else None
            if unit:
                out.append(TENS[word] + unit)
                homophone = homophone or nxt in HOMOPHONES
                i += 2
                continue
            out.append(TENS[word] + "0")
        elif word in TEENS:
            out.append(TEENS[word])
        elif word in SPOKEN:
            out.append(SPOKEN[word])
        elif word in HOMOPHONES:
            out.append(HOMOPHONES[word])
            homophone = True
        elif word.isdigit():
            out.append(word)
        else:
            return None, False
        i += 1
    return "".join(out), homophone


def _candidates(text):
    """Every digit string the caller might have been reading out, best first."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    numberish = [w for w in words]
    runs, run = [], []
    for word in numberish:
        if (word.isdigit() or word in SPOKEN or word in HOMOPHONES
                or word in TENS or word in TEENS or word in REPEATS):
            run.append(word)
        else:
            if run:
                runs.append(run)
            run = []
    if run:
        runs.append(run)

    out = []
    for run in runs:
        # A lone homophone is a word like "for", not a digit. Two or more in a
        # row, or one beside a real number word, is someone reading a number.
        if len(run) == 1 and run[0] in HOMOPHONES and not run[0].isdigit():
            continue
        # "order for 44812" — the preposition is swept into the run, so try
        # the run without its ends as well as whole.
        variants = {tuple(run)}
        if len(run) > 1:
            variants.add(tuple(run[1:]))
            variants.add(tuple(run[:-1]))
        if len(run) > 2:
            variants.add(tuple(run[1:-1]))
        for variant in variants:
            if not variant:
                continue
            digits, _ = _spoken_digits(list(variant))
            if digits:
                out.append(digits)
    return out


def find_number(text):
    """Pull an order number out of what the caller said.

    Speech recognition writes them as digits, but people also say "four four
    eight one two", and the recogniser mishears half of those words as
    ordinary English. A candidate that matches a real order always wins over
    one that merely looks like a number.
    """
    digits = re.findall(r"\b\d{4,8}\b", text or "")
    for candidate in digits:
        if candidate in ORDERS:
            return candidate

    spoken = _candidates(text)
    for candidate in spoken:
        if candidate in ORDERS:
            return candidate
    # A number read out with a stray word in the middle arrives as two runs,
    # and one read out with an extra word on the end is too long. Slide a
    # window of the right size over everything and see what sticks.
    for candidate in spoken + ["".join(spoken)]:
        for start in range(max(0, len(candidate) - ORDER_LENGTH) + 1):
            window = candidate[start:start + ORDER_LENGTH]
            if window in ORDERS:
                return window

    if digits:
        return digits[0]
    for candidate in spoken:
        if 4 <= len(candidate) <= 8:
            return candidate
    return None


def working_days(start, end):
    """Working days between two dates, which is what the policies count in."""
    if start > end:
        return 0
    days = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def look_up(number):
    """Everything about one order, with the lateness already worked out."""
    number = (number or "").strip()
    order = ORDERS.get(number)
    if not order:
        return {"found": False, "number": number}

    out = dict(order)
    out["found"] = True
    out["number"] = number

    try:
        promised = datetime.strptime(order["promised"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return out

    today = _today()
    if order["status"] in ("delivered", "cancelled", "refunded"):
        out["days_late"] = 0
    else:
        out["days_late"] = working_days(promised, today)

    # The thresholds the documents use, resolved here rather than left for the
    # model to derive out loud.
    out["counts_as_late"] = out["days_late"] >= 2
    out["counts_as_lost"] = out["days_late"] >= 10
    return out


def describe(order):
    """Flatten an order into something to put in front of the model."""
    if not order.get("found"):
        return (f"No order found with the number {order.get('number') or 'given'}. "
                "Ask them to read it back, or offer to find it by email address.")

    items = ", ".join(f"{i['qty']} x {i['name']} at £{i['price']:.2f}"
                      for i in order.get("items", []))
    lines = [
        f"Order {order['number']}",
        f"Status: {order['status']}",
        f"Items: {items}",
        f"Placed: {order['placed']}, promised by {order['promised']}",
        f"Paid: £{order.get('paid', 0):.2f} ({order.get('delivery', 'standard')} delivery)",
    ]
    if order.get("carrier"):
        lines.append(f"Carrier: {order['carrier']}, tracking {order.get('tracking', 'none')}")
    if order.get("last_scan"):
        lines.append(f"Last tracking scan: {order['last_scan']}")
    for key, label in (("delivered_on", "Delivered on"), ("refunded_on", "Refunded on"),
                       ("cancelled_on", "Cancelled on"), ("return_started", "Return started"),
                       ("reason", "Reason")):
        if order.get(key):
            lines.append(f"{label}: {order[key]}")
    if "days_late" in order:
        if order["counts_as_lost"]:
            lines.append(f"This is {order['days_late']} working days past the promised "
                         "date, which is past the 10 day threshold for treating it as lost.")
        elif order["counts_as_late"]:
            lines.append(f"This is {order['days_late']} working days past the promised "
                         "date, which counts as late.")
        elif order["days_late"]:
            lines.append(f"This is {order['days_late']} working day past the promised "
                         "date, not yet late by the 2 day rule.")
        else:
            lines.append("This is not late.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        asked = " ".join(sys.argv[1:])
        number = find_number(asked)
        print(f"{asked!r} -> number {number!r}\n")
        print(describe(look_up(number)))
        raise SystemExit

    # What Chrome actually writes when someone reads an order number down a
    # microphone. Every short digit word has a commoner homophone and the
    # recogniser reaches for it, so these are not hypotheticals: "44" came
    # back as "for" on the first live test.
    HEARD = [
        ("it's order 44812", "44812"),
        ("order number four four eight one two", "44812"),
        ("for for eight one to", "44812"),
        ("my order is for for eight one too", "44812"),
        ("double four eight one two", "44812"),
        ("forty four eight twelve", "44812"),
        ("four four nine zero one", "44901"),
        ("for for nine oh one", "44901"),
        ("four five zero one zero", "45010"),
        ("for five oh one oh", "45010"),
        ("it's four four seven seven zero", "44770"),
        ("double four six double eight", "44688"),
        ("forty five one oh two", "45102"),
        ("four four eight one five please", "44815"),
        ("the number is 4 4 8 1 2", "44812"),
        ("45044", "45044"),
        # Ordinary speech. Nothing here is an order number, and "for", "to"
        # and "ate" must stay words.
        ("thanks for your help", None),
        ("I want to return it", None),
        ("can you look into it for me", None),
        ("I ate lunch and it still hasn't arrived", None),
        ("where is my order", None),
        ("for the second time", None),
        # A real number with a preposition swept in front of it.
        ("it's for order 44812", "44812"),
        ("I'm calling about order number 45044 please", "45044"),
    ]
    wrong = 0
    for heard, want in HEARD:
        got = find_number(heard)
        # Anything not a known order counts as "nothing found" here: the
        # agent asks again rather than looking up a number nobody said.
        found = got if got in ORDERS else None
        ok = found == want
        wrong += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {str(found):8} (wanted {str(want):8}) {heard}")
    print(f"\n  {len(HEARD) - wrong}/{len(HEARD)} heard correctly")
