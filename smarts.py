"""Small, instant judgements made on every turn.

Each of these runs before the model is called, so it has to cost nothing: a
second model call to decide which model to call would put a whole round trip
in front of every answer, which is the opposite of the point. They are
heuristics, and they say why they decided, so the screen can show it.
"""

import os
import re

# ------------------------------------------------------------------ routing

GREETING = re.compile(
    r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening)|thanks?( you)?|thank you|"
    r"cheers|ok(ay)?|cool|great|perfect|yes|yeah|yep|no|nope|sure|bye|goodbye|"
    r"marhaba|salam|مرحبا|السلام عليكم|شكرا|hola|gracias|bonjour|merci|hallo|danke)\b",
    re.I)

# Questions that need working out rather than recalling.
HARD = re.compile(
    r"\b(why|how come|how (do|does|would|should|can) (i|we|you|it)|compare|comparison|"
    r"difference between|versus|vs\.?|pros and cons|trade-?offs?|which (is|one) (better|best)|"
    r"should i|calculate|work out|estimate|step by step|troubleshoot|diagnos\w*|"
    r"what('s| is) wrong|figure out|plan|explain how|walk me through)\b", re.I)


def _pick(catalog, preferred, fallbacks=()):
    """The first of these the account actually has."""
    if not catalog:
        return preferred
    for name in (preferred,) + tuple(fallbacks):
        if name and name in catalog:
            return name
    return None


def quick_model(catalog, chosen):
    want = os.getenv("SAMBANOVA_QUICK_MODEL", "")
    found = _pick(catalog, want) if want else None
    if found:
        return found
    # The smallest Llama on the account, by name.
    small = [m for m in (catalog or []) if re.search(r"\b(1B|3B|8B)\b", m.replace("-", " "))]
    return small[0] if small else chosen


def think_model(catalog):
    return _pick(catalog, os.getenv("SAMBANOVA_THINK_MODEL") or "gpt-oss-120b",
                 ("DeepSeek-R1-0528", "DeepSeek-R1", "Qwen3-32B"))


def route(prompt, catalog, chosen, has_order=False):
    """Which model should answer this turn, and why.

    quick    greetings and acknowledgements: the smallest model, nothing to think about
    standard most questions: the model picked in the UI (Llama by default)
    deep     questions that need working out: a reasoning model thinks while the
             standard model talks, so the caller is never left in silence
    """
    words = len(prompt.split())
    # Only a greeting on its own is small talk. "hi, it's about order 44812"
    # starts like one and is not.
    if (words <= 5 and GREETING.search(prompt) and "?" not in prompt
            and not has_order and not re.search(r"\d", prompt)
            and not re.search(r"\b(order|refund|deliver\w*|return|problem|issue|help)\b", prompt, re.I)):
        model = quick_model(catalog, chosen)
        return {"tier": "quick", "model": model, "reason": "small talk"}

    if HARD.search(prompt) and words >= 7 and not has_order:
        thinker = think_model(catalog)
        if thinker and thinker != chosen:
            return {"tier": "deep", "model": chosen, "thinker": thinker,
                    "reason": "needs working out"}

    return {"tier": "standard", "model": chosen, "reason": "ordinary question"}


# ------------------------------------------------------------------ mood

FRUSTRATED = re.compile(
    r"\b(ridiculous|unacceptable|useless|terrible|awful|joke|again|still|seriously|"
    r"fed up|sick of|waste|wasting|angry|furious|annoyed|frustrated|disgusting|"
    r"worst|never (arrived|came)|third time|second time|how many times|"
    r"what the|wtf|damn|bloody|hell)\b", re.I)
CONFUSED = re.compile(
    r"\b(confused|don'?t understand|do not understand|what do you mean|lost|"
    r"not sure (what|how)|i don'?t get|huh|sorry\?|come again|which one|"
    r"can you explain|say that again)\b", re.I)
HURRIED = re.compile(r"\b(quick(ly)?|hurry|asap|in a rush|no time|just tell me|short version)\b", re.I)


