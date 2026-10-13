"""What a turn costs, for the live cost meter.

Prices live in pricing.json so they can be corrected without touching code.
They are estimates, and the screen says so.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.getenv("PRICING_FILE") or os.path.join(HERE, "pricing.json")


def load():
    try:
        with open(PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"sambanova_per_million_tokens": {"default": [0.6, 1.2]},
                "cartesia_per_character": 0.00004, "human_agent_cost_per_call": None}


PRICES = load()


def per_million(model):
    table = PRICES.get("sambanova_per_million_tokens", {})
    return table.get(model) or table.get("default") or [0.6, 1.2]


class Meter:
    """Tokens by model and characters spoken, for one turn."""

    def __init__(self):
        self.tokens = {}       # model -> [prompt, completion]
        self.chars = 0

    def add(self, model, prompt=0, completion=0):
        if not model:
            return
        slot = self.tokens.setdefault(model, [0, 0])
        slot[0] += int(prompt or 0)
        slot[1] += int(completion or 0)

    def add_usage(self, model, usage):
        """Accepts an OpenAI-style usage object or SambaNova's stats dict."""
        if usage is None:
            return
        get = usage.get if isinstance(usage, dict) else (lambda k, d=None: getattr(usage, k, d))
        self.add(model, get("prompt_tokens", 0) or 0, get("completion_tokens", 0) or 0)

    def merge(self, other):
        for model, (p, c) in other.tokens.items():
            self.add(model, p, c)
        self.chars += other.chars

    def summary(self):
        sn = 0.0
        models = []
        for model, (p, c) in self.tokens.items():
            pin, pout = per_million(model)
            cost = p / 1e6 * pin + c / 1e6 * pout
            sn += cost
            models.append({"model": model, "prompt": p, "completion": c, "usd": round(cost, 6)})
        tts = self.chars * float(PRICES.get("cartesia_per_character") or 0)
        return {
            "sambanova_usd": round(sn, 6),
            "cartesia_usd": round(tts, 6),
            "total_usd": round(sn + tts, 6),
            "prompt_tokens": sum(m["prompt"] for m in models),
            "completion_tokens": sum(m["completion"] for m in models),
            "characters": self.chars,
            "models": sorted(models, key=lambda m: -m["usd"]),
        }


def human_cost():
    value = PRICES.get("human_agent_cost_per_call")
    return float(value) if isinstance(value, (int, float)) else None
