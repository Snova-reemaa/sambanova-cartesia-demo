"""Retrieval over the knowledge/ folder.

Deliberately lexical rather than embedding-based. This account has no embedding
model — the seven models are all chat — and a support knowledge base is small,
written in the customer's own vocabulary, and answered by whole sections rather
than sentence fragments. BM25 over headed sections is a better fit than a vector
store here, and it adds no dependency, no index to rebuild and no startup cost.

Swap the folder for real documentation and nothing else changes.
"""

import math
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.getenv("KNOWLEDGE_DIR", os.path.join(HERE, "knowledge"))

TOP_K = 4
MIN_SCORE = 1.0          # worth showing the model alongside a better hit

# A single weak match is not coverage. "Do you price match with competitors"
# scraped 2.25 off the word "price" in the returns section and nothing else,
# which was enough to be told "answer from this" — and it invented a price
# match policy.
#
# But an absolute score cannot be what decides it. "What is your returns
# policy" scored 2.28 and "do you price match with competitors" scored 2.28:
# one is answered in full by returns.md and the other is not written down
# anywhere, and no threshold placed between two identical numbers separates
# them. BM25 cannot tell them apart because IDF works against this corpus —
# "return" appears in 5 of 16 sections, so the word the whole document is
# about is scored as the least informative word in the question, while
# "price" appears once, in passing, and is scored as the most.
#
# What does separate them is where the match lands. Every section here is
# titled with its subject, and "price" is in no title or heading while
# "return" is in several. So a question is treated as covered when it shares
# a word with what a section is *about*, and a body-only match has to clear
# the old bar on its own.
COVERED_SCORE = 2.6

# Multiplies the IDF of each query term found in a section's heading or
# document title. Enough to lift a section about the thing asked over one
# that merely mentions it: "how do I return a product" was answered by
# warranty's "What is covered", on the single word "product", rather than by
# the returns window itself.
TOPIC_BONUS = 1.2

# BM25 constants. k1 damps repeated terms, b weights length normalisation.
K1, B = 1.5, 0.6

STOP = {
    "the", "a", "an", "and", "or", "but", "if", "is", "are", "was", "were", "be",
    "been", "to", "of", "in", "on", "for", "with", "at", "by", "from", "as",
    "it", "its", "this", "that", "these", "those", "i", "you", "we", "they",
    "my", "your", "our", "their", "me", "do", "does", "did", "can", "could",
    "will", "would", "should", "have", "has", "had", "what", "when", "where",
    "how", "why", "who", "there", "here", "about", "get", "got", "please",
    # Part of how a question is phrased rather than what it is about. "How
    # long do emails take to answer" was answered by the section headed "How
    # long a refund takes", which it shares "long" and "take" with and
    # nothing else.
    "long", "take", "much", "many", "need", "want", "know", "tell", "say",
    # What is left of a negated verb once the contraction is stripped:
    # "don't" becomes "don", which is nobody's search term.
    "don", "doesn", "didn", "isn", "aren", "wasn", "weren", "hasn", "haven",
    "hadn", "won", "wouldn", "couldn", "shouldn", "ca", "wo",
}


def words(text):
    # Possessives and contractions are split off first: "what's" is not in
    # STOP, so it used to survive as a term and stem to "what'", a token that
    # matches nothing and dilutes the query it came from.
    cleaned = re.sub(r"'(s|re|ve|ll|d|m|t)\b", "", text.lower())
    return [w for w in re.findall(r"[a-z0-9]+", cleaned)
            if w not in STOP and len(w) > 1]


# "es" is only a plural ending in its own right after a sibilant: boxes,
# watches, dishes. Everywhere else the "e" belongs to the word and only the
# "s" is the plural. Stripping "es" regardless meant "stores" became "stor"
# while "store" stayed "store", so a question about a store did not match a
# section headed "Stores" — and the same for charges/charge,
# headphones/headphone and every other noun ending in a silent e.
SIBILANT = re.compile(r"(s|x|z|ch|sh)es$")


def stem(word):
    """Crude suffix trimming, so "refunds" finds "refund" and "delivered"
    finds "delivery". A real stemmer is not worth a dependency here."""
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if not word.endswith(suffix) or len(word) - len(suffix) < 3:
            continue
        if suffix == "es" and not SIBILANT.search(word):
            continue          # let the "s" rule take it: stores -> store
        base = word[: -len(suffix)]
        return base + "y" if suffix == "ies" else base
    return word


def terms(text):
    return [stem(w) for w in words(text)]


class Section:
    """One '##' section of one document — the unit a support answer comes in."""

    def __init__(self, doc, heading, body):
        self.doc = doc
        self.heading = heading
        self.body = body.strip()
        # The heading is what the customer is most likely echoing back, so it
        # counts three times. The document title counts too: "opening hours"
        # appears only in the title of hours.md, and without it a question
        # using those exact words ranked a delivery section first.
        self.terms = Counter(terms(body) + terms(heading) * 3 + terms(doc) * 2)
        self.length = sum(self.terms.values())
        # What this section is *about*, as opposed to what it mentions.
        self.topic = set(terms(heading)) | set(terms(doc))

    @property
    def text(self):
        return f"{self.heading}\n{self.body}"


def load(folder=None):
    """Read every markdown file into headed sections."""
    folder = folder or FOLDER
    out = []
    if not os.path.isdir(folder):
        return out

    for name in sorted(os.listdir(folder)):
        if not name.endswith((".md", ".markdown", ".txt")):
            continue
        path = os.path.join(folder, name)
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()

        title = name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        heading = title
        buffer = []
        for line in raw.splitlines():
            if line.startswith("## "):
                if buffer:
                    out.append(Section(title, heading, "\n".join(buffer)))
                heading = line[3:].strip()
                buffer = []
            elif line.startswith("# "):
                title = line[2:].strip()
            else:
                buffer.append(line)
        if buffer:
            out.append(Section(title, heading, "\n".join(buffer)))

    return [s for s in out if s.body]


