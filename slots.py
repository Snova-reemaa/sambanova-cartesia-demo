"""What counts as a valid answer, what the canonical form is, and how to say
it back.

One table per field type, because the three questions are different for each
and answering them in prose inside a prompt gets you an agent that is sure
the caller said something they did not. Validation is code. The model's only
jobs are to phrase the question and to pull the answer out of the sentence;
whether the answer is any good is decided here.

Every check returns the same shape, so the state machine never has to know
which field it is holding:

    Checked(ok, value, say, problem, needs)

    value   the canonical form, which is what gets stored
    say     how to read it back, written for the ear
    problem why not, in words the agent can use
    needs   a specific follow-up when the answer is nearly right —
            "which apartment?" rather than "say that again"
"""

import datetime as dt
import os
import re
import socket
import string

# ---------------------------------------------------------------- result


class Checked:
    __slots__ = ("ok", "value", "say", "problem", "needs")

    def __init__(self, ok, value=None, say=None, problem=None, needs=None):
        self.ok = ok
        self.value = value
        self.say = say
        self.problem = problem
        self.needs = needs

    def __repr__(self):
        return (f"Checked(ok={self.ok}, value={self.value!r}, "
                f"say={self.say!r}, problem={self.problem!r}, "
                f"needs={self.needs!r})")


def good(value, say):
    return Checked(True, value=value, say=say)


def bad(problem, needs=None):
    return Checked(False, problem=problem, needs=needs)


# ------------------------------------------------------------ spelling out

SAY_LETTER = {
    "a": "ay", "b": "bee", "c": "see", "d": "dee", "e": "ee", "f": "eff",
    "g": "gee", "h": "aitch", "i": "eye", "j": "jay", "k": "kay", "l": "ell",
    "m": "em", "n": "en", "o": "oh", "p": "pee", "q": "cue", "r": "are",
    "s": "ess", "t": "tee", "u": "you", "v": "vee", "w": "double you",
    "x": "ex", "y": "why", "z": "zed",
}

SAY_SYMBOL = {".": "dot", "@": "at", "-": "dash", "_": "underscore",
              "+": "plus", "'": "apostrophe"}

# The same, per language. These are the words that matter most when an
# address is read back — a Spanish caller hearing "r dot patel at example
# dot com" is being read English, badly.
SYMBOLS_BY_LANGUAGE = {
    "es": {".": "punto", "@": "arroba", "-": "guion", "_": "guion bajo",
           "+": "más", "'": "apóstrofo"},
    "fr": {".": "point", "@": "arobase", "-": "tiret", "_": "tiret bas",
           "+": "plus", "'": "apostrophe"},
    "de": {".": "Punkt", "@": "at", "-": "Bindestrich", "_": "Unterstrich",
           "+": "plus", "'": "Apostroph"},
    "it": {".": "punto", "@": "chiocciola", "-": "trattino",
           "_": "trattino basso", "+": "più", "'": "apostrofo"},
    "pt": {".": "ponto", "@": "arroba", "-": "hífen", "_": "underline",
           "+": "mais", "'": "apóstrofo"},
    "nl": {".": "punt", "@": "apenstaartje", "-": "streepje",
           "_": "liggend streepje", "+": "plus", "'": "apostrof"},
    "ar": {".": "نقطة", "@": "آت", "-": "شرطة", "_": "شرطة سفلية",
           "+": "زائد", "'": "فاصلة عليا"},
}

# Letter names, where they can be written with confidence. An email is
# always Latin characters whatever the call is in, so the question is only
# what those letters are called.
#
# Italian, Portuguese, Dutch and Arabic are deliberately absent. Arabic
# speakers conventionally use the English names for Latin letters, and for
# the other three I would be guessing — a guessed letter name read out at a
# caller is worse than the English one they will recognise. The symbols are
# still localised for all eight, which is the part that carries most of the
# meaning. A native speaker can fill these in; the gap is here, not hidden.
# Comma separated, not space separated: "uve doble" and "i grec" are two
# words each, and splitting on spaces shifted every letter after w by one —
# Spanish x came out as "doble".
LETTERS_BY_LANGUAGE = {
    "es": "a,be,ce,de,e,efe,ge,hache,i,jota,ka,ele,eme,ene,o,pe,cu,erre,"
          "ese,te,u,uve,uve doble,equis,i griega,zeta",
    "fr": "a,bé,cé,dé,e,effe,gé,ache,i,ji,ka,elle,emme,enne,o,pé,ku,erre,"
          "esse,té,u,vé,double vé,iks,i grec,zède",
    "de": "a,be,ce,de,e,ef,ge,ha,i,jot,ka,el,em,en,o,pe,ku,er,es,te,u,vau,"
          "we,iks,ypsilon,zett",
}
SAY_LETTER_BY_LANGUAGE = {}
for _code, _names in LETTERS_BY_LANGUAGE.items():
    _parts = [n.strip() for n in _names.split(",")]
    assert len(_parts) == 26, f"{_code} has {len(_parts)} letter names, not 26"
    SAY_LETTER_BY_LANGUAGE[_code] = dict(zip(string.ascii_lowercase, _parts))

MONTHS_BY_LANGUAGE = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
           "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"],
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
           "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"],
    "nl": ["januari", "februari", "maart", "april", "mei", "juni", "juli",
           "augustus", "september", "oktober", "november", "december"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
           "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
}

