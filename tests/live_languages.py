"""A reply stays in the language it was asked in, every word of it.

Each word is spoken aloud, so a stray English noun in an Arabic sentence is
not a cosmetic blemish: Cartesia is given one language per generation and
reads it with the wrong phonology.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.live import Caller, run                             # noqa: E402

ASKS = {
    "ar": ["متى يوصل طلبي؟", "كيف أرجع المنتج؟", "هل تشحنون إلى السعودية؟",
           "طلبي تأخر كثير، وش أسوي؟"],
    "es": ["¿Cuándo llega mi pedido?", "¿Cómo devuelvo un producto?",
           "Mi pedido llegó roto, ¿qué hago?", "¿Cuál es vuestro horario de atención?"],
    "fr": ["Quand est-ce que ma commande arrive ?", "Comment puis-je retourner un article ?",
           "Ma commande est en retard, que faire ?", "Quels sont vos horaires d'ouverture ?"],
    "de": ["Wann kommt meine Bestellung an?", "Wie kann ich einen Artikel zurückgeben?",
           "Meine Bestellung ist beschädigt angekommen, was nun?", "Wie sind Ihre Öffnungszeiten?"],
    "it": ["Quando arriva il mio ordine?", "Come posso restituire un prodotto?",
           "Il mio ordine è in ritardo, cosa faccio?", "Quali sono i vostri orari?"],
    "pt": ["Quando chega a minha encomenda?", "Como devolvo um produto?",
           "A minha encomenda chegou danificada, o que faço?", "Qual é o vosso horário?"],
    "nl": ["Wanneer komt mijn bestelling aan?", "Hoe kan ik een product retourneren?",
           "Mijn bestelling is beschadigd aangekomen, wat nu?", "Wat zijn jullie openingstijden?"],
}

CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")
LATIN_RUN = re.compile(r"[A-Za-z]{3,}")
WORD = re.compile(r"[A-Za-zÀ-ÿ']+")

# The agent's own name, a colleague's, the company's.
NAMES = {"omar", "maya", "sam", "nadia", "sambanova", "cartesia", "uk", "voxnova"}

ENGLISH = {
    "shipment", "shipments", "shipping", "tracking", "delivery", "delivered",
    "refund", "refunded", "order", "orders", "return", "returns", "returned",
    "warranty", "checkout", "billing", "support", "customer", "invoice",
    "payment", "account", "update", "updated", "sorry", "thanks", "thank",
    "please", "hello", "sure", "okay", "check", "checking", "team", "number",
    "the", "and", "with", "for", "you", "your", "our", "will", "can", "let",
    "about", "something", "right", "away", "issue", "help", "days", "working",
    "business", "email", "address", "item", "items", "package", "parcel",
    "replacement", "exchange", "hours", "open",
}
# Words above that are ordinary vocabulary in a given language, where
# flagging them would be a false positive. German really does say Support.
NATIVE = {
    "de": {"team", "update", "updated", "account", "check", "checking", "sorry",
           "support", "item", "items", "business", "open", "will", "for",
           "hours", "address"},
    "nl": {"team", "update", "updated", "account", "check", "checking", "sorry",
           "support", "order", "orders", "item", "items", "business", "open",
           "the", "our", "hours", "address", "email"},
    "it": {"team", "update", "updated", "account", "check", "checking", "sorry",
           "business", "open", "email"},
    "fr": {"support", "check", "sorry", "business", "item", "address", "note",
           "our", "for"},
    "es": {"check", "sorry", "no", "order"},
    "pt": {"check", "sorry", "no", "item", "items"},
    "ar": set(),
}


def leaks(code, reply):
    out = [f"CJK:{c}" for c in CJK.findall(reply)[:3]]
    if code == "ar":
        out += [w for w in LATIN_RUN.findall(reply) if w.lower() not in NAMES]
    else:
        if ARABIC.search(reply):
            out.append("arabic-script")
        allow = NAMES | NATIVE.get(code, set())
        out += [w for w in WORD.findall(reply)
                if w.lower() not in allow and w.lower() in ENGLISH]
    return out


async def cases(http, server):
    results = []
    for code, asks in ASKS.items():
        async with Caller(http, server) as caller:
            for ask in asks:
                turn = await caller.say(ask)
                reply = turn["reply"] or ""
                bleed = leaks(code, reply)
                results.append((not bleed and bool(reply),
                                f"{code}  {','.join(bleed)[:28]:28} {reply[:60]}"))
    return results


if __name__ == "__main__":
    raise SystemExit(run(cases))
