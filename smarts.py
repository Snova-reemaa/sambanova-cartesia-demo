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
    r"marhaba|salam|مرحبا|السلام عليكم|شكرا|شكرًا|أهلا|أهلاً|هلا|مع السلامة|تمام|"
    r"hola|buenos d[ií]as|buenas|gracias|vale|adi[oó]s|s[ií]|"
    r"bonjour|bonsoir|salut|merci|d'accord|au revoir|oui|"
    r"hallo|guten (morgen|tag|abend)|danke|tsch[uü]ss|ja|"
    r"ciao|buongiorno|buonasera|grazie|va bene|arrivederci|s[ií]|"
    r"ol[aá]|bom dia|boa (tarde|noite)|obrigad[oa]|at[eé] logo|"
    r"hoi|goedemorgen|goedemiddag|dank u|bedankt|doei|tot ziens|ja)\b",
    re.I)

# Questions that need working out rather than recalling.
#
# Every one of these lists used to be English only, so a caller asking
# exactly the question that earns the reasoning model — "why do my
# headphones disconnect every time I open my laptop" — got the ordinary
# tier in Arabic and an offer to transfer them to technical support. The
# routing decided what kind of question it was from words it could not read.
HARD = re.compile(
    r"\b(why|how come|how (do|does|would|should|can) (i|we|you|it)|compare|comparison|"
    r"difference between|versus|vs\.?|pros and cons|trade-?offs?|which (is|one) (better|best)|"
    r"should i|calculate|work out|estimate|step by step|troubleshoot|diagnos\w*|"
    r"what('s| is) wrong|figure out|plan|explain how|walk me through)\b"
    # why / how do I / what is the difference / what is wrong / fix this
    r"|\b(por ?qu[eé]|c[oó]mo puedo|c[oó]mo se|cu[aá]l es la diferencia|qu[eé] pasa|"
    r"qu[eé] le pasa|explica|expl[ií]came|comparar|cu[aá]l es mejor|solucionar|arreglar)\b"
    r"|\b(pourquoi|comment (puis-je|faire|est-ce)|quelle (est la )?diff[ée]rence|"
    r"qu'est-ce qui (ne va pas|cloche)|expliquer|expliquez|comparer|lequel est mieux|"
    r"r[ée]soudre|d[ée]panner)\b"
    r"|\b(warum|wieso|weshalb|wie kann ich|wie funktioniert|unterschied zwischen|"
    r"vergleich|was ist (los|falsch)|erkl[aä]ren|erkl[aä]r mir|beheben|l[oö]sen)\b"
    r"|\b(perch[eé]|come posso|come mai|qual [eè] la differenza|cosa non va|"
    r"spiegare|spiegami|confronto|quale [eè] meglio|risolvere)\b"
    r"|\b(por ?qu[eê]|porqu[eê]|como posso|qual [eé] a diferen[cç]a|o que (h[aá]|se passa)|"
    r"explicar|explica-me|comparar|qual [eé] melhor|resolver)\b"
    r"|\b(waarom|hoe kan ik|hoe werkt|wat is het verschil|wat is er (mis|aan de hand)|"
    r"uitleggen|leg uit|vergelijken|welke is beter|oplossen)\b"
    r"|(ليش|لماذا|ليه|وش السبب|ما السبب|إيش السبب|كيف أ|كيف ا|شلون|إزاي|ازاي|"
    r"الفرق بين|وش الفرق|أيهما أفضل|ايش احسن|وش الحل|ما الحل|اشرح|وضح|"
    r"ما المشكلة|وش المشكلة|كل ما)",
    re.I)

# Arabic says in five words what English needs eight for, so the same
# threshold would keep ruling it out.
SHORT_LANGUAGES = {"ar"}


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


def route(prompt, catalog, chosen, has_order=False, code="en"):
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

    if HARD.search(prompt) and words >= (5 if code in SHORT_LANGUAGES else 7) \
            and not has_order:
        thinker = think_model(catalog)
        if thinker and thinker != chosen:
            return {"tier": "deep", "model": chosen, "thinker": thinker,
                    "reason": "needs working out"}

    return {"tier": "standard", "model": chosen, "reason": "ordinary question"}


# ------------------------------------------------------------------ mood