# How a date is said. English puts the day first with an ordinal; most of
# the others say the plain number.
DATE_SHAPE = {
    "en": "the {ordinal} of {month} {year}",
    "es": "el {day} de {month} de {year}",
    "fr": "le {day} {month} {year}",
    "de": "der {day}. {month} {year}",
    "it": "il {day} {month} {year}",
    "pt": "{day} de {month} de {year}",
    "nl": "{day} {month} {year}",
    "ar": "{day} {month} {year}",
}


def spell(text, code="en"):
    """Letter by letter, for reading an address or a spelling back.

    Written the way a person says it rather than as the bare characters,
    because synthesis reads "r.patel" as a word and the caller hears mush.
    In the caller's language: the symbols always, the letter names where
    they are known.
    """
    letters = SAY_LETTER_BY_LANGUAGE.get(code, SAY_LETTER)
    symbols = SYMBOLS_BY_LANGUAGE.get(code, SAY_SYMBOL)
    out = []
    for ch in (text or ""):
        low = ch.lower()
        if low in letters:
            out.append(letters[low])
        elif low in symbols:
            out.append(symbols[low])
        elif low.isdigit():
            out.append(low)
        elif low == " ":
            continue
        else:
            out.append(low)
    return " ".join(out)


def digit_groups(digits, sizes=(3, 3, 4)):
    """Numbers are said in groups, not as one long run."""
    digits = re.sub(r"\D", "", digits or "")
    out, at = [], 0
    for size in sizes:
        if at >= len(digits):
            break
        out.append(digits[at:at + size])
        at += size
    if at < len(digits):
        out.append(digits[at:])
    return " ".join(out)


# ------------------------------------------------------------------- name


def check_name(said):
    """A name is whatever the person says it is.

    No validation beyond "there is something here". Nothing in this file
    knows how a name is spelled, and an agent that corrects one is worse
    than an agent that writes it down wrong — the caller can hear a wrong
    spelling read back and fix it, but cannot argue with a confident
    correction.
    """
    name = " ".join((said or "").split())
    name = re.sub(r"^(my name is|it's|its|i'm|im|this is|call me)\s+", "",
                  name, flags=re.I).strip(" .,")
    if len(name) < 2:
        return bad("I didn't catch a name there.")
    if len(name) > 70:
        return bad("That was longer than a name — could you just give me "
                   "the name on the account?")
    # Title case only where it is plainly all one case; "McDonald" and
    # "van der Berg" are left exactly as said.
    canonical = name.title() if name.islower() or name.isupper() else name
    return good(canonical, canonical)


def spell_back_name(name):
    """For an unusual spelling, offered rather than forced on everyone."""
    parts = (name or "").split()
    return "; ".join(f"{p}, {spell(p)}" for p in parts)


# What a name ordinarily looks like in each language this agent speaks.
# Accented Latin is unremarkable in six of them and Arabic script is
# unremarkable in the seventh; treating either as odd would mean flagging
# most of the names in most of the calls.
ORDINARY_SCRIPT = {
    "ar": re.compile(r"^[\u0600-\u06ff\u0750-\u077f\s'\u0640-]+$"),
}
LATIN_NAME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿŁłŃńŚśŹźŻżČčŠšŽžĞğİıŞşÅåÆæØøÞþÐð\s'-]+$")


def looks_unusual(name, code="en"):
    """Worth offering to spell back? Signals only, never a judgement.

    Two earlier versions got this wrong in instructive ways. One counted
    vowels and decided Smith was unusual. The next treated any character
    outside plain ASCII as unusual, which in an eight-language agent means
    Bergström, Ferreira and every Arabic name in every Arabic call — the
    test for "unusual" cannot be "not English".

    So the question is asked against the language being spoken: a name
    written in the script that call is in is ordinary, whatever its
    accents. What is left is the same for everybody — a mark inside the
    word, a capital in the middle, unusual length, or a script the call is
    not in at all.
    """
    name = (name or "").strip()
    if not name:
        return False
    expected = ORDINARY_SCRIPT.get(code, LATIN_NAME)
    if not expected.match(name):
        # Latin letters in an Arabic call, or Arabic in an English one.
        # Not odd in itself, but worth confirming the spelling of.
        return True
    for part in name.split():
        if len(part) > 11:
            return True
        if any(ch in part for ch in "'-"):
            return True
        # An internal capital: McDonald, O'Neill, DeSouza.
        if part[1:] != part[1:].lower():
            return True
    return False


# ------------------------------------------------------------------ email

EMAIL_SHAPE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}@"
    r"([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}$")

# A true MX lookup needs a DNS library. Without one this falls back to
# "does the domain resolve at all", which catches a mistyped domain and not
# a real domain with no mail. Set EMAIL_CHECK_DNS=0 where egress is blocked
# or the wait is not wanted; syntax alone is still enforced.
DNS_CHECK = (os.getenv("EMAIL_CHECK_DNS", "1").lower()
             not in ("0", "off", "false", "no"))
DNS_SECONDS = float(os.getenv("EMAIL_DNS_SECONDS", "2"))


_dns_complained = set()


