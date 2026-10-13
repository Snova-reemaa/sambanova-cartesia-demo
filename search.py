"""Web search for the voice demo.

Pluggable on purpose. Set SEARCH_PROVIDER, or just supply one of the API keys
below and the right provider is chosen for you:

    tavily   TAVILY_API_KEY    answer-shaped results, best fit for a voice reply
    brave    BRAVE_API_KEY     general web index
    serper   SERPER_API_KEY    Google results via serper.dev
    exa      EXA_API_KEY       neural/semantic search, good at "find one like this"
    ddg      (no key)          scrapes DuckDuckGo's lite endpoint

The keyless DuckDuckGo path exists so the demo runs out of the box. It parses
HTML that DuckDuckGo can change without warning, and heavy use will get you rate
limited, so put a real key in for anything you intend to show people.
"""

import asyncio
import html
import os
import re
import urllib.parse

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=8)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) VoiceLoopDemo/1.0"
MAX_RESULTS = 4


def provider():
    explicit = os.getenv("SEARCH_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if os.getenv("TAVILY_API_KEY"):
        return "tavily"
    if os.getenv("BRAVE_API_KEY"):
        return "brave"
    if os.getenv("SERPER_API_KEY"):
        return "serper"
    if os.getenv("EXA_API_KEY"):
        return "exa"
    return "ddg"


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


async def _tavily(session, query):
    # include_answer gives back one clean sentence built from the sources.
    # Tavily's per-result `content` is a raw page extract that often carries
    # navigation furniture, which reads badly when spoken.
    async with session.post(
        "https://api.tavily.com/search",
        json={
            "api_key": os.environ["TAVILY_API_KEY"],
            "query": query,
            "max_results": MAX_RESULTS,
            "search_depth": "basic",
            "include_answer": True,
        },
    ) as r:
        data = await r.json()
    results = [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": _clean(x.get("content"))}
        for x in data.get("results", [])[:MAX_RESULTS]
    ]
    return {"answer": (data.get("answer") or "").strip() or None, "results": results}


async def _brave(session, query):
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": MAX_RESULTS}
    )
    headers = {"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"}
    async with session.get(url, headers=headers) as r:
        data = await r.json()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": _clean(x.get("description"))}
        for x in (data.get("web", {}).get("results") or [])[:MAX_RESULTS]
    ]


