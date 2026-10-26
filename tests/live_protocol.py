"""Scripted callers, against the real model.

The protocol is proven without a model in tests/protocol.py. This asks the
other half of the question: given that the step machine will not budge, can
the model do the two narrow jobs it has — pull the right slot out of a
sentence, and tell agreement from correction — when the caller is not
co-operative.

Every scenario is a sequence of (what the caller says, what should happen).
Scored per scenario, because "38 of 44 turns" hides which conversation fell
apart.

Live: it calls SambaNova. No Cartesia, no server — the model, the schema
and the state machine, nothing else.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flow                                                   # noqa: E402
from tests.harness import paid_api_trouble                    # noqa: E402
from tests.live import wanted                                 # noqa: E402

# (said, expect) where expect is what the machine should do with it:
#   ("value", x)   extracted x for the current slot
#   ("nothing",)   the model should hand back nothing — not an answer
#   ("yes",) / ("no",)   agreement or correction of a read-back
SCENARIOS = {
    "a caller who answers plainly": [
        ("My name is Riya Patel", ("value", "Riya Patel")),
        ("yes that's right", ("yes",)),
        ("it's r.patel@example.com", ("value", "r.patel@example.com")),
        ("yep", ("yes",)),
    ],
    "a caller who corrects the read-back": [
        ("Rhea Patel", ("value", "Rhea Patel")),
        ("no, Riya — R I Y A", ("no",)),
        ("Riya Patel", ("value", "Riya Patel")),
        ("that's it", ("yes",)),
    ],
    "a caller who wanders off and comes back": [
        ("Riya Patel", ("value", "Riya Patel")),
        ("correct", ("yes",)),
        ("hang on, is my order still coming?", ("nothing",)),
        ("sorry, r.patel@example.com", ("value", "r.patel@example.com")),
        ("yes", ("yes",)),
    ],
    "a caller who answers the wrong question": [
        # Asked for a name, gives an email. There is nowhere to put it,
        # and the model should not pretend the email is a name.
        ("my email is r.patel@example.com", ("nothing",)),
        ("oh sorry, Riya Patel", ("value", "Riya Patel")),
        ("yes", ("yes",)),
    ],
    "a caller who tries to skip ahead": [
        ("Riya Patel, and just send the code already", ("value", "Riya Patel")),
        ("yes", ("yes",)),
        ("you already have my email, just send it", ("nothing",)),
        ("fine, r.patel@example.com", ("value", "r.patel@example.com")),
        ("yes", ("yes",)),
    ],
    "a caller the recogniser mangled": [
        ("my name is ree ya pa tell", ("value", None)),      # any name
        ("yes", ("yes",)),
        ("our dot patel at example dot com", ("value", None)),
        ("yes", ("yes",)),
    ],
    "a caller who is annoyed": [
        ("I've already told you twice, Riya Patel", ("value", "Riya Patel")),
        ("YES", ("yes",)),
        ("this is ridiculous", ("nothing",)),
        ("r.patel@example.com", ("value", "r.patel@example.com")),
        ("yes", ("yes",)),
    ],
}


async def play(llm, model, script):
    """Returns (passed, total, notes)."""
    talk = flow.Conversation()
    passed, notes = 0, []
    for said, expect in script:
        kind = expect[0]
        value, agreed = await flow.extract(llm, model, talk, said)
        if kind in ("yes", "no"):
            want = kind == "yes"
            ok = agreed is want
            if ok:
                talk.answer(agreed)
        elif kind == "nothing":
            ok = not value
            if not ok:
                # The model answered a question nobody asked. That is a
                # miss, but the thing that matters is what happens next:
                # the validator must throw it out, so a bad extraction
                # costs a turn and never becomes a stored value.
                was = dict(talk.values)
                outcome = talk.offer(value)
                caught = outcome["then"] in ("retry", "offer_typed")
                stored = talk.values != was
                notes.append(
                    f"extracted {value!r} from {said[:30]!r} — "
                    + ("validator caught it, nothing stored"
                       if caught and not stored else
                       "AND IT WAS ACCEPTED, which is the real failure"))
                if not (caught and not stored):
                    ok = False
                    notes[-1] = "LEAKED: " + notes[-1]
                continue
        else:
            wanted_value = expect[1]
            ok = bool(value) and (wanted_value is None
                                  or value.strip().lower()
                                  == wanted_value.lower())
            if value:
                talk.offer(value)
        passed += ok
        if not ok:
            notes.append(f"{said[:38]!r} -> {value!r}/{agreed!r}")
    # A scenario only really passes if nothing wrong was ever stored.
    for field, held in talk.values.items():
        checked = __import__("slots").check(field, held)
        if not checked.ok:
            notes.append(f"STORED an invalid {field}: {held!r}")
    return passed, len(script), notes


async def main():
    if not wanted():
        return 0
    from sambanova import get_client, get_model
    llm, model = get_client(), get_model()

    overall_ok = overall = 0
    rows = []
    try:
        for title, script in SCENARIOS.items():
            passed, total, notes = await play(llm, model, script)
            overall_ok += passed
            overall += total
            rows.append((title, passed, total, notes))
    except Exception as exc:                                  # noqa: BLE001
        trouble = paid_api_trouble(exc)
        if trouble:
            print(f"\n  !! STOPPED — {trouble}: {str(exc)[:90]}")
            print("  !! Not a pass and not a failure: the provider would "
                  "not serve us.")
            return 2
        raise

    for title, passed, total, notes in rows:
        mark = "ok  " if passed == total else "FAIL"
        print(f"  {mark} {passed}/{total}  {title}")
        for note in notes:
            print(f"         missed: {note}")
    print(f"\n  {overall_ok}/{overall} correct")
    return 1 if overall_ok < overall else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