def _dns_unavailable(domain, why):
    """Say it once per reason, not once per caller."""
    if why in _dns_complained:
        return
    _dns_complained.add(why)
    print(f"  ! cannot look up mail domains ({why}); checking email syntax "
          f"only from here. Set EMAIL_CHECK_DNS=0 to stop trying.")


def domain_takes_mail(domain):
    """True, False, or None when we could not find out.

    None matters: a DNS failure is not a bad address, and treating it as one
    would turn an egress problem into every caller being told their email is
    wrong.
    """
    try:
        import dns.resolver                                   # noqa: F401
    except ImportError:
        pass
    else:
        try:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = DNS_SECONDS
            resolver.timeout = DNS_SECONDS
            return bool(resolver.resolve(domain, "MX"))
        except Exception as exc:                              # noqa: BLE001
            name = type(exc).__name__
            if name in ("NXDOMAIN", "NoAnswer"):
                return False
            # Timeout, no nameserver, egress blocked. Not the caller's
            # fault and not knowable from here, so syntax is all we have —
            # but the operator should know it is all we have.
            _dns_unavailable(domain, name)
            return None
    # No DNS library: the weaker question, which still catches "gmial.con".
    was = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(DNS_SECONDS)
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror:
        return False
    except Exception as exc:                                  # noqa: BLE001
        _dns_unavailable(domain, type(exc).__name__)
        return None
    finally:
        socket.setdefaulttimeout(was)


def check_email(said, dns=None, code="en"):
    address = re.sub(r"\s+", "", (said or "")).strip(" .,<>").lower()
    if "@" not in address:
        # Said rather than typed: "r dot patel at example dot com". The
        # transcript repair usually gets there first, but an address given
        # mid-sentence reaches here as words and is still an address.
        try:
            import heard
            words = heard.unfuse_words(said or "").split()
            # "my email is ..." — the lead-in belongs to the sentence, not
            # to the address, and heard.py already knows which words those
            # are. Without this the address came out as
            # "myemailiss.bergstrom@example.com".
            while words and words[0].lower().strip(",.") in heard.LEAD_IN:
                words = words[1:]
            spoken = heard.spoken_address(words)
        except Exception:                                     # noqa: BLE001
            spoken = None
        if spoken:
            address = spoken.lower()
    if not address:
        return bad("I didn't catch an email address.")
    if address.count("@") != 1:
        return bad("That didn't sound like a full email address — I need the "
                   "part before the at sign and the part after.")
    if not EMAIL_SHAPE.match(address):
        return bad("That email doesn't look quite right to me.")

    domain = address.split("@", 1)[1]
    if (DNS_CHECK if dns is None else dns):
        answer = domain_takes_mail(domain)
        if answer is False:
            return bad(f"I can't find anything at {spell(domain)} — "
                       "could that be a typo?")
        # None: unreachable. Carry on rather than blame the caller.
    return good(address, spell(address, code))


# ------------------------------------------------------------------ phone

PHONE_REGION = os.getenv("PHONE_REGION", "GB")


def check_phone(said, region=None):
    digits = re.sub(r"[^\d+]", "", (said or ""))
    if len(re.sub(r"\D", "", digits)) < 7:
        return bad("That wasn't enough digits for a phone number.")
    try:
        import phonenumbers
    except ImportError:
        # Degrade to digits rather than refuse. Noted, not hidden.
        plain = re.sub(r"\D", "", digits)
        return good(f"+{plain}" if digits.startswith("+") else plain,
                    digit_groups(plain))
    try:
        parsed = phonenumbers.parse(digits, region or PHONE_REGION)
    except phonenumbers.NumberParseException:
        return bad("I couldn't make a phone number out of that.")
    if not phonenumbers.is_valid_number(parsed):
        return bad("That isn't a working number as far as I can tell.")
    e164 = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164)
    national = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
    return good(e164, digit_groups(re.sub(r"\D", "", national), (5, 6)))


# ------------------------------------------------------- verification code


# A code is read aloud, so it arrives as words as often as digits, and in
# whichever language the call is in. orders.py knows the English ones; a
# code has to work in all eight.
DIGIT_WORDS = {
    "en": "zero one two three four five six seven eight nine",
    "es": "cero uno dos tres cuatro cinco seis siete ocho nueve",
    "fr": "zéro un deux trois quatre cinq six sept huit neuf",
    "de": "null eins zwei drei vier fünf sechs sieben acht neun",
    "it": "zero uno due tre quattro cinque sei sette otto nove",
    "pt": "zero um dois três quatro cinco seis sete oito nove",
    "nl": "nul een twee drie vier vijf zes zeven acht negen",
    "ar": "صفر واحد اثنين ثلاثة أربعة خمسة ستة سبعة ثمانية تسعة",
}
# What the recogniser writes instead, and the forms people actually use.
DIGIT_ALSO = {
    "oh": "0", "o": "0", "nought": "0", "for": "4", "to": "2", "too": "2",
    "ate": "8", "won": "1", "tree": "3", "sex": "6", "niner": "9",
    "une": "1", "deuce": "2",
    "uno": "1", "una": "1", "dos": "2",            # es
    "zwo": "2", "eins": "1",                        # de
    "aan": "1", "nul": "0",                         # nl
    "واحدة": "1", "اثنان": "2", "ثلاث": "3", "أربع": "4", "خمس": "5",
    "ست": "6", "سبع": "7", "ثمان": "8", "تسع": "9",
}

