"""Repair a speech transcript before anything else reads it.

Browser speech recognition is tuned for prose. Anything said as characters —
an order number, an email address — comes back as the commonest English words
that sound like it, because those are what a language model expects next:

    "four four eight one two"          -> "for for eight one to"
    "r dot patel at example dot com"   -> "ordot Patel at Gmail.com"

The second one is the instructive failure. "our" and "dot" were run together
into one word that is not a word, and a domain nobody said was substituted for
one that was, because gmail.com is vastly commoner in text than example.com.
No amount of care further down can recover from that: by the time the address
reaches the lookup it is already a different address.

So the repair happens once, here, at the boundary where the transcript
arrives, in the way a dictation system does it — by knowing what the caller
is likely to be saying. The order numbers and the customer addresses are
known, and a reading that lands on a real one beats a reading that does not.
Nothing is changed unless it lands on something real, so ordinary speech
passes through untouched.
"""

import difflib
import re

# A letter said on its own, and what gets written instead.
LETTERS = {
    "a": "a", "ay": "a", "eh": "a",
    "b": "b", "bee": "b", "be": "b",
    "c": "c", "see": "c", "sea": "c", "cee": "c",
    "d": "d", "dee": "d",
    "e": "e", "ee": "e",
    "f": "f", "ef": "f", "eff": "f",
    "g": "g", "gee": "g", "jee": "g",
    "h": "h", "aitch": "h", "haitch": "h",
    "i": "i", "eye": "i", "aye": "i",
    "j": "j", "jay": "j",
    "k": "k", "kay": "k", "cay": "k",
    "l": "l", "el": "l", "ell": "l",
    "m": "m", "em": "m", "emm": "m",
    "n": "n", "en": "n", "enn": "n",
    "o": "o", "oh": "o", "owe": "o",
    "p": "p", "pee": "p", "pea": "p",
    "q": "q", "cue": "q", "queue": "q",
    "r": "r", "are": "r", "our": "r", "ar": "r", "arr": "r", "or": "r",
    "ore": "r", "oar": "r", "err": "r",
    "s": "s", "ess": "s", "es": "s",
    "t": "t", "tee": "t", "tea": "t",
    "u": "u", "you": "u", "yoo": "u", "ewe": "u",
    "v": "v", "vee": "v",
    "w": "w", "dubya": "w",
    "x": "x", "ex": "x", "eks": "x",
    "y": "y", "why": "y", "wye": "y",
    "z": "z", "zed": "z", "zee": "z",
}

SEPARATORS = ("dot", "point", "at", "underscore", "dash", "hyphen")

# "essdot", "jaydot": the letter and the separator spoken without a gap
# between them and written as one token. Longest letter words first, so "ess"
# is tried before "es".
#
# Only "dot" and "point" split. Allowing "at" too meant every ordinary word
# that happens to end in it came apart — "cat" is "c" plus "at", and so are
# sat, mat, bat, hat, rat, eat and beat. The caller said "I sat on the mat"
# and the agent was handed "I s at on the m at".
_LETTER_ALT = "|".join(sorted(LETTERS, key=len, reverse=True))
_SEP_ALT = "|".join(SEPARATORS)
FUSED = re.compile(rf"\b({_LETTER_ALT})(dot|point)\b", re.I)

ADDRESS_WORDS = re.compile(
    rf"\b(?:{_SEP_ALT})\b|@|\b[\w.+-]+@[\w.-]+\b", re.I)

WRITTEN = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)

# What people say in front of an address. Swept into the span otherwise, so
# "my email is ordot Patel at Gmail.com" replaced the sentence rather than
# the address in it.
LEAD_IN = {"my", "me", "mine", "email", "e-mail", "mail", "address", "is",
           "its", "it's", "the", "a", "i", "im", "i'm", "so", "and", "um",
           "uh", "er", "yes", "yeah", "sure", "ok", "okay", "thanks",
           "please", "thats", "that's", "sorry", "well", "right", "you",
           "can", "have", "use", "try", "here", "there"}


def unfuse_words(text):
    """"ordot patel" -> "our dot patel". Only splits where the first half is
    the name of a letter and the second is a separator, which no English word
    is."""
    return FUSED.sub(lambda m: f"{m.group(1)} {m.group(2)}", text or "")


