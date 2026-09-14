"""Web search for the voice demo.

Pluggable on purpose. Set SEARCH_PROVIDER, or just supply one of the API keys
below and the right provider is chosen for you:

    tavily   TAVILY_API_KEY    answer-shaped results, best fit for a voice reply
    brave    BRAVE_API_KEY     general web index
    serper   SERPER_API_KEY    Google results via serper.dev
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
    return "ddg"


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


async def _tavily(session, query):
    async with session.post(
        "https://api.tavily.com/search",
        json={
            "api_key": os.environ["TAVILY_API_KEY"],
            "query": query,
            "max_results": MAX_RESULTS,
            "search_depth": "basic",
        },
    ) as r:
        data = await r.json()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
        for x in data.get("results", [])[:MAX_RESULTS]
    ]


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


PROVIDERS = {"tavily": _tavily, "brave": _brave, "serper": _serper, "ddg": _ddg}


async def search(query):
    """Return up to MAX_RESULTS {title, url, snippet} dicts. Never raises: a
    failed search should degrade the answer, not kill the turn."""
    name = provider()
    fn = PROVIDERS.get(name, _ddg)
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            return await fn(session, query)
    except (asyncio.TimeoutError, aiohttp.ClientError, KeyError, ValueError):
        return []


def as_context(results):
    """Flatten results into something worth putting in a prompt."""
    if not results:
        return "No search results were returned."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\n{r['snippet']}\nSource: {r['url']}")
    return "\n\n".join(lines)