SPOKEN_DIGITS = dict(DIGIT_ALSO)
for _code, _words in DIGIT_WORDS.items():
    for _value, _word in enumerate(_words.split()):
        SPOKEN_DIGITS.setdefault(_word, str(_value))


def digits_from(said):
    """Every digit in what was said, whether written or spoken, any of the
    eight languages."""
    out = []
    for token in re.findall(r"[^\W_]+", (said or "").lower(), re.UNICODE):
        if token.isdigit():
            out.append(token)
        elif token in SPOKEN_DIGITS:
            out.append(SPOKEN_DIGITS[token])
    return "".join(out)


def check_code(said):
    """Exactly six digits. Never read back — saying a one-time code aloud
    to the person who just said it to you teaches them nothing and puts it
    in one more transcript."""
    digits = digits_from(said)
    if not digits:
        return bad("I didn't catch any numbers there.")
    if len(digits) != 6:
        return bad(f"I heard {len(digits)} digits and the code is six.")
    return good(digits, None)


# ----------------------------------------------------------- order number

ORDER_SHAPE = re.compile(r"^\d{4,8}$")


def check_order(said, exists=None):
    """Shape first, then the book — but only for someone already verified.

    `exists` is a callable, or None to skip the lookup. Skipping it is the
    default on purpose: telling an unverified caller whether an order number
    is real is a way to go fishing for real order numbers.
    """
    digits = re.sub(r"\D", "", said or "")
    if not ORDER_SHAPE.match(digits):
        return bad("An order number is four to eight digits — I heard "
                   f"{len(digits) or 'none'}.")
    if exists is not None and not exists(digits):
        return bad("I can't find that order number.")
    return good(digits, digit_groups(digits, (2, 3, 3)))


# ------------------------------------------------------------------ dates

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DATE_PATTERNS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y",
                 "%B %d %Y", "%b %d %Y", "%d %B", "%B %d")


def check_date(said, past_only=False, today=None, code="en"):
    text = " ".join((said or "").replace(",", " ").split())
    if not text:
        return bad("I didn't catch a date.")
    today = today or dt.date.today()
    for pattern in DATE_PATTERNS:
        try:
            parsed = dt.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        if "%Y" not in pattern:
            parsed = parsed.replace(year=today.year)
        if past_only and parsed > today:
            return bad("That date hasn't happened yet.")
        months = MONTHS_BY_LANGUAGE.get(code, MONTHS)
        shape = DATE_SHAPE.get(code, DATE_SHAPE["en"])
        return good(parsed.isoformat(),
                    shape.format(day=parsed.day, ordinal=ordinal(parsed.day),
                                 month=months[parsed.month - 1],
                                 year=parsed.year))
    # Dates that parse in no format are usually impossible rather than
    # unreadable: the thirtieth of February reaches here, not the parser.
    return bad("That isn't a date I recognise — could you give me the day, "
               "the month and the year?")


def ordinal(day):
    if 11 <= day <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


# ------------------------------------------------------- card numbers: no

CARD_LIKE = re.compile(r"(?:\d[ -]?){13,19}")


# What the first digits of a real card look like. Luhn on its own passes
# one random number in ten, which over order numbers and phone numbers is
# a steady trickle of callers being told off for reading out their own
# order number. An issuer prefix and a plausible length narrow it to
# something that is almost certainly a card.
CARD_PREFIXES = (
    ("4", (13, 16, 19)),                       # Visa
    ("34", (15,)), ("37", (15,)),              # American Express
    ("36", (14, 16, 19)), ("38", (14, 16, 19)),  # Diners
    ("6011", (16, 19)), ("65", (16, 19)),      # Discover
    ("35", (16, 19)),                          # JCB
    ("62", (16, 17, 18, 19)),                  # UnionPay
)


def _mastercard(digits):
    head4, head2 = digits[:4], digits[:2]
    if "51" <= head2 <= "55":
        return True
    return head4.isdigit() and "2221" <= head4 <= "2720"


def looks_like_card(said):
    """Caught so it can be refused, never so it can be stored.

    A card number must not pass through the model: it would be in the
    prompt, in the transcript, in the call summary and in whatever logs
    those touch. If a payment is ever needed it is a handoff to something
    built for it, not a slot in a conversation.

    Three things together, because Luhn alone is not evidence: a card
    length, an issuer prefix, and the check digit.
    """
    digits = re.sub(r"\D", "", said or "")
    if not (13 <= len(digits) <= 19):
        return False
    issued = _mastercard(digits) and len(digits) == 16
    if not issued:
        for prefix, lengths in CARD_PREFIXES:
            if digits.startswith(prefix) and len(digits) in lengths:
                issued = True
                break
    return issued and luhn(digits)


def luhn(digits):
    total, double = 0, False
    for ch in reversed(digits):
        value = ord(ch) - 48
        if double:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        double = not double
    return total % 10 == 0


# ---------------------------------------------------------------- address
#
# Three layers, in order of how much they can be trusted.
#
#   1. Shape. Is there a house number, is there a postcode, does the
#      postcode look like a postcode for the country we are in.
#   2. Agreement. Does the postcode belong to the place they named. This
#      catches a transcription error that shape alone cannot: "Manchester,
#      B1 2AA" is two real things that do not go together.
#   3. A real validator, off by default, behind VALIDATE_ADDRESS_WITH.
#      Only this one can tell you the street exists or the house number is
#      on it, and only this one costs money and reaches the internet.
#
# The first two run everywhere and are the reason the third is optional.