def mood(prompt, previous="neutral"):
    """How the caller sounds, from what they said and how they said it.

    Frustration is sticky: one calm sentence does not mean they have calmed
    down, so it fades over two turns rather than flipping straight back.
    """
    text = prompt or ""
    shouting = sum(1 for w in re.findall(r"\b[A-Z]{3,}\b", text) if w not in ("GPU", "CPU", "USB", "API"))
    bangs = text.count("!")
    if FRUSTRATED.search(text) or shouting >= 2 or bangs >= 2:
        return "frustrated"
    if CONFUSED.search(text):
        return "confused"
    if HURRIED.search(text):
        return "hurried"
    if previous == "frustrated":
        return "settling"
    return "neutral"


MOOD_RULES = {
    "frustrated": ("The caller is frustrated. Acknowledge it once, plainly, without grovelling. "
                   "Keep sentences short and calm, give the concrete next step first, and "
                   "do not use upbeat filler or exclamation marks."),
    "settling": ("The caller was frustrated a moment ago. Stay calm and concrete; do not "
                 "bring the frustration up again."),
    "confused": ("The caller is confused. Slow down: one idea per sentence, plain words, and "
                 "check they are with you before moving on."),
    "hurried": "The caller is in a hurry. Give the answer in one or two short sentences, nothing else.",
}

# How the voice itself changes. Cartesia's sonic-2 voice controls; anything the
# account's model does not accept is dropped for the session automatically.
MOOD_VOICE = {
    "frustrated": {"speed": "slow", "emotion": ["positivity:low", "curiosity:low"]},
    "settling":   {"speed": "normal", "emotion": ["positivity"]},
    "confused":   {"speed": "slow", "emotion": ["positivity"]},
    "hurried":    {"speed": "fast"},
}

MOOD_SAYS = {
    "frustrated": "slower, calmer voice",
    "settling": "staying calm",
    "confused": "slower, one step at a time",
    "hurried": "shorter answers, faster voice",
}


# ------------------------------------------------------------------ claims

SENTENCE_END = re.compile(r"(?<=[.!?؟。])[\"')\]]?\s+")

# What makes a sentence checkable against the records: numbers, dates and the
# vocabulary policies are written in. "Sure, let me look" has none of these.
CLAIM = re.compile(
    r"(\d|£|\$|€|%|"
    r"\b(today|tomorrow|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"days?|weeks?|months?|years?|hours?|refund\w*|deliver\w*|dispatch\w*|ship\w*|"
    r"warrant\w*|guarantee\w*|return\w*|exchange\w*|replace\w*|price\w*|cost\w*|charge\w*|"
    r"free|fee|paid|pay|open|closed?|track\w*|carrier|lost|late|arriv\w*|cancel\w*|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fourteen|thirty)\b)",
    re.I)


def split_sentences(buffer):
    """Complete sentences out of a growing buffer, and what is left over."""
    parts = SENTENCE_END.split(buffer)
    if len(parts) == 1:
        return [], buffer
    done = [p for p in parts[:-1] if p.strip()]
    # split() drops the whitespace that separated them; put it back so the
    # spoken text keeps its spacing.
    return [p + " " for p in done], parts[-1]


def has_claim(sentence):
    return bool(CLAIM.search(sentence or ""))


if __name__ == "__main__":
    cat = ["Meta-Llama-3.3-70B-Instruct", "Meta-Llama-3.1-8B-Instruct", "gpt-oss-120b"]
    for p in ("hi there", "thanks!", "hi, it's about order 44812", "hey, I need help", "where's my order 44812?",
              "why would my headphones disconnect every time I open my laptop lid",
              "what's the difference between the X1 and X3 earbuds for running",
              "how long do refunds take"):
        r = route(p, cat, "Meta-Llama-3.3-70B-Instruct", has_order="44812" in p)
        print(f"  {r['tier']:8} {r.get('thinker') or r['model']:30} {p}")
    print()
    for p in ("this is RIDICULOUS, it still hasn't arrived!!", "what do you mean, which one?",
              "just tell me quickly", "ok thanks"):
        print(f"  {mood(p):10} {p}")
    print()
    print(split_sentences("Your order is late. It shipped on the 21st. And so"))
    print([has_claim(s) for s in ("Sure, let me look.", "It's 11 working days late.",
                                  "Refunds take five working days.", "Anything else?")])
