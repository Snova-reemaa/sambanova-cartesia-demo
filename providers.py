"""The model providers VoxNova can talk to, all through one OpenAI-compatible client.

SambaNova is the default and the only one the app itself uses: every normal
call still goes through sambanova.get_client() with no provider named,
exactly as before. The others are here for the side-by-side comparison
(compare.py, /compare).

Which model each provider runs is a setting, not code. Normally one line:

    COMPARE_MODEL=gpt-oss-120b

names the model once, and MODEL_IDS below turns it into each provider's own
id for it. A provider whose id is not in the table, or that should run
something else, takes its own line:

    COMPARE_MODEL_TOGETHER=openai/gpt-oss-120b

Nothing is picked by default: until COMPARE_MODEL is set the comparison
says so instead of choosing for you.

Each provider is also labelled serverless (shared, pay per token) or
dedicated (a deployment of its own), so a result is never shown without
saying which it was. Serverless unless COMPARE_DEPLOYMENT_<NAME> says
otherwise.
"""

import os

from openai import AsyncOpenAI

PROVIDERS = {
    "sambanova": {
        "label": "SambaNova",
        "base_url": lambda: os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1"),
        "key": "SAMBANOVA_API_KEY",
        # SambaNova closes every stream with a usage frame unasked; the
        # others need asking.
        "ask_for_usage": False,
    },
    "fireworks": {
        "label": "Fireworks",
        "base_url": lambda: os.getenv("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"),
        "key": "FIREWORKS_API_KEY",
        "ask_for_usage": True,
    },
    "together": {
        "label": "Together",
        "base_url": lambda: os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1"),
        "key": "TOGETHER_API_KEY",
        "ask_for_usage": True,
    },
    "novita": {
        "label": "Novita",
        # Not yet tried: there is no key to try it with. Override if wrong.
        "base_url": lambda: os.getenv("NOVITA_BASE_URL", "https://api.novita.ai/v3/openai"),
        "key": "NOVITA_API_KEY",
        "ask_for_usage": True,
    },
}

# One model, each provider's name for it. Checked against the providers'
# serverless model lists on 5 November 2026 (bench/models.py); Together's
# could not be listed (its key was refused) and is left out until it can.
MODEL_IDS = {
    "gpt-oss-120b": {
        "sambanova": "gpt-oss-120b",
        "fireworks": "accounts/fireworks/models/gpt-oss-120b",
    },
    "minimax-m3.1-flash": {
        "sambanova": "MiniMax-M3.1-Flash",
        "fireworks": "accounts/fireworks/models/minimax-m3p1-flash",
    },
}


def names():
    return list(PROVIDERS)


def has_key(name):
    return bool(os.getenv(PROVIDERS[name]["key"], "").strip())


def model_for(name):
    """This provider's model id for the comparison, or None if not set."""
    own = os.getenv(f"COMPARE_MODEL_{name.upper()}", "").strip()
    if own:
        return own
    chosen = os.getenv("COMPARE_MODEL", "").strip()
    if not chosen:
        return None
    return MODEL_IDS.get(chosen, {}).get(name)


def deployment(name):
    """'serverless' or 'dedicated': said on every result."""
    value = os.getenv(f"COMPARE_DEPLOYMENT_{name.upper()}", "serverless").strip().lower()
    return "dedicated" if value == "dedicated" else "serverless"


def describe(name):
    """What the page needs to show a provider, and whether it can run."""
    p = PROVIDERS[name]
    model = model_for(name)
    why = None
    if not has_key(name):
        why = f"No {p['key']} in .env"
    elif not model:
        why = ("Set COMPARE_MODEL" if not os.getenv("COMPARE_MODEL")
               else f"No {name} id for {os.getenv('COMPARE_MODEL')}: set COMPARE_MODEL_{name.upper()}")
    return {"name": name, "label": p["label"], "model": model,
            "deployment": deployment(name), "ready": why is None, "why": why}


_clients = {}


def client(name):
    """One client per provider, kept, so its connection stays warm between
    runs the way the app's own does between turns."""
    if name not in _clients:
        p = PROVIDERS[name]
        _clients[name] = AsyncOpenAI(api_key=os.environ[p["key"]], base_url=p["base_url"](),
                                     max_retries=0)
    return _clients[name]
