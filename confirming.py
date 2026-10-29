"""Confirming a name or an email before it is saved, in eight languages.

Three tries, each a different way in, because the second time a
recogniser mishears a name it mishears it the same way:

    1. heard          read back what was heard: "Got it, Reema Alotaibi,
                      did I get that right?"
    2. spelled        on a no, ask for it letter by letter, in the letter
                      names of the caller's language, and read that back
    3. typed          on a second no, ask them to type it in the box on the
                      screen, and keep exactly what they type

The step is code, not the model: what is said here is a fixed sentence in
the caller's language, and whether a yes was a yes is decided below. The
model never gets to announce that something was saved.

Pure: no server, no store. server.py drives it and decides what a
confirmed value is for.
"""

import re
import unicodedata

import memory
import slots

LANGUAGES = ("en", "es", "fr", "de", "it", "pt", "nl", "ar")

# What is said at each point, per language. {v} is the value, {letters}
# the same spelled out the way slots.spell says it.
SAYS = {
    "readback_name": {
        "en": "Got it, {v}. Did I get that right?",
        "es": "Entendido, {v}. ¿Lo he entendido bien?",
        "fr": "D'accord, {v}. C'est bien ça ?",
        "de": "Alles klar, {v}. Habe ich das richtig verstanden?",
        "it": "Perfetto, {v}. Ho capito bene?",
        "pt": "Certo, {v}. Percebi bem?",
        "nl": "Goed, {v}. Heb ik dat goed verstaan?",
        "ar": "تمام، {v}. هل فهمته صح؟",
    },
    "readback_email": {
        "en": "Let me read that back: {letters}. Did I get that right?",
        "es": "Se lo leo: {letters}. ¿Es correcto?",
        "fr": "Je vous le relis : {letters}. C'est bien ça ?",
        "de": "Ich lese es vor: {letters}. Stimmt das?",
        "it": "Glielo rileggo: {letters}. È corretto?",
        "pt": "Vou ler de volta: {letters}. Está correto?",
        "nl": "Ik lees het even terug: {letters}. Klopt dat?",
        "ar": "دعني أقرأه لك: {letters}. صحيح؟",
    },
    "readback_spelled": {
        "en": "So that's {letters}: {v}. Is that right?",
        "es": "Entonces es {letters}: {v}. ¿Es correcto?",
        "fr": "Donc c'est {letters} : {v}. C'est correct ?",
        "de": "Also {letters}: {v}. Stimmt das?",
        "it": "Quindi {letters}: {v}. È corretto?",
        "pt": "Então é {letters}: {v}. Está correto?",
        "nl": "Dus {letters}: {v}. Klopt dat?",
        "ar": "إذن {letters}: {v}. صحيح؟",
    },
    "ask_spell_name": {
        "en": "Sorry about that. Could you spell your name for me, letter by letter?",
        "es": "Disculpe. ¿Podría deletrearme su nombre, letra por letra?",
        "fr": "Pardon. Pourriez-vous m'épeler votre nom, lettre par lettre ?",
        "de": "Entschuldigung. Könnten Sie mir Ihren Namen buchstabieren, Buchstabe für Buchstabe?",
        "it": "Mi scusi. Può dettarmi il suo nome, lettera per lettera?",
        "pt": "Desculpe. Pode soletrar o seu nome, letra a letra?",
        "nl": "Sorry. Kunt u uw naam spellen, letter voor letter?",
        "ar": "عذرًا. ممكن تتهجّى اسمك حرفًا حرفًا؟",
    },
    "ask_spell_email": {
        "en": "Sorry about that. Could you spell the email address for me, letter by letter?",
        "es": "Disculpe. ¿Podría deletrearme el correo electrónico, letra por letra?",
        "fr": "Pardon. Pourriez-vous m'épeler l'adresse e-mail, lettre par lettre ?",
        "de": "Entschuldigung. Könnten Sie mir die E-Mail-Adresse buchstabieren, Buchstabe für Buchstabe?",
        "it": "Mi scusi. Può dettarmi l'indirizzo email, lettera per lettera?",
        "pt": "Desculpe. Pode soletrar o endereço de e-mail, letra a letra?",
        "nl": "Sorry. Kunt u het e-mailadres spellen, letter voor letter?",
        "ar": "عذرًا. ممكن تتهجّى البريد الإلكتروني حرفًا حرفًا؟",
    },
    "ask_type_name": {
        "en": "Let's try another way: please type your name in the box on the screen.",
        "es": "Probemos de otra forma: escriba su nombre en el recuadro de la pantalla, por favor.",
        "fr": "Essayons autrement : tapez votre nom dans la case à l'écran, s'il vous plaît.",
        "de": "Versuchen wir es anders: Bitte tippen Sie Ihren Namen in das Feld auf dem Bildschirm.",
        "it": "Proviamo in un altro modo: scriva il suo nome nella casella sullo schermo, per favore.",
        "pt": "Vamos tentar de outra forma: escreva o seu nome na caixa no ecrã, por favor.",
        "nl": "Laten we het anders proberen: typ uw naam in het vak op het scherm.",
        "ar": "خلّينا نجرب طريقة ثانية: اكتب اسمك في المربع على الشاشة، لو سمحت.",
    },
    "ask_type_email": {
        "en": "Let's try another way: please type the email address in the box on the screen.",
        "es": "Probemos de otra forma: escriba el correo electrónico en el recuadro de la pantalla, por favor.",
        "fr": "Essayons autrement : tapez l'adresse e-mail dans la case à l'écran, s'il vous plaît.",
        "de": "Versuchen wir es anders: Bitte tippen Sie die E-Mail-Adresse in das Feld auf dem Bildschirm.",
        "it": "Proviamo in un altro modo: scriva l'indirizzo email nella casella sullo schermo, per favore.",
        "pt": "Vamos tentar de outra forma: escreva o endereço de e-mail na caixa no ecrã, por favor.",
        "nl": "Laten we het anders proberen: typ het e-mailadres in het vak op het scherm.",
        "ar": "خلّينا نجرب طريقة ثانية: اكتب البريد الإلكتروني في المربع على الشاشة، لو سمحت.",
    },
    "yes_or_no": {
        "en": "Sorry, just to check: is that right, yes or no?",
        "es": "Perdone, solo para confirmar: ¿es correcto, sí o no?",
        "fr": "Pardon, juste pour vérifier : c'est correct, oui ou non ?",
        "de": "Entschuldigung, nur zur Sicherheit: Stimmt das, ja oder nein?",
        "it": "Mi scusi, solo per conferma: è corretto, sì o no?",
        "pt": "Desculpe, só para confirmar: está correto, sim ou não?",
        "nl": "Sorry, even checken: klopt dat, ja of nee?",
        "ar": "عذرًا، للتأكد فقط: صحيح، نعم أو لا؟",
    },
    "bad_typed_email": {
        "en": "That doesn't look like an email address. Could you check it and type it again?",
        "es": "Eso no parece una dirección de correo. ¿Puede revisarla y escribirla de nuevo?",
        "fr": "Cela ne ressemble pas à une adresse e-mail. Pouvez-vous la vérifier et la retaper ?",
        "de": "Das sieht nicht wie eine E-Mail-Adresse aus. Könnten Sie sie prüfen und noch einmal eingeben?",
        "it": "Non sembra un indirizzo email. Può controllarlo e scriverlo di nuovo?",
        "pt": "Isso não parece um endereço de e-mail. Pode verificar e escrever novamente?",
        "nl": "Dat lijkt geen e-mailadres. Kunt u het controleren en opnieuw typen?",
        "ar": "هذا لا يبدو بريدًا إلكترونيًا. ممكن تتأكد منه وتكتبه مرة ثانية؟",
    },
    # The typed box's own label and button, in the caller's language.
    "box_name": {
        "en": "Type your name", "es": "Escriba su nombre", "fr": "Tapez votre nom",
        "de": "Ihren Namen eingeben", "it": "Scriva il suo nome",
        "pt": "Escreva o seu nome", "nl": "Typ uw naam", "ar": "اكتب اسمك",
    },
    "box_email": {
        "en": "Type your email address", "es": "Escriba su correo electrónico",
        "fr": "Tapez votre adresse e-mail", "de": "E-Mail-Adresse eingeben",
        "it": "Scriva il suo indirizzo email", "pt": "Escreva o seu e-mail",
        "nl": "Typ uw e-mailadres", "ar": "اكتب بريدك الإلكتروني",
    },
    "box_button": {
        "en": "Save", "es": "Guardar", "fr": "Enregistrer", "de": "Speichern",
        "it": "Salva", "pt": "Guardar", "nl": "Opslaan", "ar": "حفظ",
    },
    "thanks": {
        "en": "Thank you, {v}.", "es": "Gracias, {v}.", "fr": "Merci, {v}.",
        "de": "Danke, {v}.", "it": "Grazie, {v}.", "pt": "Obrigado, {v}.",
        "nl": "Dank u, {v}.", "ar": "شكرًا، {v}.",
    },
    "ask_email": {
        "en": "What's the email address on your account?",
        "es": "¿Cuál es el correo electrónico de su cuenta?",
        "fr": "Quelle est l'adresse e-mail de votre compte ?",
        "de": "Wie lautet die E-Mail-Adresse Ihres Kontos?",
        "it": "Qual è l'indirizzo email del suo account?",
        "pt": "Qual é o endereço de e-mail da sua conta?",
        "nl": "Wat is het e-mailadres van uw account?",
        "ar": "ما هو البريد الإلكتروني المسجّل في حسابك؟",
    },
    "code_sent": {
        "en": "I've sent a six-digit code to that address. Please read it back to me when you have it.",
        "es": "He enviado un código de seis dígitos a esa dirección. Léamelo cuando lo tenga.",
        "fr": "J'ai envoyé un code à six chiffres à cette adresse. Lisez-le-moi quand vous l'avez.",
        "de": "Ich habe einen sechsstelligen Code an diese Adresse geschickt. Lesen Sie ihn mir bitte vor, sobald Sie ihn haben.",
        "it": "Ho inviato un codice di sei cifre a quell'indirizzo. Me lo legga quando lo ha.",
        "pt": "Enviei um código de seis dígitos para esse endereço. Leia-mo quando o tiver.",
        "nl": "Ik heb een zescijferige code naar dat adres gestuurd. Leest u hem voor zodra u hem hebt.",
        "ar": "أرسلت رمزًا من ستة أرقام إلى هذا العنوان. اقرأه لي لما يوصلك.",
    },
    "unavailable": {
        "en": "I can't pull up accounts right now, but I can still help with what you need.",
        "es": "Ahora mismo no puedo consultar cuentas, pero puedo ayudarle con lo que necesite.",
        "fr": "Je ne peux pas consulter les comptes pour le moment, mais je peux quand même vous aider.",
        "de": "Ich kann gerade keine Konten aufrufen, aber ich helfe Ihnen trotzdem gern weiter.",
        "it": "Al momento non posso consultare gli account, ma posso comunque aiutarla.",
        "pt": "Neste momento não consigo consultar contas, mas posso ajudar no que precisar.",
        "nl": "Ik kan nu geen accounts opzoeken, maar ik kan u wel verder helpen.",
        "ar": "ما أقدر أفتح الحسابات الآن، لكن أقدر أساعدك في اللي تحتاجه.",
    },
    "too_many": {
        "en": "There have been too many code attempts. Please give it a quarter of an hour; until then I can help with anything that doesn't need your account.",
        "es": "Ha habido demasiados intentos con el código. Espere un cuarto de hora; mientras tanto puedo ayudarle con lo que no necesite su cuenta.",
        "fr": "Il y a eu trop de tentatives de code. Patientez un quart d'heure ; d'ici là, je peux vous aider pour tout ce qui ne demande pas votre compte.",
        "de": "Es gab zu viele Code-Versuche. Bitte warten Sie eine Viertelstunde; bis dahin helfe ich gern bei allem, wofür Ihr Konto nicht nötig ist.",
        "it": "Ci sono stati troppi tentativi con il codice. Aspetti un quarto d'ora; nel frattempo posso aiutarla con ciò che non richiede il suo account.",
        "pt": "Houve demasiadas tentativas de código. Aguarde um quarto de hora; entretanto posso ajudar no que não precise da sua conta.",
        "nl": "Er zijn te veel codepogingen geweest. Wacht een kwartier; tot die tijd help ik u graag met alles waarvoor uw account niet nodig is.",
        "ar": "صارت محاولات كثيرة للرمز. انتظر ربع ساعة لو سمحت، وإلى ذلك الوقت أقدر أساعدك في أي شيء ما يحتاج حسابك.",
    },
}