UK_POSTCODE = re.compile(
    r"^([A-Z]{1,2})(\d[A-Z\d]?)\s*(\d)([A-Z]{2})$", re.I)
US_ZIP = re.compile(r"^(\d{5})(?:-(\d{4}))?$")

# Postcode area -> the places it covers, for the ones a UK demo will meet.
# Not the whole table: enough to catch a city and a postcode that disagree,
# which is the error this layer exists for.
UK_AREAS = {
    "M": "Manchester", "B": "Birmingham", "L": "Liverpool", "LS": "Leeds",
    "BS": "Bristol", "EH": "Edinburgh", "G": "Glasgow", "CF": "Cardiff",
    "BT": "Belfast", "NE": "Newcastle", "S": "Sheffield", "NG": "Nottingham",
    "LE": "Leicester", "CV": "Coventry", "OX": "Oxford", "CB": "Cambridge",
    "BN": "Brighton", "SO": "Southampton", "PO": "Portsmouth",
    "RG": "Reading", "YO": "York", "HU": "Hull", "DE": "Derby",
    "ST": "Stoke-on-Trent", "PR": "Preston", "LA": "Lancaster",
    "AB": "Aberdeen", "DD": "Dundee", "IM": "Isle of Man",
}
for _london in ("E", "EC", "N", "NW", "SE", "SW", "W", "WC"):
    UK_AREAS[_london] = "London"

# US ZIP prefix ranges -> state. Three digits is enough to disagree with a
# city, which is all this is for.
US_ZIP_RANGES = [
    (("006", "009"), "PR"), (("010", "027"), "MA"), (("028", "029"), "RI"),
    (("030", "038"), "NH"), (("039", "049"), "ME"), (("050", "059"), "VT"),
    (("060", "069"), "CT"), (("070", "089"), "NJ"), (("100", "149"), "NY"),
    (("150", "196"), "PA"), (("197", "199"), "DE"), (("200", "205"), "DC"),
    (("206", "219"), "MD"), (("220", "246"), "VA"), (("247", "268"), "WV"),
    (("270", "289"), "NC"), (("290", "299"), "SC"), (("300", "319"), "GA"),
    (("320", "349"), "FL"), (("350", "369"), "AL"), (("370", "385"), "TN"),
    (("386", "397"), "MS"), (("400", "427"), "KY"), (("430", "459"), "OH"),
    (("460", "479"), "IN"), (("480", "499"), "MI"), (("500", "528"), "IA"),
    (("530", "549"), "WI"), (("550", "567"), "MN"), (("570", "577"), "SD"),
    (("580", "588"), "ND"), (("590", "599"), "MT"), (("600", "629"), "IL"),
    (("630", "658"), "MO"), (("660", "679"), "KS"), (("680", "693"), "NE"),
    (("700", "714"), "LA"), (("716", "729"), "AR"), (("730", "749"), "OK"),
    (("750", "799"), "TX"), (("800", "816"), "CO"), (("820", "831"), "WY"),
    (("832", "838"), "ID"), (("840", "847"), "UT"), (("850", "865"), "AZ"),
    (("870", "884"), "NM"), (("889", "898"), "NV"), (("900", "961"), "CA"),
    (("967", "968"), "HI"), (("970", "979"), "OR"), (("980", "994"), "WA"),
    (("995", "999"), "AK"),
]

US_CITIES = {
    "new york": "NY", "brooklyn": "NY", "buffalo": "NY", "los angeles": "CA",
    "san francisco": "CA", "san diego": "CA", "sacramento": "CA",
    "chicago": "IL", "houston": "TX", "dallas": "TX", "austin": "TX",
    "phoenix": "AZ", "philadelphia": "PA", "pittsburgh": "PA",
    "boston": "MA", "seattle": "WA", "denver": "CO", "miami": "FL",
    "orlando": "FL", "atlanta": "GA", "detroit": "MI", "portland": "OR",
    "las vegas": "NV", "minneapolis": "MN", "nashville": "TN",
}

# Buildings that are nearly always subdivided. Not a rule, a prompt: an
# address in one of these with no unit is probably missing one.
HAS_UNITS = re.compile(
    r"\b(flat|apt|apartment|unit|suite|ste|studio|penthouse|"
    r"house|court|mansions|towers?|heights|lofts?|residences?)\b", re.I)
UNIT_GIVEN = re.compile(
    r"\b(flat|apt|apartment|unit|suite|ste|no|number|#)\s*[-#]?\s*"
    r"([0-9]+[a-z]?|[a-z])\b", re.I)
HOUSE_NUMBER = re.compile(r"(?:^|,|\s)(\d+[a-z]?)(?=\s|,|$)", re.I)

STREET_SUFFIX = {
    "st": "Street", "str": "Street", "rd": "Road", "ave": "Avenue",
    "av": "Avenue", "ln": "Lane", "dr": "Drive", "ct": "Court",
    "cres": "Crescent", "gdns": "Gardens", "pl": "Place", "sq": "Square",
    "ter": "Terrace", "blvd": "Boulevard", "hwy": "Highway", "pkwy": "Parkway",
}