def _spell(segment):
    """A whole segment that is the name of a letter becomes that letter."""
    return LETTERS.get(segment.lower(), segment)


def spoken_address(words):
    """Build an address out of a run of spoken words, or None."""
    out, saw_at = [], False
    for word in words:
        low = word.lower()
        if low in ("at", "@"):
            if saw_at:
                return None
            saw_at = True
            out.append("@")
        elif low in ("dot", "point"):
            out.append(".")
        elif low == "underscore":
            out.append("_")
        elif low in ("dash", "hyphen"):
            out.append("-")
        else:
            out.append(_spell(low))
    address = "".join(out)
    return address if saw_at and "." in address.split("@")[-1] else None


def _surname(address):
    """The longest alphabetic run in the local part — the bit a recogniser
    gets right, because it is a name rather than a letter."""
    local = address.split("@")[0]
    runs = re.findall(r"[a-z]{3,}", local.lower())
    return max(runs, key=len) if runs else ""


def best_email(candidate, known):
    """The real address this is most likely to be, or None.

    A surname is the part speech recognition gets right; the initial and the
    domain are the parts it invents. So an exact surname match outranks
    overall similarity — "ordot Patel at Gmail.com" is Riya Patel however
    little of the rest survived.
    """
    if not candidate or not known:
        return None
    low = candidate.lower()
    if low in known:
        return low
    name = _surname(low)
    if name:
        hits = [e for e in known if _surname(e) == name]
        if len(hits) == 1:
            return hits[0]
    # The surname can arrive welded to whatever came before it: "our dot"
    # was written "ordot", so the local part is "ordotpatel". Look for a
    # known surname inside it rather than expecting it to stand alone.
    local = low.split("@")[0]
    inside = [e for e in known if _surname(e) and _surname(e) in local]
    if len(inside) == 1:
        return inside[0]
    close = difflib.get_close_matches(low, sorted(known), n=1, cutoff=0.72)
    return close[0] if close else None


def _address_starts(span, email):
    """Index of the first token in `span` that is part of the address."""
    name = _surname(email)
    for i, token in enumerate(span):
        low = token.lower().strip(",.?!'\"")
        if (low in LETTERS or low in SEPARATORS or "@" in low
                or low.endswith(("dot", "point"))
                or (name and name in low)):
            return i
    return 0


def repair(text, emails=(), orders=()):
    """Return (repaired text, list of (before, after) for anything changed)."""
    if not text:
        return text, []
    known_emails = {e.lower() for e in emails if e}
    changes = []

    out = unfuse_words(text)
    if out != text:
        changes.append((text, out))

    # Addresses, written and spoken. A span is only rewritten when it lands
    # on a real customer, so "my brother's at home" is never touched.
    if known_emails:
        for written in set(WRITTEN.findall(out)):
            match = best_email(written, known_emails)
            if match and match != written.lower():
                out = out.replace(written, match)
                changes.append((written, match))

        tokens = out.split()
        for start in range(len(tokens)):
            for end in range(min(start + 12, len(tokens)), start + 2, -1):
                span = tokens[start:end]
                while span and span[0].lower().strip(",.") in LEAD_IN:
                    span = span[1:]
                if len(span) < 2 or not any(ADDRESS_WORDS.search(t) for t in span):
                    continue
                built = spoken_address(span)
                if not built:
                    continue
                match = best_email(built, known_emails)
                if not match:
                    continue
                # Keep only the part that is actually the address. A list of
                # lead-in words cannot cover everything somebody might say
                # first — "hi it's all that Patel at example.com" replaced
                # the whole greeting — so the span starts at the first token
                # that belongs to an address: a letter, a separator, a letter
                # welded to one, or the surname itself.
                span = span[_address_starts(span, match):]
                phrase = " ".join(span)
                if match != phrase:
                    out = out.replace(phrase, match, 1)
                    changes.append((phrase, match))
                tokens = out.split()
                break

    # Order numbers: orders.find_number already prefers a candidate that
    # matches a real order, so this only rewrites the transcript, so that the
    # model reads "44812" rather than "for for eight one to" and says it back
    # correctly. Only the run of number words is replaced — an earlier version took
    # the whole sentence with it and "where is order for for eight one to"
    # became "44812".
    if orders:
        import orders as order_book          # local, so this module stands alone
        words = out.split()
        numberish = [
            bool(re.fullmatch(r"\d+", w.strip(",.?!")))
            or w.lower().strip(",.?!") in order_book.SPOKEN
            or w.lower().strip(",.?!") in order_book.HOMOPHONES
            or w.lower().strip(",.?!") in order_book.TENS
            or w.lower().strip(",.?!") in order_book.TEENS
            or w.lower().strip(",.?!") in order_book.REPEATS
            for w in words
        ]
        start = None
        for i in range(len(words) + 1):
            if i < len(words) and numberish[i]:
                start = i if start is None else start
                continue
            if start is not None and i - start >= 2:
                phrase = " ".join(words[start:i])
                number = order_book.find_number(phrase)
                if number and number in orders and number != phrase:
                    out = out.replace(phrase, number, 1)
                    changes.append((phrase, number))
                    words = out.split()
                    numberish = numberish[:start] + [True] + numberish[i:]
            start = None

    return out, changes