def say(key, code, **kw):
    table = SAYS[key]
    return table.get(code, table["en"]).format(**kw)


# --------------------------------------------------------------- yes or no

YES = {
    "en": ("yes", "yeah", "yep", "yup", "correct", "that's right", "thats right",
           "right", "exactly", "sure", "it is", "that's it"),
    "es": ("sí", "si", "correcto", "exacto", "eso es", "así es", "claro", "vale"),
    "fr": ("oui", "ouais", "exact", "exactement", "c'est ça", "c'est bien ça",
           "correct", "tout à fait"),
    "de": ("ja", "genau", "richtig", "stimmt", "korrekt", "jawohl", "passt"),
    "it": ("sì", "si", "esatto", "giusto", "corretto", "certo", "esattamente"),
    "pt": ("sim", "isso", "correto", "certo", "exato", "exatamente", "é isso"),
    "nl": ("ja", "klopt", "correct", "precies", "juist", "inderdaad", "jazeker"),
    "ar": ("نعم", "ايوه", "أيوه", "ايوا", "أيوا", "اي", "إي", "صح", "صحيح",
           "تمام", "بالضبط", "مضبوط", "اكيد", "أكيد"),
}
NO = {
    "en": ("no", "nope", "nah", "wrong", "not quite", "incorrect", "not right",
           "that's not", "thats not"),
    "es": ("no", "incorrecto", "mal", "no es", "para nada"),
    "fr": ("non", "pas tout à fait", "incorrect", "faux", "pas ça"),
    "de": ("nein", "falsch", "nicht ganz", "stimmt nicht", "nicht richtig"),
    "it": ("no", "sbagliato", "non è", "non proprio", "non corretto"),
    "pt": ("não", "nao", "errado", "não é", "incorreto"),
    "nl": ("nee", "neen", "fout", "niet goed", "klopt niet", "niet helemaal"),
    "ar": ("لا", "لأ", "غلط", "خطأ", "خطا", "مو صحيح", "مش صحيح", "ليس صحيح"),
}


