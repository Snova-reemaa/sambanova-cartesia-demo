"""The state machine holds the step. The model cannot move it.

These drive flow.Conversation directly, with no model and no server, which
is the point: what is being checked is that the protocol does not depend on
the model behaving. A model that tries to answer the wrong question, or to
announce something that has not happened, finds nowhere to put it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flow                                                   # noqa: E402

results = []


def check(what, ok):
    results.append((what, bool(ok)))


def run():
    # --- a clean run through -------------------------------------------
    c = flow.Conversation()
    check("starts at the name", c.step == "name")
    check("asks for a name", c.wants()["field"] == "name")
    check("the schema offers only the name",
          list(c.schema_for()["properties"]) == ["name"])

    out = c.offer("Riya Patel")
    check("a good name is read back", out["then"] == "confirm")
    check("read back as given", out["say"] == "Riya Patel")
    check("the step has not moved yet", c.step == "name")
    check("and the schema now wants a yes or no",
          list(c.schema_for()["properties"]) == ["agreed"])

    out = c.answer(True)
    check("agreeing advances to the email", out["step"] == "email")
    check("the name is kept", c.values["name"] == "Riya Patel")

    # --- the model tries to skip a step --------------------------------
    c2 = flow.Conversation()
    # It has been asked for a name. There is nowhere to put an email.
    check("an email cannot be returned during the name step",
          "email" not in c2.schema_for()["properties"])
    # And if something hands one over anyway, it is validated as a name
    # and the step still does not jump.
    c2.offer("r.patel@example.com")
    check("an email offered as a name does not move the step",
          c2.step == "name")
    check("and nothing is marked sent", not c2.may_say_sent())
    check("and nobody is verified", not c2.may_say_verified())

    # The send step cannot be reached by talking.
    c3 = flow.Conversation()
    c3.offer("Riya Patel"); c3.answer(True)
    c3.offer("r.patel@example.com"); c3.answer(True)
    check("after the email, the next step is to send", c3.step == "send")
    check("nothing claims to have been sent yet", not c3.may_say_sent())
    check("there is no question to ask at the send step",
          c3.wants() is None)
    c3.mark_sent()
    check("only sending makes it sent", c3.may_say_sent())
    check("and that moves to the code", c3.step == "code")

    # --- a correction mid read-back ------------------------------------
    c4 = flow.Conversation()
    c4.offer("Rhea Patel")
    out = c4.answer(False)
    check("saying no goes back to the same slot", c4.step == "name")
    check("and is not counted as a failure", c4.misses == 0)
    check("and is marked as a correction", out.get("corrected"))
    out = c4.offer("Riya Patel")
    check("the correction is read back", out["say"] == "Riya Patel")
    c4.answer(True)
    check("and then it moves on", c4.step == "email")
    check("the corrected value is the one kept",
          c4.values["name"] == "Riya Patel")

    # --- wandering off topic, then coming back -------------------------
    c5 = flow.Conversation()
    c5.offer("Riya Patel"); c5.answer(True)
    before = c5.step
    out = c5.aside()
    check("an aside does not move the step", c5.step == before)
    check("and it says what to ask again",
          out["reask"]["field"] == "email")
    check("nothing was accepted by wandering", "email" not in c5.values)
    c5.offer("r.patel@example.com")
    c5.answer(True)
    check("the step resumes where it was", c5.step == "send")

    # --- patience: two failures, then the typed box --------------------
    c6 = flow.Conversation()
    c6.offer("Riya Patel"); c6.answer(True)
    first = c6.offer("not an email at all")
    check("a bad answer asks again", first["then"] == "retry")
    check("the typed box is not offered yet", not c6.typed)
    second = c6.offer("still not an email")
    check("the second failure offers the typed box",
          second["then"] == "offer_typed")
    check("and the box is showing", c6.typed)
    check("but the caller is not blocked", c6.step == "email")
    good = c6.offer("r.patel@example.com")
    check("and a good answer still works", good["then"] == "confirm")

    # --- the code step -------------------------------------------------
    c7 = flow.Conversation()
    for said in ("Riya Patel", "r.patel@example.com"):
        c7.offer(said); c7.answer(True)
    c7.mark_sent()
    check("a code is never read back", c7.current.confirm is False)
    out = c7.offer("four two seven nine one oh")
    check("a spoken code is accepted as six digits", out["then"] == "next")
    check("but accepting the digits is not being verified",
          not c7.may_say_verified())
    c7.mark_verified(True)
    check("only a code that checked out verifies", c7.may_say_verified())
    check("and the flow is finished", c7.finished)

    # Three wrong codes, shared with the typed box.
    c8 = flow.Conversation(first="code")
    c8.mark_verified(False)
    check("one wrong code asks again", not c8.typed)
    c8.mark_verified(False)
    check("the second offers the typed box", c8.typed)
    check("and still nobody is verified", not c8.may_say_verified())

    for what, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    bad = sum(not ok for _, ok in results)
    print(f"\n  {len(results) - bad}/{len(results)} correct")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(run())