if __name__ == "__main__":
    EMAILS = ["r.patel@example.com", "s.bergstrom@example.com",
              "j.okafor@example.com", "k.yamamoto@example.com",
              "a.novak@example.com", "m.lindqvist@example.com",
              "l.haddad@example.com", "d.ferreira@example.com"]

    # Left column is what the browser actually wrote down.
    CASES = [
        ("my email is ordot Patel at Gmail.com", "r.patel@example.com"),
        ("my email is our dot patel at example dot com", "r.patel@example.com"),
        ("r dot patel at example dot com", "r.patel@example.com"),
        ("are dot patel at example dot com", "r.patel@example.com"),
        ("it's essdot bergstrom at gmail.com", "s.bergstrom@example.com"),
        ("jay dot okafor at example dot com", "j.okafor@example.com"),
        ("eh dot novak at example dot com", "a.novak@example.com"),
        ("dee dot ferreira at hotmail dot com", "d.ferreira@example.com"),
        ("r.patel@example.com", "r.patel@example.com"),
        ("k dot yamamoto at example dot com please", "k.yamamoto@example.com"),
        # The greeting is not part of the address. This replaced the whole
        # line, so the screen showed only an email where a sentence had been.
        ("hi it's all that Patel at example.com", "r.patel@example.com"),
        ("hello there it is j dot okafor at example dot com", "j.okafor@example.com"),
        # Nothing to do with an address. None of this may be touched.
        ("thanks for your help", None),
        ("can you look at the order", None),
        ("I want to return it", None),
        ("my brother is at home", None),
        ("where is order 44812", None),
        ("he lives at number four", None),
        # Every one of these came apart on the "at": c-at, s-at, m-at, e-at,
        # be-at. The caller was handed back their own words in pieces.
        ("the cat sat on the mat", None),
        ("I want to eat something first", None),
        ("just beat it", None),
        ("can you look at the order", None),
        ("that is great", None),
        ("is it a flat rate", None),
    ]

    ORDER_CASES = [
        ("where is order for for eight one to", "where is order 44812"),
        ("for for eight one to", "44812"),
        ("my order is double four six double eight", "my order is 44688"),
        ("order 44 812", "order 44812"),
        ("hi its order number four four nine zero one has it shipped",
         "hi its order number 44901 has it shipped"),
        ("forty five one oh two", "45102"),
        # No order in any of these, so the words stay words.
        ("thanks for your help", "thanks for your help"),
        ("is it four or five days", "is it four or five days"),
        ("I want to return it", "I want to return it"),
        ("he lives at number four", "he lives at number four"),
    ]
    wrong = 0
    for said, want in CASES:
        out, changes = repair(said, emails=EMAILS)
        got = want if want and want in out else (None if not want else out)
        ok = (want in out) if want else (out == said)
        wrong += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {out[:62]:64} {said[:40]}")
    import orders as order_book
    for said, want in ORDER_CASES:
        out, _ = repair(said, orders=order_book.ORDERS)
        ok = out == want
        wrong += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {out[:62]:64} {said[:40]}")
    total = len(CASES) + len(ORDER_CASES)
    print(f"\n  {total - wrong}/{total} transcripts repaired correctly")