def _phrase(words):
    """A pattern that finds any of these words or phrases, whole."""
    alts = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in alts) + r")(?!\w)",
                      re.I)


_YES = {c: _phrase(YES[c] + YES["en"][:3]) for c in YES}
_NO = {c: _phrase(NO[c] + NO["en"][:1]) for c in NO}


def yes_or_no(said, code="en"):
    """True, False, or None when it was neither. The earlier word wins:
    "no, yes it is" is a no. "No problem, yes" is rare enough to lose."""
    text = (said or "").replace("’", "'").strip()
    y = _YES.get(code, _YES["en"]).search(text)
    n = _NO.get(code, _NO["en"]).search(text)
    if y and n:
        # "that's not right" contains "right": the longer, earlier phrase wins.
        if n.start() <= y.start() or (n.start() <= y.end() and n.end() >= y.end()):
            return False
        return True
    if n:
        return False
    if y:
        return True
    return None


# ---------------------------------------------------------- spelling, heard

# Arabic letter names, for a name spelled in Arabic script. Latin letters
# in an Arabic call use the English names, as Arabic speakers do.
ARABIC_LETTERS = {
    "ألف": "ا", "الف": "ا", "أليف": "ا", "باء": "ب", "با": "ب", "تاء": "ت",
    "تا": "ت", "ثاء": "ث", "ثا": "ث", "جيم": "ج", "حاء": "ح", "حا": "ح",
    "خاء": "خ", "خا": "خ", "دال": "د", "ذال": "ذ", "راء": "ر", "را": "ر",
    "زاي": "ز", "زين": "ز", "سين": "س", "شين": "ش", "صاد": "ص", "ضاد": "ض",
    "طاء": "ط", "طا": "ط", "ظاء": "ظ", "ظا": "ظ", "عين": "ع", "غين": "غ",
    "فاء": "ف", "فا": "ف", "قاف": "ق", "كاف": "ك", "لام": "ل", "ميم": "م",
    "نون": "ن", "هاء": "ه", "ها": "ه", "واو": "و", "ياء": "ي", "يا": "ي",
    "همزة": "ء", "تاء مربوطة": "ة",
}