def uk_area(postcode):
    found = UK_POSTCODE.match((postcode or "").strip())
    return found.group(1).upper() if found else None


def us_state_for_zip(zipcode):
    found = US_ZIP.match((zipcode or "").strip())
    if not found:
        return None
    prefix = found.group(1)[:3]
    for (low, high), state in US_ZIP_RANGES:
        if low <= prefix <= high:
            return state
    return None


def tidy_suffix(line):
    """"12 high st" -> "12 High St" with the suffix spelled out."""
    words = []
    for word in line.split():
        plain = word.lower().strip(".,")
        if plain in STREET_SUFFIX:
            words.append(STREET_SUFFIX[plain])
        elif word.isupper() and len(word) > 3:
            words.append(word.title())
        elif word.islower():
            words.append(word.title())
        else:
            words.append(word)
    return " ".join(words)


# Which validator to use, if any. Off by default: none of them is free,
# all of them reach the internet, and a cluster that cannot is a cluster
# where every address would otherwise be refused.
VALIDATE_WITH = (os.getenv("VALIDATE_ADDRESS_WITH") or "").strip().lower()


def check_address(said, country=None, validator=None):
    """Shape, then agreement, then optionally a real validator.

    `validator` is a callable taking the parsed parts and returning either
    a corrected dict or None; it is how the pluggable backends attach, and
    how the tests stand one in. A validator that raises or returns None is
    treated as unreachable, not as a bad address — an outage must not tell
    every caller their address is wrong.
    """
    text = " ".join((said or "").replace("\n", ", ").split())
    if len(text) < 6:
        return bad("I didn't catch an address there.")

    parts = [p.strip(" ,") for p in text.split(",") if p.strip(" ,")]
    line_one = parts[0] if parts else text
    tail = parts[-1] if len(parts) > 1 else ""

    postcode, kind = None, None
    for candidate in reversed(parts):
        last = candidate.split()[-3:] if candidate.split() else []
        for size in (2, 1):
            maybe = " ".join(last[-size:]) if len(last) >= size else ""
            if UK_POSTCODE.match(maybe):
                postcode, kind = maybe.upper(), "uk"
                break
            if US_ZIP.match(maybe):
                postcode, kind = maybe, "us"
                break
        if postcode:
            break

    # The number is not always in the first comma-part: "Flat, 7 Grange
    # Court" has the building number second, and asking for a house number
    # there sends the caller round in a circle when what is missing is the
    # flat. Look across everything before the postcode.
    before_postcode = ", ".join(parts[:-1]) if len(parts) > 1 else text
    if not HOUSE_NUMBER.search(before_postcode):
        return bad("I have the street but not the number — what's the house "
                   "or building number?", needs="house number")
    if not postcode:
        word = "postcode" if (country or "GB").upper() in ("GB", "UK") else "ZIP code"
        return bad(f"I didn't catch the {word}.", needs="postcode")

    # Does the postcode belong to the town they said? Two real things that
    # do not go together is exactly what a transcription error looks like.
    named = " ".join(parts[:-1]).lower() if len(parts) > 1 else text.lower()
    if kind == "uk":
        area = uk_area(postcode)
        town = UK_AREAS.get(area)
        if town and town.lower() not in named and len(parts) > 2:
            return bad(f"That postcode is in {town}, which doesn't match the "
                       "town I heard — could you give me the postcode again?",
                       needs="postcode")
    else:
        state = us_state_for_zip(postcode)
        for city, belongs in US_CITIES.items():
            if city in named and state and belongs != state:
                return bad(f"That ZIP code is in {state} and I heard "
                           f"{city.title()} — could you say the ZIP again?",
                           needs="postcode")

    if HAS_UNITS.search(text) and not UNIT_GIVEN.search(text):
        return bad("That building has flats in it — which one are you?",
                   needs="unit")

    canonical = ", ".join(
        [tidy_suffix(line_one)]
        + [tidy_suffix(p) for p in parts[1:-1]]
        + ([tail.replace(postcode, postcode).strip()] if len(parts) > 1 else []))
    canonical = canonical or tidy_suffix(text)

    if validator is not None:
        try:
            fixed = validator({"text": canonical, "postcode": postcode,
                               "country": kind})
        except Exception:                                     # noqa: BLE001
            fixed = None
        if fixed:
            if fixed.get("needs"):
                return bad(fixed.get("problem") or "I need a bit more of that "
                                                   "address.",
                           needs=fixed["needs"])
            canonical = fixed.get("text", canonical)
            postcode = fixed.get("postcode", postcode)

    # Read back the standardised form, not the transcript, and spell the
    # postcode out — it is the part that is heard wrong.
    spoken = canonical
    if postcode and postcode in canonical:
        spoken = canonical.replace(postcode, spell(postcode))
    return good(canonical, spoken)


# ------------------------------------------------- pluggable address check
#
# One interface, the same shape as the code-delivery backends: a callable
# taking the parsed address and returning a correction, a request for more,
# or None for "could not tell you". None is the important one — an outage
# must never become "your address is wrong".
#
# Off unless VALIDATE_ADDRESS_WITH names one. None of these is written
# against a live account yet; usps, google and smarty are stubs that say so
# rather than pretending, because a backend that silently passes everything
# is worse than no backend.


