"""The steps of a structured exchange, and who decides what.

Code holds the step. The model has exactly two jobs — phrase the question
in the caller's language, and pull the answer out of the sentence — and it
cannot do anything else, because nothing else is wired to it. It cannot
decide the step is finished, cannot say a code was sent, cannot say someone
is verified. Those are facts about state, and state lives here.

That is the whole design. An agent told in a prompt to "ask for the email,
then confirm it, then send a code" will one call in twenty announce it has
sent a code it never sent, and no amount of prompt will reliably stop it.
So it is not asked to.

Each step is the same shape:

    ask       what to get the model to phrase
    field     which validator in slots.py decides whether the answer is good
    confirm   whether the canonical value is read back for a yes or no
    on_yes    where to go when they agree
    on_no     where to go when they do not, usually back to the same slot

and the loop around them is always:

    ask -> extract -> validate in code -> read back -> yes or no -> advance

Off-topic questions are answered and then the current step is asked again.
State does not move, because nothing happened to it.
"""

import time

import slots

# After this many failed validations on one step, stop asking the same way.
# Read back whatever we have and offer the typed box. Hard-blocking a caller
# who cannot be understood is how a support line loses somebody who had a
# real problem.
PATIENCE = 2


class Step:
    __slots__ = ("name", "ask", "field", "confirm", "on_yes", "on_no", "kw")

    def __init__(self, name, ask, field=None, confirm=True,
                 on_yes=None, on_no=None, **kw):
        self.name = name
        self.ask = ask
        self.field = field
        self.confirm = confirm
        self.on_yes = on_yes
        self.on_no = on_no
        self.kw = kw


# The identification flow. Nothing personal is said before `done`.
STEPS = {
    "name": Step(
        "name",
        ask="Ask for their full name.",
        field="name",
        on_yes="email", on_no="name"),
    "email": Step(
        "email",
        ask="Ask for the email address on the account.",
        field="email",
        on_yes="send", on_no="email"),
    # Not a question: the code is sent here, by code, and the model is told
    # afterwards. It cannot reach this step early and it cannot skip it.
    "send": Step(
        "send", ask=None, field=None, confirm=False,
        on_yes="code", on_no="code"),
    "code": Step(
        "code",
        ask="Ask them to read back the six digit code.",
        field="code",
        confirm=False,                       # never read a code back
        on_yes="done", on_no="code"),
    "done": Step("done", ask=None, field=None, confirm=False),
}

FIRST = "name"


class Conversation:
    """Where this caller has got to, and what may happen next.

    Everything the model is allowed to say about progress is derived from
    here. `sent` is only ever set by actually sending, `verified` only by a
    code that checked out.
    """

    def __init__(self, steps=None, first=None, patience=PATIENCE):
        self.steps = steps or STEPS
        self.step = first or FIRST
        self.values = {}          # field -> canonical value
        self.pending = None       # (field, value, say) waiting for yes or no
        self.misses = 0           # failed validations on this step
        self.typed = False        # the typed box is showing
        self.sent = False         # a code really was sent
        self.verified = False     # a code really did check out
        self.history = []         # (step, what happened), for the tests
        self.started = time.time()

    # ---------------------------------------------------------- enquiry

    @property
    def finished(self):
        return self.step == "done"

    @property
    def current(self):
        return self.steps[self.step]

    def wants(self):
        """What the model should be asked to phrase, or None."""
        if self.pending:
            field, _value, say = self.pending
            return {"do": "confirm", "field": field, "say": say}
        step = self.current
        if step.ask is None:
            return None
        return {"do": "ask", "field": step.field, "ask": step.ask,
                "typed": self.typed, "attempts_used": self.misses}

    def schema_for(self):
        """The one slot the model may return at this moment, and no other.

        A tool schema naming only the current field is what stops the model
        answering a question it was not asked — it cannot hand back an email
        during the name step because there is nowhere to put one.
        """
        if self.pending:
            return {"type": "object",
                    "properties": {"agreed": {"type": "boolean"}},
                    "required": ["agreed"]}
        field = self.current.field
        if not field:
            return None
        return {"type": "object",
                "properties": {field: {"type": "string"}},
                "required": [field]}

    # ----------------------------------------------------------- moving

    def note(self, what):
        self.history.append((self.step, what))

    def offer(self, said, **kw):
        """The caller answered the current step. Validate and decide.

        Returns a dict the caller of this method acts on — never a sentence,
        because what to say is the model's job and whether to move is not.
        """
        step = self.current
        if step.field is None:
            return {"then": "wait"}

        checked = slots.check(step.field, said, **{**step.kw, **kw})
        if not checked.ok:
            self.misses += 1
            self.note(f"rejected: {checked.problem}")
            out = {"then": "retry", "problem": checked.problem,
                   "needs": checked.needs, "attempts": self.misses}
            if self.misses >= PATIENCE:
                # Not a block. Another way in, and a chance to confirm what
                # we already heard.
                self.typed = True
                out["then"] = "offer_typed"
                out["have"] = self.values.get(step.field)
            return out

        if not step.confirm:
            return self.accept(checked.value)

        self.pending = (step.field, checked.value, checked.say)
        self.note(f"read back: {checked.say}")
        return {"then": "confirm", "field": step.field, "say": checked.say}

    def answer(self, agreed):
        """Yes or no to a read-back."""
        if not self.pending:
            return {"then": "wait"}
        field, value, _say = self.pending
        self.pending = None
        if agreed:
            return self.accept(value)
        # "No, it's..." goes back to the same slot, with the count reset:
        # a correction is not a failure, and treating it as one walks the
        # caller into the typed box for getting something right.
        self.misses = 0
        self.note("corrected")
        self.step = self.current.on_no or self.step
        return {"then": "retry", "problem": None, "corrected": True}

    def accept(self, value):
        step = self.current
        self.values[step.field] = value
        self.misses = 0
        self.note(f"accepted {step.field}")
        self.step = step.on_yes or "done"
        return {"then": "next", "field": step.field, "value": value,
                "step": self.step}

    def aside(self):
        """They asked something else. Answer it, then ask again.

        Nothing moves. The step is not finished, so it is still the step.
        """
        self.note("aside")
        return {"then": "aside", "reask": self.wants()}

    # -------------------------------------------------- facts, not claims

    def may_say_sent(self):
        return self.sent

    def may_say_verified(self):
        return self.verified

    def mark_sent(self):
        """Only ever called after something really was sent."""
        self.sent = True
        self.note("code sent")
        self.step = self.steps["send"].on_yes
        return {"then": "next", "step": self.step}

    def mark_verified(self, ok):
        if ok:
            self.verified = True
            self.note("verified")
            self.step = "done"
            return {"then": "next", "step": "done"}
        self.misses += 1
        self.note("code refused")
        if self.misses >= PATIENCE:
            self.typed = True
            return {"then": "offer_typed", "attempts": self.misses}
        return {"then": "retry", "attempts": self.misses}