# Extra letters that are names of their own in some languages.
EXTRA_LETTERS = {
    "es": {"eñe": "ñ", "ene con tilde": "ñ"},
    "de": {"eszett": "ß", "scharfes s": "ß", "a umlaut": "ä", "o umlaut": "ö",
           "u umlaut": "ü"},
    "fr": {"c cédille": "ç"},
    "pt": {"cê cedilha": "ç"},
}

# Spoken after a letter, to put a mark on it.
MARKS = {
    "́": ("acute", "with an acute", "accent aigu", "aigu", "con tilde",
               "acento", "con acento", "acento agudo", "agudo", "accento acuto",
               "acuto", "akut", "met accent aigu"),
    "̀": ("grave", "with a grave", "accent grave", "acento grave",
               "accento grave", "met accent grave"),
    "̂": ("circumflex", "accent circonflexe", "circonflexe", "circunflexo",
               "acento circunflexo", "circonflesso", "zirkumflex", "chapéu"),
    "̈": ("umlaut", "with an umlaut", "diaeresis", "tréma", "trema",
               "diéresis", "dieresis", "con diéresis", "met trema"),
    "̧": ("cedilla", "cédille", "cedilha", "cedille", "cediglia"),
    "̃": ("til", "com til", "tilde"),
}

SPACE_WORDS = ("space", "espacio", "espace", "leerzeichen", "spazio", "espaço",
               "spatie", "مسافة", "new word")