# Mood was read from English words too, so a caller could be plainly furious
# in Spanish and be answered as though they were perfectly happy.
FRUSTRATED = re.compile(
    r"\b(ridiculous|unacceptable|useless|terrible|awful|joke|again|still|seriously|"
    r"fed up|sick of|waste|wasting|angry|furious|annoyed|frustrated|disgusting|"
    r"worst|never (arrived|came)|third time|second time|how many times|"
    r"what the|wtf|damn|bloody|hell)\b"
    r"|\b(incre[ií]ble|inaceptable|in[uú]til|fatal|vergüenza|harto|harta|otra vez|"
    r"todav[ií]a|tercera vez|segunda vez|enfadad[oa]|molest[oa]|p[eé]simo)\b"
    r"|\b(inadmissible|inacceptable|inutile|scandaleux|ras[- ]le[- ]bol|encore une fois|"
    r"toujours pas|troisi[eè]me fois|deuxi[eè]me fois|[ée]nerv[ée]|furieux|honteux)\b"
    r"|\b(l[aä]cherlich|inakzeptabel|unm[oö]glich|nutzlos|frechheit|schon wieder|"
    r"immer noch|dritte mal|zweite mal|verärgert|w[uü]tend|unversch[aä]mt)\b"
    r"|\b(assurdo|inaccettabile|inutile|vergogna|di nuovo|ancora|terza volta|"
    r"seconda volta|arrabbiat[oa]|scandaloso)\b"
    r"|\b(rid[ií]culo|inaceit[aá]vel|in[uú]til|vergonha|outra vez|ainda|terceira vez|"
    r"segunda vez|irritad[oa]|zangad[oa]|p[eé]ssimo)\b"
    r"|\b(belachelijk|onacceptabel|nutteloos|schande|alweer|nog steeds|derde keer|"
    r"tweede keer|boos|kwaad|woedend)\b"
    r"|(سخيف|غير مقبول|ما ينفع|فاشل|مو معقول|مره ثانية|مرة ثانية|للمرة الثالثة|"
    r"زهقت|مليت|تعبت|زعلان|غاضب|متضايق|وش هالخدمة|كارثة|سيئ جدا)", re.I)
CONFUSED = re.compile(
    r"\b(confused|don'?t understand|do not understand|what do you mean|lost|"
    r"not sure (what|how)|i don'?t get|huh|sorry\?|come again|which one|"
    r"can you explain|say that again)\b"
    r"|\b(no entiendo|no comprendo|qu[eé] quieres decir|estoy perdid[oa]|"
    r"c[oó]mo dice|repite|cu[aá]l de)\b"
    r"|\b(je ne comprends pas|comprends pas|que voulez-vous dire|je suis perdu|"
    r"r[ée]p[eé]tez|pardon ?\?|lequel)\b"
    r"|\b(verstehe nicht|ich verstehe das nicht|was meinen sie|bin verwirrt|"
    r"nochmal|wie bitte|welche[rn]?)\b"
    r"|\b(non capisco|non ho capito|cosa intendi|sono confus[oa]|ripeti|quale di)\b"
    r"|\b(n[aã]o percebo|n[aã]o entendo|o que quer dizer|estou perdid[oa]|repita|qual dos)\b"
    r"|\b(ik begrijp het niet|snap het niet|wat bedoelt u|ik ben in de war|"
    r"herhaal|welke van)\b"
    r"|(ما فهمت|مو فاهم|مش فاهم|وش تقصد|ايش تقصد|ممكن تعيد|أعد|محتار|مش واضح)", re.I)
HURRIED = re.compile(
    r"\b(quick(ly)?|hurry|asap|in a rush|no time|just tell me|short version)\b"
    r"|\b(r[aá]pido|deprisa|tengo prisa|sin tiempo|resumido|solo dime)\b"
    r"|\b(vite|rapidement|je suis press[ée]|pas le temps|en bref|dites-moi juste)\b"
    r"|\b(schnell|eile|keine zeit|kurz gesagt|sag mir einfach)\b"
    r"|\b(veloce|in fretta|ho fretta|non ho tempo|in breve|dimmi solo)\b"
    r"|\b(r[aá]pido|depressa|tenho pressa|sem tempo|resumindo|diga-me s[oó])\b"
    r"|\b(snel|haast|geen tijd|kort gezegd|zeg gewoon)\b"
    r"|(بسرعة|مستعجل|ما عندي وقت|باختصار|بس قل لي|على السريع)", re.I)


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

# How the voice itself changes with the caller's mood.
#
# Two levers, and they are not equally portable. Speed works on every
# language Cartesia speaks. Emotion is documented as English-only and in
# beta — though the API accepts it for all eight of ours, which means only
# that it does not refuse, not that it does anything useful. Whether it helps
# or distorts the voice is a listening judgement, one language at a time.
MOOD_VOICE = {
    "frustrated": {"speed": "slow", "emotion": ["positivity:low", "curiosity:low"]},
    "settling":   {"speed": "normal", "emotion": ["positivity"]},
    "confused":   {"speed": "slow", "emotion": ["positivity"]},
    "hurried":    {"speed": "fast"},
}