class Knowledge:
    def __init__(self, folder=None):
        self.sections = load(folder)
        self.avg_len = (sum(s.length for s in self.sections) / len(self.sections)
                        if self.sections else 0)
        # How many sections each term appears in, for the IDF weighting.
        self.seen = Counter()
        for section in self.sections:
            self.seen.update(set(section.terms))

    def __len__(self):
        return len(self.sections)

    def idf(self, term):
        n = len(self.sections)
        appears = self.seen.get(term, 0)
        return math.log(1 + (n - appears + 0.5) / (appears + 0.5))

    def score(self, section, query_terms):
        total = 0.0
        for term in query_terms:
            count = section.terms.get(term, 0)
            if not count:
                continue
            norm = 1 - B + B * (section.length / self.avg_len) if self.avg_len else 1
            total += self.idf(term) * (count * (K1 + 1)) / (count + K1 * norm)
        # Weighted by how distinctive the word is, not flat. "How long do
        # emails take to answer" shares "take" with the heading "How long a
        # refund takes", and a flat bonus let that outrank the section headed
        # "Email" — a word in a title still has to be worth something.
        on_topic = set(query_terms) & section.topic
        return total + TOPIC_BONUS * sum(self.idf(t) for t in on_topic)

    def on_topic(self, section, query_terms):
        """Does the question share a word with what this section is about?"""
        return bool(set(query_terms) & section.topic)

    def find(self, question, k=TOP_K):
        """Sections worth showing the model, best first. Empty when the base
        simply does not cover the question — which the caller must be told,
        rather than having an answer invented."""
        query = terms(question)
        if not query or not self.sections:
            return []
        ranked = sorted(
            ((self.score(s, query), s) for s in self.sections),
            key=lambda pair: pair[0],
            reverse=True,
        )
        # Covered if the best section is actually about one of the things
        # asked, or — for a question whose words appear only in bodies — if
        # it clears the old bar on body text alone.
        best_score, best = ranked[0]
        if not self.on_topic(best, query) and best_score < COVERED_SCORE:
            return []
        return [(round(score, 2), s) for score, s in ranked[:k] if score >= MIN_SCORE]


def as_context(hits):
    return "\n\n".join(f"[{s.doc} — {s.heading}]\n{s.body}" for _, s in hits)


if __name__ == "__main__":
    import sys

    kb = Knowledge()
    if len(sys.argv) > 1 and sys.argv[1] != "-v":
        question = " ".join(sys.argv[1:])
        hits = kb.find(question)
        print(f"{question!r} -> {len(hits)} hits")
        for score, section in hits:
            print(f"  {score:5.2f}  {section.doc} — {section.heading}")
            print(f"         {section.body.replace(chr(10), ' ')[:110]}")
        if not hits:
            print("  nothing relevant — the agent should say it does not know")
        raise SystemExit

    # Two failures matter and they pull against each other: a miss makes the
    # agent say it will find out about something written down in front of it,
    # and a false hit lets it answer from a section about something else.
    # (question, the document that answers it, or None if none does)
    CASES = [
        ("How do I return a product?", "returns"),
        ("Can I return something after six weeks?", "returns"),
        ("what is your returns policy", "returns"),
        ("how do i send something back", "returns"),
        ("Can I return opened headphones?", "returns"),
        ("How long does a refund usually take?", "returns"),
        ("I changed my mind, can I get my money back", "returns"),
        ("do I pay for return postage", "returns"),
        ("What are your opening hours?", "hours"),
        ("when are you open", "hours"),
        ("can I phone you on a Sunday", "hours"),
        ("is there a live chat", "hours"),
        ("how long do emails take to answer", "hours"),
        ("Where is my order?", "delivery"),
        ("do you deliver to scotland", "delivery"),
        ("how much is next day delivery", "delivery"),
        ("my order is late, what happens", "delivery"),
        ("do you ship abroad", "delivery"),
        ("my tracking number shows nothing", "delivery"),
        ("is saturday delivery available", "delivery"),
        ("what's the warranty on your monitors", "warranty"),
        ("is water damage covered", "warranty"),
        ("how do I make a warranty claim", "warranty"),
        ("my laptop broke after 18 months", "warranty"),
        ("are batteries covered", "warranty"),
        # Nothing here says whether a delivery address can be changed. The
        # delivery section still comes back, and that is the intended trade
        # rather than a failure: policy_prompt tells the model these sections
        # "may be about something adjacent" and to decline if the specific
        # thing is not stated, and end to end it does. Withholding the
        # section instead would only mean the model saw less.
        ("can I change my delivery address", "delivery"),
        # These share no word with what any section is about, so nothing
        # comes back at all and the agent says it will find out.
        ("Do you price match with competitors", None),
        ("who is your chief executive", None),
        ("do you have a student discount", None),
        ("can I pay in instalments", None),
    ]
    wrong = 0
    for question, want in CASES:
        hits = kb.find(question)
        got = hits[0][1].doc.lower() if hits else None
        ok = (want is None and got is None) or (want and got and want in got)
        wrong += not ok
        top = f"{hits[0][0]:5.2f}" if hits else "  -  "
        print(f"  {'ok  ' if ok else 'FAIL'} {top}  {(got or '(none)')[:22]:24} {question[:44]}")
    print(f"\n  {len(CASES) - wrong}/{len(CASES)} correct")