def _letter_names(code):
    """Spoken name -> character, for this language first, then English."""
    names = {}
    names.update({v: k for k, v in slots.SAY_LETTER.items()})
    names.update({k: v for k, v in memory.LETTER_WORDS.items()})
    names.update({v: k for k, v in slots.SAY_LETTER_BY_LANGUAGE.get(code, {}).items()})
    names.update(EXTRA_LETTERS.get(code, {}))
    if code == "ar":
        names.update(ARABIC_LETTERS)
    return {k.lower(): v for k, v in names.items()}


def _symbol_names(code):
    out = {"at": "@", "dot": ".", "point": ".", "dash": "-", "hyphen": "-",
           "underscore": "_", "plus": "+", "apostrophe": "'"}
    out.update({v.lower(): k for k, v in slots.SAY_SYMBOL.items()})
    out.update({v.lower(): k for k, v in slots.SYMBOLS_BY_LANGUAGE.get(code, {}).items()})
    return out


def _tokens(said):
    # Letters run together as "R-E-E-M-A" or "R, E, E, M, A" as often as
    # with spaces; split on all of them, keep letters of any script.
    text = unicodedata.normalize("NFC", (said or "").replace("’", "'"))
    return [t for t in re.split(r"[\s,;:\-–—]+", text.strip().lower()) if t]