async def _serper(session, query):
    async with session.post(
        "https://google.serper.dev/search",
        json={"q": query, "num": MAX_RESULTS},
        headers={"X-API-KEY": os.environ["SERPER_API_KEY"]},
    ) as r:
        data = await r.json()
    return [
        {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
        for x in (data.get("organic") or [])[:MAX_RESULTS]
    ]


async def _exa(session, query):
    """Exa — semantic rather than keyword.

    Untested against this demo: it needs a key nobody has set yet. Worth trying
    for "find me one like this", which is what neural search is actually for and
    where the keyword providers struggle with a description rather than a name.
    Likely worse for the other two things this demo searches — current news and
    a specific product by name — which are freshness and keyword problems.

    Costs more than the alternatives: roughly $7 per 1,000 searches against
    Serper's $1, and the contents and answer endpoints bill separately. Check
    exa.ai/pricing before relying on any of that.
    """
    headers = {"x-api-key": os.environ["EXA_API_KEY"], "Content-Type": "application/json"}
    body = {
        "query": query,
        "numResults": MAX_RESULTS,
        "type": "auto",          # let Exa choose neural or keyword per query
        # Page text, so the model has something to answer from. Without this
        # Exa returns links alone, which is useless for a spoken reply.
        "contents": {"text": {"maxCharacters": 800}},
    }
    async with session.post("https://api.exa.ai/search", json=body, headers=headers) as r:
        data = await r.json()

    results = [
        {
            "title": x.get("title") or x.get("url", ""),
            "url": x.get("url", ""),
            "snippet": _clean(x.get("text") or x.get("summary") or ""),
        }
        for x in (data.get("results") or [])[:MAX_RESULTS]
    ]
    # Exa has a separate /answer endpoint that is billed on its own, so there
    # is no synthesised sentence here the way Tavily gives one. The model gets
    # the extracts instead, which is a step down for a voice reply.
    return {"answer": None, "results": results}


# DuckDuckGo's lite page wraps every result URL in a redirect; the real one is
# the uddg query parameter. Attribute order and quoting here are not stable.
_LINK = re.compile(r"<a[^>]*?href=\"(?P<href>[^\"]+)\"[^>]*?class='result-link'[^>]*>(?P<title>.*?)</a>", re.S)
_SNIP = re.compile(r"<td[^>]*?class='result-snippet'[^>]*>(?P<snip>.*?)</td>", re.S)


def _unwrap(href):
    if "uddg=" in href:
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if parsed.get("uddg"):
            return parsed["uddg"][0]
    return href if href.startswith("http") else "https:" + href


async def _ddg(session, query):
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    async with session.get(url, headers={"User-Agent": UA}) as r:
        body = await r.text()

    links = [(m.group("href"), _clean(m.group("title"))) for m in _LINK.finditer(body)]
    snippets = [_clean(m.group("snip")) for m in _SNIP.finditer(body)]

    out = []
    for i, (href, title) in enumerate(links[:MAX_RESULTS]):
        out.append({
            "title": title,
            "url": _unwrap(href),
            "snippet": snippets[i] if i < len(snippets) else "",
        })
    return out


PROVIDERS = {"tavily": _tavily, "brave": _brave, "serper": _serper,
             "exa": _exa, "ddg": _ddg}


async def search(query):
    """Return {"answer": str|None, "results": [{title, url, snippet}, ...]}.

    Never raises: a failed search should degrade the answer, not kill the turn.
    Only Tavily supplies `answer`; the others return results alone.
    """
    name = provider()
    fn = PROVIDERS.get(name, _ddg)
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            out = await fn(session, query)
    except (asyncio.TimeoutError, aiohttp.ClientError, KeyError, ValueError):
        return {"answer": None, "results": []}

    if isinstance(out, dict):
        return out
    return {"answer": None, "results": out}


SNIPPET_CHARS = 500


def as_context(found):
    """Flatten a search payload into something worth putting in a prompt."""
    results = found.get("results") or []
    if not results and not found.get("answer"):
        return "No search results were returned."

    lines = []
    if found.get("answer"):
        lines.append(f"Summary of the sources:\n{found['answer']}")
    for i, r in enumerate(results, 1):
        snippet = (r["snippet"] or "")[:SNIPPET_CHARS]
        lines.append(f"[{i}] {r['title']}\n{snippet}\nSource: {r['url']}")
    return "\n\n".join(lines)


if __name__ == "__main__":
    # python search.py "some query"  — check a key works without starting the app.
    import sys

    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

    query = " ".join(sys.argv[1:]) or "who won the 2026 super bowl"
    name = provider()
    keyed = {"tavily": "TAVILY_API_KEY", "brave": "BRAVE_API_KEY",
             "serper": "SERPER_API_KEY", "exa": "EXA_API_KEY"}

    print(f"provider: {name}")
    if name in keyed:
        # Never print the key itself, just enough to confirm it was picked up.
        value = os.getenv(keyed[name], "")
        print(f"{keyed[name]}: set, {len(value)} chars, ends {value[-4:]!r}")
    else:
        print("no search key found — using the keyless DuckDuckGo fallback")

    found = asyncio.get_event_loop().run_until_complete(search(query))
    print(f"\n{query!r} -> {len(found['results'])} results")
    if found.get("answer"):
        print(f"  answer: {found['answer'][:200]}")
    for r in found["results"]:
        print(f"  - {r['title'][:70]}")
        print(f"    {r['url'][:80]}")
        print(f"    {r['snippet'][:110]}")
    if not found["results"]:
        print("  nothing came back — bad key, no quota, or the provider is unreachable")
