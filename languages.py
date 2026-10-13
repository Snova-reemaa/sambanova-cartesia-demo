"""Which language the caller just spoke.

Stopword counting rather than a dependency or a model call. Detection has to
happen before the turn starts — it picks the synthesis language — so it must
cost nothing, and the alternative is a round trip on every utterance to answer
a question that a hundred common words settle.

Cartesia's sonic models take a language code per generation, so switching is a
parameter, not a different voice.
"""

import re

# The most common words in each language, chosen to overlap as little as
# possible with the others. "the" is not here because it appears in English
# loanwords everywhere; "que" and "ich" are decisive.
MARKERS = {
    "en": ("the", "and", "you", "what", "have", "with", "this", "that", "for",
           "can", "please", "thanks", "hello", "would", "about", "when"),
    "es": ("que", "de", "no", "la", "el", "es", "por", "una", "con", "para",
           "hola", "gracias", "cuando", "donde", "puedo", "quiero", "cómo", "pero",
           "muy", "está", "usted", "sí", "hacer", "hacéis", "envío"),
    "fr": ("je", "le", "pas", "vous", "une", "est", "et", "des", "pour", "que",
           "bonjour", "merci", "comment", "quand", "pouvez", "voudrais", "avec",
           "dans", "mais", "votre", "être", "livrez", "aussi"),
    "de": ("ich", "nicht", "und", "das", "die", "der", "sie", "ein", "mit",
           "ist", "hallo", "danke", "wann", "können", "möchte", "wie", "nach", "haben",
           "wir", "auch", "noch", "bitte", "liefern", "aber"),
    "it": ("non", "che", "di", "il", "la", "per", "sono", "una", "con", "come",
           "ciao", "grazie", "quando", "dove", "posso", "vorrei", "molto", "anche",
           "questo", "ma", "più", "fate"),
    "pt": ("não", "que", "de", "uma", "para", "com", "está", "você", "obrigado",
           "olá", "quando", "onde", "posso", "quero", "como", "muito", "bem",
           "obrigada", "mas", "fazem", "entrega"),
    "nl": ("het", "een", "niet", "van", "ik", "en", "dat", "is", "hallo",
           "dank", "wanneer", "waar", "kan", "wil", "hoe", "maar", "ook", "mijn",
           "jullie", "leveren"),
}

# Letters that only appear in some of these languages at all.
HINTS = {
    "es": "ñ¿¡",
    "fr": "çàèùœ",
    "de": "äöüß",
    "pt": "ãõç",
    "it": "àèìòù",
}

# Arabic needs no word list: the script settles it on its own.
ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

SUPPORTED = tuple(MARKERS) + ("ar",)

NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ar": "Arabic",
}

# Short, unambiguous English words that are not in MARKERS because they are
# too common to separate languages — but enough to say "this is English, do
# not pay for a translation".
EVERYDAY_EN = {"hi", "hey", "there", "yeah", "yes", "okay", "ok", "is", "are",
               "my", "it", "how", "why", "who", "where", "does", "do", "not",
               "i", "me", "your", "order", "thank", "much", "good", "great",
               "need", "want", "help", "tell", "explain", "know", "if", "of"}


def needs_translation(text):
    """Could this be a non-English question, worth translating for retrieval?

    False for anything that reads as English: that turn already went through
    retrieval in its own words, and a translation round trip cannot help it.
    """
    lowered = (text or "").lower()
    if ARABIC.search(lowered):
        return True
    if any(ord(ch) > 127 and ch.isalpha() for ch in lowered):
        return True
    words = re.findall(r"[a-z']+", lowered)
    if not words:
        return False
    for code, markers in MARKERS.items():
        if code != "en" and any(w in markers for w in words):
            english = sum(w in MARKERS["en"] or w in EVERYDAY_EN for w in words)
            other = sum(w in markers for w in words)
            if other > english:
                return True
    return False

# Below this, the utterance is too short or too ambiguous to act on — keep
# whatever language the call was already in rather than flapping between two.
MIN_EVIDENCE = 2


def detect(text, current="en"):
    """Best guess at the language, falling back to what we were already using."""
    if len(ARABIC.findall(text or "")) >= 2:
        return "ar"
    words = re.findall(r"[^\W\d_]+", (text or "").lower(), re.UNICODE)
    if not words:
        return current

    scores = {code: 0 for code in MARKERS}
    for word in words:
        for code, markers in MARKERS.items():
            if word in markers:
                scores[code] += 1

    lowered = (text or "").lower()
    for code, letters in HINTS.items():
        if any(ch in lowered for ch in letters):
            scores[code] += 2

    best = max(scores, key=scores.get)
    if scores[best] < MIN_EVIDENCE:
        return current

    # A clear winner, or stay put. Spanish and Portuguese share a lot.
    runner_up = max((s for c, s in scores.items() if c != best), default=0)
    if scores[best] == runner_up:
        return current
    return best


def name(code):
    return NAMES.get(code, "English")


if __name__ == "__main__":
    SAMPLES = [
        ("livrez-vous en France?", "fr"),
        ("liefern Sie nach Deutschland?", "de"),
        ("¿hacéis envíos a España?", "es"),
        ("no it hasn't arrived yet", "en"),
        ("that's fine thanks", "en"),
        ("Can you tell me when my order will arrive?", "en"),
        ("¿Cuándo llega mi pedido? Gracias", "es"),
        ("Bonjour, je voudrais savoir quand ma commande arrive", "fr"),
        ("Hallo, ich möchte wissen wann meine Bestellung ankommt", "de"),
        ("Ciao, quando arriva il mio ordine?", "it"),
        ("Olá, quando é que chega a minha encomenda?", "pt"),
        ("Hallo, wanneer komt mijn bestelling aan?", "nl"),
        ("ok", "en"),
        ("hmm", "en"),
        ("متى يوصل طلبي؟", "ar"),
        ("مرحبا، عندي مشكلة في الطلب", "ar"),
    ]
    wrong = 0
    for text, want in SAMPLES:
        got = detect(text)
        if got != want:
            wrong += 1
        print(f"  {'ok  ' if got == want else 'FAIL'} {got}  (wanted {want})  {text[:46]}")
    total = len(SAMPLES)
    # (text, should translate for retrieval)
    TRANSLATE = [
        ("hi there", False),
        ("what is a GPU", False),
        ("do you price match with competitors", False),
        ("livrez-vous en France?", True),
        ("¿hacéis envíos a España?", True),
        ("متى يوصل طلبي؟", True),
        ("liefern Sie nach Deutschland?", True),
        ("no it hasn't arrived yet", False),
    ]
    for text, want in TRANSLATE:
        got = needs_translation(text)
        if got != want:
            wrong += 1
        print(f"  {'ok  ' if got == want else 'FAIL'} translate={got}  {text}")
    total += len(TRANSLATE)
    print(f"\n  {total - wrong}/{total} correct")
