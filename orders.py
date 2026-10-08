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


def find_number(text):
    """Pull an order number out of what the caller said.

    Speech recognition writes them as digits, but people also say "four four
    eight one two" and read them back with spaces.
    """
    digits = re.findall(r"\b\d{4,8}\b", text or "")
    for candidate in digits:
        if candidate in ORDERS:
            return candidate
    spaced = re.sub(r"[\s\-]", "", text or "")
    for candidate in re.findall(r"\d{4,8}", spaced):
        if candidate in ORDERS:
            return candidate
    return digits[0] if digits else None


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

    print(f"{len(ORDERS)} orders from {PATH}, today is {_today()}\n")
    asked = " ".join(sys.argv[1:]) or "it's order 44812"
    number = find_number(asked)
    print(f"{asked!r} -> number {number!r}\n")
    print(describe(look_up(number)))
