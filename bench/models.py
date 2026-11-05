"""Which models each provider serves serverless, and which they share.

    venv/bin/python bench/models.py

Free: it only lists models. Keys come from .env and are never printed.
SambaNova's list is all serverless (SambaCloud has no other kind on this
account). Fireworks' serverless models come from its own filter
(supports_serverless). Together's list marks dedicated-only models by
type; if its key is refused the script says so rather than guessing.

Writes bench/MODELS.md with the date, so the snapshot says when it was true.
"""

import datetime
import json
import os
import re
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def env():
    out = {}
    for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip("\"'")
    return out


def get(url, key):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "Accept": "application/json",
                                               "User-Agent": "voxnova-bench"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(model_id):
    """A comparable name: lower case, no org or path, no punctuation."""
    tail = model_id.split("/")[-1].lower()
    tail = tail.replace("p", ".") if re.search(r"\dp\d", tail) else tail
    return re.sub(r"[^a-z0-9.]+", "-", tail).strip("-")


def sambanova(keys):
    data = get(os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1") + "/models",
               keys["SAMBANOVA_API_KEY"])["data"]
    return [m["id"] for m in data]


def fireworks(keys):
    url = ("https://api.fireworks.ai/v1/accounts/fireworks/models"
           "?filter=supports_serverless%3Dtrue&pageSize=200")
    models = get(url, keys["FIREWORKS_API_KEY"])["models"]
    return [m["name"] for m in models if m.get("kind") != "EMBEDDING_MODEL"]


def together(keys):
    data = get("https://api.together.xyz/v1/models", keys["TOGETHER_API_KEY"])
    return [m["id"] for m in data if m.get("type") == "chat"]


def main():
    keys = env()
    found, problems = {}, {}
    for name, fetch, key in (("sambanova", sambanova, "SAMBANOVA_API_KEY"),
                             ("fireworks", fireworks, "FIREWORKS_API_KEY"),
                             ("together", together, "TOGETHER_API_KEY"),
                             ("novita", None, "NOVITA_API_KEY")):
        if not keys.get(key):
            problems[name] = f"no {key} in .env"
            continue
        if fetch is None:
            problems[name] = "listing not written yet"
            continue
        try:
            found[name] = fetch(keys)
        except urllib.error.HTTPError as exc:
            problems[name] = f"HTTP {exc.code} {'(key refused)' if exc.code == 401 else ''}".strip()
        except Exception as exc:
            problems[name] = type(exc).__name__

    by_norm = {}
    for name, ids in found.items():
        for model_id in ids:
            by_norm.setdefault(norm(model_id), {})[name] = model_id
    shared = {n: ids for n, ids in by_norm.items() if len(ids) >= 2}
    everyone = [n for n, ids in shared.items() if len(ids) == len(found) and len(found) >= 3]

    today = datetime.date.today().isoformat()
    lines = [f"# Serverless models, {today}", "",
             "Made by `bench/models.py`, which only lists models (free).", ""]
    for name in ("sambanova", "fireworks", "together", "novita"):
        if name in found:
            lines += [f"## {name} ({len(found[name])})", ""]
            lines += [f"- `{m}`" for m in sorted(found[name])] + [""]
        else:
            lines += [f"## {name}", "", f"Not listed: {problems[name]}.", ""]
    lines += ["## Shared", ""]
    if shared:
        lines += ["| Model | " + " | ".join(found) + " |", "|---|" + "---|" * len(found)]
        for n, ids in sorted(shared.items()):
            lines.append(f"| {n} | " + " | ".join(f"`{ids[p]}`" if p in ids else "—" for p in found) + " |")
    else:
        lines.append("None found.")
    lines += ["", "All three share: " + (", ".join(f"`{n}`" for n in everyone) if everyone else
              ("none" if len(found) >= 3 else
               f"can't tell yet ({', '.join(f'{k}: {v}' for k, v in problems.items())})")), ""]
    with open(os.path.join(HERE, "MODELS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
