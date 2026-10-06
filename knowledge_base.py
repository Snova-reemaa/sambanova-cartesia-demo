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
# match policy. Questions the documents genuinely answer score far higher:
# refunds 8.06, late orders 7.56, warranty 5.56, shipping abroad 2.78.
COVERED_SCORE = 2.6

# BM25 constants. k1 damps repeated terms, b weights length normalisation.
K1, B = 1.5, 0.6

STOP = {
    "the", "a", "an", "and", "or", "but", "if", "is", "are", "was", "were", "be",
    "been", "to", "of", "in", "on", "for", "with", "at", "by", "from", "as",
    "it", "its", "this", "that", "these", "those", "i", "you", "we", "they",
    "my", "your", "our", "their", "me", "do", "does", "did", "can", "could",
    "will", "would", "should", "have", "has", "had", "what", "when", "where",
    "how", "why", "who", "there", "here", "about", "get", "got", "please",
}


def words(text):
    return [w for w in re.findall(r"[a-z0-9']+", text.lower())
            if w not in STOP and len(w) > 1]


def stem(word):
    """Crude suffix trimming, so "refunds" finds "refund" and "delivered"
    finds "delivery". A real stemmer is not worth a dependency here."""
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
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
        return total

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
        if not ranked or ranked[0][0] < COVERED_SCORE:
            return []
        return [(round(score, 2), s) for score, s in ranked[:k] if score >= MIN_SCORE]


def as_context(hits):
    return "\n\n".join(f"[{s.doc} — {s.heading}]\n{s.body}" for _, s in hits)


if __name__ == "__main__":
    import sys

    kb = Knowledge()
    print(f"{len(kb)} sections from {FOLDER}\n")
    question = " ".join(sys.argv[1:]) or "how long do refunds take"
    hits = kb.find(question)
    print(f"{question!r} -> {len(hits)} hits")
    for score, section in hits:
        print(f"  {score:5.2f}  {section.doc} — {section.heading}")
        print(f"         {section.body.replace(chr(10), ' ')[:110]}")
    if not hits:
        print("  nothing relevant — the agent should say it does not know")