def decode(said, field, code="en"):
    """What a caller spelled out, or "" if it could not be read as letters.

    Longest phrase first, so "uve doble" is w and not v then a stray word.
    A name comes back with each word capitalised; an email lower case.
    """
    letters = _letter_names(code)
    symbols = _symbol_names(code) if field == "email" else {}
    marks = {phrase: mark for mark, phrases in MARKS.items() for phrase in phrases}
    spaces = set(SPACE_WORDS)
    words = _tokens(said)
    # Fillers people put round a spelling.
    skip = {"it's", "its", "is", "es", "c'est", "das", "ist", "è", "é", "het",
            "is", "that's", "the", "letter", "letters", "then", "and", "y", "et",
            "und", "e", "en", "و", "um", "uh", "erm", "my", "name", "email",
            "address"} - set(letters) - set(symbols)
    out = []
    i, misses = 0, 0
    while i < len(words):
        matched = False
        for size in (3, 2, 1):
            chunk = " ".join(words[i:i + size])
            if len(words) - i < size:
                continue
            if chunk in marks and out and out[-1] not in (" ", "@", "."):
                out[-1] = unicodedata.normalize("NFC", out[-1] + marks[chunk])
            elif chunk in spaces and field == "name":
                if out and out[-1] != " ":
                    out.append(" ")
            elif chunk in symbols:
                out.append(symbols[chunk])
            elif chunk in letters:
                out.append(letters[chunk])
            elif size == 1 and len(chunk) == 1 and (chunk.isalpha() or chunk.isdigit()):
                out.append(chunk)
            elif size == 1 and chunk.isdigit():
                out.append(chunk)
            else:
                continue
            i += size
            matched = True
            break
        if not matched:
            if words[i] not in skip:
                misses += 1
            i += 1
    text = "".join(out).strip()
    # Mostly not letters at all: this was a sentence, not a spelling.
    if not text or misses > max(1, len(out) // 3):
        return ""
    if field == "email":
        text = text.lower()
        return text if EMAIL_TYPED.fullmatch(text) else ""
    return " ".join(w[:1].upper() + w[1:] for w in text.split())


def say_spelled(value, field, code="en"):
    """The value letter by letter, as it will be read back."""
    if field == "name":
        return ", ".join(slots.spell(w, code) for w in value.split())
    return slots.spell(value, code)


# ---------------------------------------------------------------- typing

# Typed into the box. Any script: an address can have a non-ASCII local
# part or domain, so this only asks for one @, a dot after it and no
# spaces, and leaves the rest to whether the code arrives.
EMAIL_TYPED = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@.]{2,}")


def typed_value(text, field):
    """What was typed, kept exactly (bar the spaces round it), or None if it
    cannot be the thing asked for."""
    value = unicodedata.normalize("NFC", (text or "").strip())
    if not value or len(value) > 120:
        return None
    if field == "email" and not EMAIL_TYPED.fullmatch(value):
        return None
    return value


# ---------------------------------------------------------------- the step

class Step:
    """One name or email being confirmed."""

    def __init__(self, field, value, code="en"):
        self.field = field            # "name" or "email"
        self.value = value            # what we currently think it is
        self.tries = 1                # spoken tries so far
        self.stage = "readback"       # readback, spell, typed
        self.spelled = False          # the value came from a spelling
        self.code = code

    def readback(self):
        if self.spelled:
            return say("readback_spelled", self.code, v=self.value,
                       letters=say_spelled(self.value, self.field, self.code))
        if self.field == "email":
            return say("readback_email", self.code,
                       letters=say_spelled(self.value, "email", self.code))
        return say("readback_name", self.code, v=self.value)

    def opening(self):
        return {"say": self.readback(), "stage": self.stage}

    def heard(self, said, code=None):
        """A spoken reply. {"done": value} or {"say": ..., "stage": ...},
        or None while waiting for the box (a spoken reply then is not this
        step's business)."""
        self.code = code or self.code
        if self.stage == "typed":
            return None
        if self.stage == "spell":
            spelled = decode(said, self.field, self.code)
            if not spelled:
                return self._next_try()
            self.value, self.spelled, self.stage = spelled, True, "readback"
            return {"say": self.readback(), "stage": self.stage}
        agreed = yes_or_no(said, self.code)
        if agreed is True:
            return {"done": self.value, "how": "spelled" if self.spelled else "heard"}
        if agreed is False:
            return self._next_try()
        return {"say": say("yes_or_no", self.code), "stage": self.stage}

    def _next_try(self):
        self.tries += 1
        if self.tries <= 2:
            self.stage = "spell"
            return {"say": say(f"ask_spell_{self.field}", self.code), "stage": "spell"}
        self.stage = "typed"
        return {"say": say(f"ask_type_{self.field}", self.code), "stage": "typed"}

    def typed(self, text, code=None):
        """Typed into the box: kept exactly as typed."""
        self.code = code or self.code
        value = typed_value(text, self.field)
        if value is None:
            if self.field == "email":
                return {"say": say("bad_typed_email", self.code), "stage": "typed"}
            return {"say": say(f"ask_type_{self.field}", self.code), "stage": "typed"}
        self.value = value
        return {"done": value, "how": "typed"}
