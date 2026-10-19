"""Which language the caller just spoke.

Stopword counting rather than a dependency or a model call. Detection has to
happen before the turn starts — it picks the synthesis language — so it must
cost nothing, and the alternative is a round trip on every utterance to answer
a question that a hundred common words settle.

Cartesia's sonic models take a language code per generation, so switching is a
parameter, not a different voice.
"""

import re

# Function words, not vocabulary. What a caller asks about ("order", "delivery")
# is the same noun in half of Europe; how they ask it — the articles, the
# pronouns, the question words — is not. These are the fifty or so commonest
# of those per language.
#
# Overlap between the lists is fine and expected ("no" is Spanish, Italian and
# Portuguese). A word shared by several languages scores for all of them and so
# decides nothing; a word unique to one is worth double. That weighting is
# computed below rather than curated, so adding a word can never quietly break
# the balance of a list it is not in.
MARKERS = {
    "en": ("the", "and", "you", "what", "have", "with", "this", "that", "for",
           "can", "please", "thanks", "hello", "would", "about", "when", "there",
           "they", "from", "will", "could", "should", "been", "were", "does",
           "doesn't", "didn't", "isn't", "won't", "i'm", "it's", "we're",
           "still", "just", "much", "very", "sorry", "thank", "yesterday",
           "tomorrow", "because", "these", "those", "which", "where", "why",
           # Listed here because they are also ordinary English, not because
           # English needs them: "no", "was", "is" and "come" each sit in
           # another list above, and without an English claim on them one of
           # them alone would carry a sentence into the wrong language.
           # "no it hasn't arrived yet" was detected as Spanish.
           "no", "me", "mi", "plus", "die", "war", "hat", "was", "per",
           "come", "fare", "do", "is", "van", "over", "so", "an", "in", "on"),
    "es": ("que", "de", "no", "la", "el", "es", "por", "una", "con", "para",
           "hola", "gracias", "cuando", "cuándo", "donde", "dónde", "puedo",
           "quiero", "cómo", "pero", "muy", "está", "usted", "sí", "hacer",
           "hacéis", "envío", "mi", "me", "se", "los", "las", "del", "al",
           "un", "ya", "más", "todavía", "porque", "cuál", "qué", "quién",
           "tengo", "tiene", "puede", "hay", "está", "están", "sobre", "desde",
           "hasta", "también", "ahora", "nada", "algo", "vosotros", "ustedes",
           "pedido", "pedidos", "devolver"),
    "fr": ("je", "le", "pas", "vous", "une", "est", "et", "des", "pour", "que",
           "bonjour", "merci", "comment", "quand", "pouvez", "voudrais", "avec",
           "dans", "mais", "votre", "être", "livrez", "aussi", "ne", "ce",
           "cette", "les", "du", "au", "aux", "sont", "suis", "nous", "ils",
           "elle", "quel", "quelle", "quels", "quelles", "vos", "mon", "ma",
           "mes", "plus", "très", "toujours", "encore", "déjà", "chez", "sans",
           "parce", "pourquoi", "où", "qui", "commande", "livraison", "retour"),
    "de": ("ich", "nicht", "und", "das", "die", "der", "sie", "ein", "mit",
           "ist", "hallo", "danke", "wann", "können", "möchte", "wie", "nach",
           "haben", "wir", "auch", "noch", "bitte", "liefern", "aber", "meine",
           "mein", "meinen", "kommt", "wurde", "werden", "wird", "war", "sind",
           "hat", "habe", "eine", "einen", "den", "dem", "für", "von", "zu",
           "auf", "schon", "warum", "was", "wer", "wo", "welche", "ihre",
           "ihnen", "bestellung", "lieferung", "zurückgeben"),
    "it": ("non", "che", "di", "il", "la", "per", "sono", "una", "con", "come",
           "ciao", "grazie", "quando", "dove", "posso", "vorrei", "molto",
           "anche", "questo", "ma", "più", "fate", "mio", "mia", "miei", "sua",
           "del", "della", "degli", "nel", "gli", "lo", "un", "uno", "quale",
           "quali", "vostri", "vostro", "vostra", "perché", "ancora", "già",
           "adesso", "qualcosa", "niente", "cosa", "chi", "ordine", "consegna",
           "restituire", "ho", "ha", "hanno", "essere", "fare"),
    "pt": ("não", "que", "de", "uma", "para", "com", "está", "você", "obrigado",
           "olá", "quando", "onde", "posso", "quero", "como", "muito", "bem",
           "obrigada", "mas", "fazem", "entrega", "minha", "meu", "minhas",
           "meus", "sua", "seu", "vosso", "vossa", "qual", "quais", "é", "são",
           "tenho", "tem", "do", "da", "dos", "das", "ao", "à", "pelo", "pela",
           "ainda", "já", "porque", "porquê", "quem", "isso", "aqui", "encomenda",
           "devolver", "pedido", "fazer", "ser"),
    "nl": ("het", "een", "niet", "van", "ik", "en", "dat", "is", "hallo",
           "dank", "wanneer", "waar", "kan", "wil", "hoe", "maar", "ook", "mijn",
           "jullie", "leveren", "zijn", "wat", "wie", "waarom", "welke", "deze",
           "die", "dit", "heb", "heeft", "hebben", "wordt", "werd", "nog",
           "al", "graag", "alstublieft", "bedankt", "uw", "jouw", "naar", "met",
           "voor", "bij", "over", "bestelling", "levering", "retourneren",
           "terugsturen", "openingstijden"),
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

# word -> every language that claims it. Built once, so the lists above stay
# the only thing anyone has to edit.
OWNERS = {}
for _code, _markers in MARKERS.items():
    for _word in _markers:
        OWNERS.setdefault(_word, []).append(_code)


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
        for code in OWNERS.get(word, ()):
            scores[code] += 2 if len(OWNERS[word]) == 1 else 1

    lowered = (text or "").lower()
    for code, letters in HINTS.items():
        if any(ch in lowered for ch in letters):
            scores[code] += 2

    best = max(scores, key=scores.get)
    if scores[best] < MIN_EVIDENCE:
        return current

    # A clear winner, or stay put. Spanish and Portuguese share a lot, and a
    # tie on function words alone is exactly the case the accents settle:
    # "é" and "ã" are Portuguese, "¿" and "ñ" are Spanish.
    tied = [c for c, s in scores.items() if s == scores[best]]
    if len(tied) > 1:
        marked = [c for c in tied if any(ch in lowered for ch in HINTS.get(c, ""))]
        if len(marked) != 1:
            return current
        return marked[0]
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
        # Every one of these came back "en" before the lists were widened,
        # so the caller got an English holding phrase and an English answer.
        ("Quando chega a minha encomenda?", "pt"),
        ("Como devolvo um produto?", "pt"),
        ("A minha encomenda chegou danificada, o que faço?", "pt"),
        ("Qual é o vosso horário?", "pt"),
        ("Quali sono i vostri orari?", "it"),
        ("Wat zijn jullie openingstijden?", "nl"),
        ("Quels sont vos horaires d'ouverture ?", "fr"),
        ("Wann kommt meine Bestellung an?", "de"),
        ("Comment puis-je retourner un article ?", "fr"),
        ("Wie kann ich einen Artikel zurückgeben?", "de"),
        ("Come posso restituire un prodotto?", "it"),
        ("Hoe kan ik een product retourneren?", "nl"),
        ("¿Cómo devuelvo un producto?", "es"),
        ("Mijn bestelling is beschadigd aangekomen, wat nu?", "nl"),
        ("Meine Bestellung ist beschädigt angekommen, was nun?", "de"),
        ("Il mio ordine è in ritardo, cosa faccio?", "it"),
        ("Mi pedido llegó roto, ¿qué hago?", "es"),
        ("Ma commande est en retard, que faire ?", "fr"),
        ("¿Cuál es vuestro horario de atención?", "es"),
        ("Quali sono i vostri orari di apertura?", "it"),
        # English must not be dragged off by a stray lookalike.
        ("no it has not arrived yet", "en"),
        ("is my order out for delivery", "en"),
        ("what are your opening hours", "en"),
        ("I would like a refund please", "en"),
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