def _demo_validator(parts):
    """Stands in for a real service, and corrects exactly one thing so the
    read-back-the-standardised-form path is exercised: a street suffix."""
    text = parts["text"]
    fixed = re.sub(r"\bSt\b", "Street", text)
    fixed = re.sub(r"\bRd\b", "Road", fixed)
    return {"text": fixed, "postcode": parts["postcode"]}


def _not_built(name):
    def refuse(parts):
        raise RuntimeError(
            f"the {name} address validator is not implemented — set "
            "VALIDATE_ADDRESS_WITH=demo, or leave it unset for the offline "
            "checks only")
    return refuse


# The order book, the prices and the postcodes in this demo are all UK, so
# the backends offered are the ones that cover the UK. USPS and Smarty are
# United States only and are not listed: naming a validator that cannot
# check a Manchester address is worse than offering none, because somebody
# will switch it on and wonder why everything fails.
VALIDATORS = {
    "demo": _demo_validator,
    "loqate": _not_built("Loqate"),
    "ideal_postcodes": _not_built("Ideal Postcodes (Royal Mail PAF)"),
    "google": _not_built("Google Address Validation"),
}

# Named so the error can say why, if somebody reaches for one anyway.
US_ONLY = {"usps": "USPS", "smarty": "Smarty"}


def address_validator():
    """The configured backend, or None for the offline checks alone."""
    if not VALIDATE_WITH:
        return None
    if VALIDATE_WITH in US_ONLY:
        print(f"  ! {US_ONLY[VALIDATE_WITH]} only validates United States "
              "addresses and this demo's data is UK. Use loqate or "
              "ideal_postcodes for the UK, or google for both.")
        return None
    found = VALIDATORS.get(VALIDATE_WITH)
    if found is None:
        print(f"  ! VALIDATE_ADDRESS_WITH={VALIDATE_WITH!r} is not one of "
              f"{sorted(VALIDATORS)}; using the offline checks only")
        return None
    return found


# ------------------------------------------------------------- the table

CHECKS = {
    "name": check_name,
    "email": check_email,
    "phone": check_phone,
    "code": check_code,
    "order": check_order,
    "date": check_date,
    "address": check_address,
}

# Whether a value may be read back to the caller at all. A one-time code is
# the only thing here that must not be: saying it to the person who just
# said it teaches them nothing and puts it in another transcript.
READ_BACK = {name: name != "code" for name in CHECKS}


def check(field, said, **kw):
    """Validate one slot. Unknown fields are a programming error, loudly."""
    if field not in CHECKS:
        raise KeyError(f"no validator for {field!r}; have {sorted(CHECKS)}")
    if field != "code" and looks_like_card(said):
        # Never stored, never echoed, never sent to a model.
        return bad("I can't take card numbers, and you shouldn't read one "
                   "out to me — if this needs a payment I'll put you "
                   "through to someone who can take it safely.")
    return CHECKS[field](said, **kw)


