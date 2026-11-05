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

import asyncio
import ipaddress
import os
import re
import socket
import urllib.parse

import httpx
from openai import AsyncOpenAI

PROVIDERS = {
    "sambanova": {
        "label": "SambaNova",
        "base_url": lambda: os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1"),
        "key": "SAMBANOVA_API_KEY",
        # SambaNova closes every stream with a usage frame unasked; the
        # others need asking.
        "ask_for_usage": False,
        # Accepts reasoning_effort on its reasoning models (tried, 5 Nov 2026).
        "reasoning_effort": True,
    },
    "fireworks": {
        "label": "Fireworks",
        "base_url": lambda: os.getenv("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"),
        "key": "FIREWORKS_API_KEY",
        "ask_for_usage": True,
        "reasoning_effort": True,
    },
    "together": {
        "label": "Together",
        "base_url": lambda: os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1"),
        "key": "TOGETHER_API_KEY",
        "ask_for_usage": True,
        # Not tried yet (its key is refused): left at its default until it is.
        "reasoning_effort": False,
    },
    "novita": {
        "label": "Novita",
        # Not yet tried: there is no key to try it with. Override if wrong.
        "base_url": lambda: os.getenv("NOVITA_BASE_URL", "https://api.novita.ai/v3/openai"),
        "key": "NOVITA_API_KEY",
        "ask_for_usage": True,
        "reasoning_effort": False,
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


# Reasoning models think before they answer, and on VoxNova's prompt at
# their default effort they can spend every token thinking and never say a
# word (seen on 5 Nov 2026). The comparison asks them to keep it short,
# where the provider is known to accept that, and says so on the result.
REASONING_MODELS = ("gpt-oss", "minimax-m3", "minimax-m2", "deepseek-r", "qwq", "o1", "o3", "o4")
REASONING_EFFORT = os.getenv("COMPARE_REASONING_EFFORT", "low").strip().lower()


def is_reasoning(model):
    tail = (model or "").lower().split("/")[-1]
    return any(tail.startswith(p) for p in REASONING_MODELS)


def reasoning_for(name, model):
    """The reasoning_effort to send, or None: only to a reasoning model, on
    a provider known to take it, and only if one is set."""
    if not REASONING_EFFORT or not is_reasoning(model):
        return None
    if not PROVIDERS.get(name, {}).get("reasoning_effort"):
        return None
    return REASONING_EFFORT


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
        # Shown in the picker as "Novita (no key set)", and the option is
        # disabled until the key is in .env.
        why = "no key set"
    elif not model:
        why = ("Set COMPARE_MODEL" if not os.getenv("COMPARE_MODEL")
               else f"No {name} id for {os.getenv('COMPARE_MODEL')}: set COMPARE_MODEL_{name.upper()}")
    return {"name": name, "label": p["label"], "model": model,
            "deployment": deployment(name), "ready": why is None, "why": why}


class Side:
    """One side of a comparison: who it is, how to reach it, how to label it."""

    def __init__(self, name, label, client, model, ask_for_usage, deployment,
                 note=None, secret=None, reasoning=None):
        self.name, self.label, self.client, self.model = name, label, client, model
        self.ask_for_usage, self.deployment, self.note = ask_for_usage, deployment, note
        # reasoning_effort sent, or None; for a reasoning model, None means
        # the provider's own default, and the page says which.
        self.reasoning = reasoning
        self.reasoning_model = is_reasoning(model)
        # Only for scrubbing it out of anything shown; never sent anywhere.
        self.secret = secret

    def public(self):
        """What the page may see. Never the key."""
        return {"name": self.name, "label": self.label, "model": self.model,
                "deployment": self.deployment, "note": self.note, "ready": True,
                "reasoning": (self.reasoning or "provider default") if self.reasoning_model else None}

    async def close(self):
        if self.name == "custom":
            await self.client.close()
            self.client = self.secret = None


def side(name):
    p = PROVIDERS[name]
    model = model_for(name)
    return Side(name, p["label"], client(name), model, p["ask_for_usage"],
                deployment(name), reasoning=reasoning_for(name, model))


# ================================================== a provider the user brings
#
# "Your own provider": any OpenAI-compatible endpoint, with the visitor's
# own key, raced against ours. The server makes that request from inside
# the cluster, to an address a stranger typed, so it is fenced in:
#
# - https only, and no user:password in the address;
# - the host is resolved first and every address it gives must be public:
#   no private, loopback, link-local, shared (100.64.0.0/10, where the pods
#   live), multicast or reserved ranges, in IPv4 or IPv6, nor IPv4 hidden
#   inside IPv6;
# - the connection is pinned to the address that was checked, so a name
#   that resolves somewhere else a moment later (DNS rebinding) cannot
#   take it inside; TLS is still verified against the name;
# - no redirects, and the transport refuses any host but the one given;
# - a short timeout, the same max_tokens as everyone, no retries.
#
# The key lives only in that run's client and is dropped with it: it is
# never logged, never written down, never sent back to the page, and is
# scrubbed out of any error a provider echoes it in. The bench recorder
# only ever wraps SambaNova's own client, so it never sees this one.

CUSTOM_TIMEOUT_SECONDS = float(os.getenv("COMPARE_CUSTOM_TIMEOUT_SECONDS", "30"))
CUSTOM_CONNECT_SECONDS = 5.0


class Refused(Exception):
    """A provider address that will not be called, and why, for the page."""


def check_url(base_url):
    """(scheme-checked URL, host, port), or Refused."""
    url = (base_url or "").strip()
    if not url:
        raise Refused("Enter your provider's base URL, for example https://api.example.com/v1")
    if len(url) > 300:
        raise Refused("That URL is too long.")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise Refused("Use an https:// address. Plain http is not allowed.")
    if parts.username or parts.password:
        raise Refused("Leave the user name and password out of the URL; the key goes in its own field.")
    if not parts.hostname:
        raise Refused("That URL has no host name.")
    try:
        port = parts.port or 443
    except ValueError:
        raise Refused("That URL's port is not a number.")
    host = parts.hostname.lower()
    # An IP address must be written plainly. "2130706433", "0177.0.0.1" and
    # "0x7f.1" are all 127.0.0.1 to some resolvers and something else to
    # others (macOS reads 0177 as 177), and a guard that depends on which
    # machine it runs on is not a guard.
    labels = [part for part in host.split(".") if part]
    if labels and all(re.fullmatch(r"0x[0-9a-f]*|[0-9]+", part) for part in labels):
        try:
            if str(ipaddress.ip_address(host)) != host:
                raise ValueError
        except ValueError:
            raise Refused("That host looks like an IP address written in an unusual form. "
                          "Use the provider's host name, or the address written plainly.")
    return url.rstrip("/"), host, port


def blocked(ip, allow=()):
    """True for any address a request from inside the cluster must not reach."""
    ip = ipaddress.ip_address(ip)
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if any(ip in net for net in allow):
        return False
    return (not ip.is_global or ip.is_multicast or ip.is_reserved
            or ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified)


async def resolve(host, port):
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise Refused(f"Could not find {host}. Check the URL.")
    return sorted({info[4][0] for info in infos})


class PinnedTransport(httpx.AsyncBaseTransport):
    """Connects only to the one address that was checked, for the one host
    that was given, and verifies TLS against that host's name."""

    def __init__(self, host, ip, verify=True):
        self.host, self.ip = host, ip
        self.inner = httpx.AsyncHTTPTransport(verify=verify, retries=0)

    async def handle_async_request(self, request):
        if request.url.host != self.host or request.url.scheme != "https":
            raise Refused("The provider tried to send the request somewhere else.")
        request.url = request.url.copy_with(host=self.ip)
        request.extensions = {**request.extensions, "sni_hostname": self.host}
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


async def custom_side(base_url, key, model, allow=(), verify=True):
    """A Side for the visitor's own provider, or Refused with the reason.

    `allow` and `verify` are for the tests' own local https server only;
    nothing the page sends can set them.
    """
    url, host, port = check_url(base_url)
    key = (key or "").strip()
    model = (model or "").strip()
    if not key:
        raise Refused("Enter your provider's API key.")
    if not model or len(model) > 200:
        raise Refused("Enter the model name your provider uses, for example its id from /v1/models.")
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        addresses = await resolve(host, port)
    if not addresses:
        raise Refused(f"Could not find {host}. Check the URL.")
    if any(blocked(ip, allow) for ip in addresses):
        raise Refused(f"{host} points inside a private network, so it is blocked.")
    try:
        http = httpx.AsyncClient(
            transport=PinnedTransport(host, addresses[0], verify=verify),
            follow_redirects=False,
            timeout=httpx.Timeout(CUSTOM_TIMEOUT_SECONDS, connect=CUSTOM_CONNECT_SECONDS))
        client = AsyncOpenAI(api_key=key, base_url=url, max_retries=0, http_client=http,
                             timeout=CUSTOM_TIMEOUT_SECONDS)
    except (httpx.InvalidURL, ValueError):
        raise Refused("That URL isn't valid. Check it and try again.")
    return Side("custom", f"Your provider ({model})", client, model, True,
                "serverless/unknown",
                note=f"{host} · we can't verify how your provider is set up", secret=key)


_clients = {}


def client(name):
    """One client per provider, kept, so its connection stays warm between
    runs the way the app's own does between turns."""
    if name not in _clients:
        p = PROVIDERS[name]
        _clients[name] = AsyncOpenAI(api_key=os.environ[p["key"]], base_url=p["base_url"](),
                                     max_retries=0)
    return _clients[name]