# Which languages emotion is sent for. Everything on by default, because the
# API takes it everywhere; turn one off the moment it is heard to sound wrong
# and that language keeps the speed change and the wording change, which work
# regardless. EMOTION_LANGUAGES overrides the lot: a comma-separated list, or
# "none".
EMOTION_BY_DEFAULT = {
    "en": True, "es": True, "fr": True, "de": True,
    "it": True, "pt": True, "nl": True, "ar": True,
}


def _configured():
    raw = (os.getenv("EMOTION_LANGUAGES") or "").strip().lower()
    if not raw:
        return dict(EMOTION_BY_DEFAULT)
    if raw in ("none", "off", "0"):
        return {code: False for code in EMOTION_BY_DEFAULT}
    wanted = {c.strip() for c in raw.split(",") if c.strip()}
    return {code: code in wanted for code in EMOTION_BY_DEFAULT}


EMOTION_LANGUAGES = _configured()


def voice_controls(mood, code="en", emotion_ok=None):
    """What to ask the voice for on this turn, or None.

    `emotion_ok` lets a call drop emotion for one language after the model
    has refused it, without losing the pacing for that language or the
    emotion for the others.
    """
    wanted = MOOD_VOICE.get(mood)
    if not wanted:
        return None
    allowed = EMOTION_LANGUAGES.get(code, False)
    if emotion_ok is not None:
        allowed = allowed and emotion_ok
    out = {key: value for key, value in wanted.items()
           if key != "emotion" or allowed}
    return out or None

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
    wrong = 0

    # (language, what they said, the tier it has to get)
    ROUTES = [
        ("en", "hi there", "quick"),
        ("en", "thanks!", "quick"),
        ("ar", "مرحبا", "quick"),
        ("es", "hola, gracias", "quick"),
        ("fr", "bonjour", "quick"),
        ("en", "hi, it's about order 44812", "standard"),
        ("en", "where's my order 44812?", "standard"),
        ("en", "how long do refunds take", "standard"),
        ("ar", "متى يوصل طلبي", "standard"),
        # The same question in eight languages. Every one of these got the
        # ordinary tier before, because the routing read English only, and
        # the caller was offered a transfer instead of an answer.
        ("en", "why do my headphones disconnect every time I open my laptop", "deep"),
        ("ar", "ليش سماعاتي تنفصل كل ما افتح اللابتوب", "deep"),
        ("es", "¿por qué mis auriculares se desconectan cada vez que abro el portátil?", "deep"),
        ("fr", "pourquoi mes écouteurs se déconnectent chaque fois que j'ouvre mon ordinateur", "deep"),
        ("de", "warum trennen sich meine Kopfhörer jedes Mal wenn ich den Laptop öffne", "deep"),
        ("it", "perché le mie cuffie si disconnettono ogni volta che apro il portatile", "deep"),
        ("pt", "porque é que os meus auscultadores desligam sempre que abro o portátil", "deep"),
        ("nl", "waarom verbreekt mijn koptelefoon steeds de verbinding als ik mijn laptop open", "deep"),
        ("en", "what's the difference between the X1 and X3 earbuds for running", "deep"),
    ]
    for code, said, want in ROUTES:
        got = route(said, cat, "Meta-Llama-3.3-70B-Instruct",
                    has_order="44812" in said, code=code)["tier"]
        wrong += got != want
        print(f"  {'ok  ' if got == want else 'FAIL'} {code}  {got:8} (wanted {want:8}) {said[:44]}")

    MOODS = [
        ("this is RIDICULOUS, it still hasn't arrived!!", "frustrated"),
        ("esto es inaceptable, otra vez lo mismo", "frustrated"),
        ("c'est inacceptable, encore une fois", "frustrated"),
        ("das ist schon wieder unmöglich", "frustrated"),
        ("هذي المرة الثالثة، زهقت", "frustrated"),
        ("what do you mean, which one?", "confused"),
        ("no entiendo, ¿cuál de los dos?", "confused"),
        ("ما فهمت، ممكن تعيد", "confused"),
        ("just tell me quickly", "hurried"),
        ("rápido por favor, tengo prisa", "hurried"),
        ("بسرعة لو سمحت، مستعجل", "hurried"),
        ("ok thanks", "neutral"),
        ("متى يوصل طلبي", "neutral"),
    ]
    print()
    for said, want in MOODS:
        got = mood(said)
        wrong += got != want
        print(f"  {'ok  ' if got == want else 'FAIL'} {got:10} (wanted {want:10}) {said[:40]}")
    print(f"\n  {len(ROUTES) + len(MOODS) - wrong}/{len(ROUTES) + len(MOODS)} correct\n")
    print(split_sentences("Your order is late. It shipped on the 21st. And so"))
    print([has_claim(s) for s in ("Sure, let me look.", "It's 11 working days late.",
                                  "Refunds take five working days.", "Anything else?")])