if __name__ == "__main__":
    wrong = 0

    def want(label, got, expected):
        global wrong
        ok = got == expected
        wrong += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}"
              + ("" if ok else f"  — got {got!r}, wanted {expected!r}"))

    print("names — taken as given, never corrected")
    want("a plain name", check_name("my name is riya patel").value, "Riya Patel")
    want("a name with a particle", check_name("van der Berg").value, "van der Berg")
    want("nothing is not a name", check_name("a").ok, False)
    want("Smith is not unusual", looks_unusual("Smith", "en"), False)
    want("O'Neill is", looks_unusual("O'Neill", "en"), True)
    want("McDonald is", looks_unusual("McDonald", "en"), True)
    # Judged against the language being spoken, not against English.
    want("Bergström is ordinary in German",
         looks_unusual("Bergström", "de"), False)
    want("Gonçalves is ordinary in Portuguese",
         looks_unusual("Gonçalves", "pt"), False)
    want("Müller is ordinary in German", looks_unusual("Müller", "de"), False)
    want("an Arabic name is ordinary in an Arabic call",
         looks_unusual("ريا باتيل", "ar"), False)
    want("a Latin name in an Arabic call is worth confirming",
         looks_unusual("Riya Patel", "ar"), True)
    want("spelled back", spell_back_name("Patel"), "Patel, pee ay tee ee ell")

    print("\nemail — syntax, then the domain, read back letter by letter")
    want("a typed address", check_email("r.patel@example.com", dns=False).value,
         "r.patel@example.com")
    want("said out loud",
         check_email("r dot patel at example dot com", dns=False).value,
         "r.patel@example.com")
    want("with a lead-in",
         check_email("my email is s dot bergstrom at example dot com",
                     dns=False).value, "s.bergstrom@example.com")
    want("not an address", check_email("notanemail", dns=False).ok, False)
    want("read back for the ear",
         check_email("a@b.com", dns=False).say, "ay at bee dot see oh em")
    # Read back in the caller's language, not in English with their accent.
    want("read back in Spanish",
         check_email("a@b.com", dns=False, code="es").say,
         "a arroba be punto ce o eme")
    want("read back in French",
         check_email("a@b.com", dns=False, code="fr").say,
         "a arobase bé point cé o emme")
    want("the Spanish alphabet is not shifted by uve doble",
         SAY_LETTER_BY_LANGUAGE["es"]["x"], "equis")
    want("nor the French by double vé",
         SAY_LETTER_BY_LANGUAGE["fr"]["z"], "zède")

    print("\nphone — E.164, read back in groups")
    want("a UK mobile", check_phone("07911 123456", region="GB").value,
         "+447911123456")
    want("too few digits", check_phone("12345").ok, False)
    want("a made-up number", check_phone("07700 900123", region="GB").ok, False)

    print("\ncode — six digits, in any of the eight, never read back")
    for code, said in (("en", "four two seven nine one oh"),
                       ("es", "cuatro dos siete nueve uno cero"),
                       ("fr", "quatre deux sept neuf un zéro"),
                       ("de", "vier zwei sieben neun eins null"),
                       ("it", "quattro due sette nove uno zero"),
                       ("pt", "quatro dois sete nove um zero"),
                       ("nl", "vier twee zeven negen een nul"),
                       ("ar", "أربعة اثنين سبعة تسعة واحد صفر")):
        want(f"spoken in {code}", check_code(said).value, "427910")
    want("a mishearing of four", check_code("for two seven nine one oh").value,
         "427910")
    want("five digits is not six", check_code("42791").ok, False)
    want("never read back", check_code("427910").say, None)

    print("\norder — shape, and the book only once they are verified")
    want("a real shape", check_order("44812").value, "44812")
    want("said with spaces", check_order("4 4 8 1 2").value, "44812")
    want("too short", check_order("441").ok, False)
    want("not looked up by default", check_order("99999").ok, True)
    want("looked up when asked",
         check_order("99999", exists=lambda n: n == "44812").ok, False)

    print("\ndates — real ones only, month said as a word")
    want("a written date", check_date("2026-03-14").value, "2026-03-14")
    want("read back", check_date("2026-03-14").say, "the 14th of March 2026")
    want("read back in German",
         check_date("2026-03-14", code="de").say, "der 14. März 2026")
    want("read back in Arabic",
         check_date("2026-03-14", code="ar").say, "14 مارس 2026")
    want("the thirtieth of February", check_date("30/02/2026").ok, False)
    want("a birthday in the future",
         check_date("01/01/2099", past_only=True).ok, False)
    want("not a date at all", check_date("sometime soon").ok, False)

    print("\naddress — number, postcode, agreement, units")
    want("a good one", check_address("12 High Street, Manchester, M1 4BT").ok, True)
    want("no house number",
         check_address("High Street, Manchester, M1 4BT").needs, "house number")
    want("no postcode", check_address("12 high st, manchester").needs, "postcode")
    want("postcode disagrees with the town",
         check_address("12 High Street, Manchester, B1 2AA").needs, "postcode")
    want("a ZIP in the wrong state",
         check_address("500 Market Street, San Francisco, 10001").needs,
         "postcode")
    want("a building with flats and no flat",
         check_address("Flat, 7 Grange Court, Leeds, LS1 4AB").needs, "unit")
    want("with the flat given",
         check_address("Flat 3, 7 Grange Court, Leeds, LS1 4AB").ok, True)
    want("the suffix is spelled out",
         check_address("12 High St, Manchester, M1 4BT").value,
         "12 High Street, Manchester, M1 4BT")
    want("a validator that cannot be reached is not a rejection",
         check_address("12 High St, Manchester, M1 4BT",
                       validator=_not_built("USPS")).ok, True)
    # Letters are spelled, digits are left as digits — synthesis says "1"
    # as "one" already, and spelling it adds nothing but length.
    want("the postcode is spelled in the read-back",
         check_address("12 High Street, Manchester, M1 4BT").say,
         "12 High Street, Manchester, em 1 4 bee tee")

    print("\ncard numbers — refused everywhere, never stored")
    for field in ("name", "email", "address", "order"):
        want(f"refused as a {field}", check(field, "4539 1488 0343 6467").ok, False)
    for label, number in (("Visa", "4539 1488 0343 6467"),
                          ("Mastercard", "5500 0000 0000 0004"),
                          ("Amex", "3782 822463 10005"),
                          ("Discover", "6011 0009 9013 9424")):
        want(f"a real {label} is caught", looks_like_card(number), True)
    # Luhn alone passes one random number in ten. These all pass it and
    # none of them is a card, which is the whole reason the issuer prefix
    # and the length are checked too.
    want("a long order reference that passes Luhn is not a card",
         looks_like_card("7305120563826"), False)
    # It is still too long to be an order number, which is a different
    # objection — the point is that it is not turned away as a card.
    want("and is not turned away as a card",
         "card" in (check("order", "7305120563826").problem or ""), False)
    want("a Japanese number that passes Luhn is not a card",
         looks_like_card("813570112306"), False)
    want("a German number starting 49 that passes Luhn is not a card",
         looks_like_card("49151123456786"), False)
    want("those really do pass Luhn",
         all(luhn(n) for n in ("7305120563826", "813570112306",
                               "49151123456786")), True)
    want("an order number is not a card", check("order", "44812").ok, True)

    print("\naddress backends — the ones that cover this market")
    want("US-only ones are not offered",
         "usps" not in VALIDATORS and "smarty" not in VALIDATORS, True)
    want("UK ones are", {"loqate", "ideal_postcodes"} <= set(VALIDATORS), True)

    total = 68
    print(f"\n  {total - wrong}/{total} correct")
    raise SystemExit(1 if wrong else 0)
