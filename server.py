"""Live SambaNova -> Cartesia voice demo.

Serves static/ and a websocket at /ws. One Cartesia socket is opened per browser
session and kept warm across turns, so "time to first sound" reflects what a real
user would feel rather than including a cold handshake.

    python server.py            # then open http://localhost:8080
"""

import asyncio
import hmac
import json
import os
import random
import re
import secrets
import time

from aiohttp import web, WSMsgType
from dotenv import load_dotenv

load_dotenv()

from sambanova import (get_client as get_llm, get_model, stream_tools,
                       describe_images, get_vision_model)
from cartesia_tts import get_client as get_tts, output_format
from search import search, as_context, provider as search_provider
from knowledge_base import Knowledge, as_context as kb_context
import languages

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

COOKIE_NAME = "voiceloop_session"
SESSIONS = set()

# Every turn spends real API credit, so the app is never served ungated. Set
# DEMO_PASSPHRASE to something you can read out on a call; otherwise one is
# generated at startup and printed to the console.
PASSPHRASE = os.getenv("DEMO_PASSPHRASE", "").strip()
GENERATED_PASSPHRASE = not PASSPHRASE
if GENERATED_PASSPHRASE:
    PASSPHRASE = secrets.token_urlsafe(9)

SAMPLE_RATE = 44100
# Written for the ear, not the page. The earlier version ("you are a voice
# assistant, reply in two or three sentences") produced correct answers that
# sounded like a manual being read aloud.
SYSTEM_PROMPT = (
    "You are a support agent on a phone call. You are talking, not writing — "
    "every word you produce is spoken aloud to a real person.\n"
    "Talk the way people actually talk: contractions, short sentences, one idea "
    "at a time. Lead with the answer, not a preamble. Never read out a list, "
    "never say 'firstly' or 'additionally', never narrate what you are doing, "
    "and never use markdown, bullets or emoji.\n"
    "React before you explain — 'ah, I see', 'okay, so', 'right'. Two or three "
    "sentences, then stop and let them reply. Ending on a question is usually "
    "better than ending on a lecture."
)

# On a video call the phone-agent framing makes it deny having eyes — "I'm just
# a voice on the phone, so no, I can't see you" — which is false the moment the
# camera is up, and reads as broken.
CALL_PROMPT = (
    "You are on a video call with the caller. You can see them and anything "
    "they hold up to the camera: when they show you something you will be told "
    "what is in view.\n"
    "Never claim you cannot see, and never say you are only a voice. Otherwise "
    "do not go on about the camera — mention it only when looking at something "
    "would genuinely help, and answer normally the rest of the time."
)

# Left to itself the model searches for everything, including definitions it
# plainly knows, which costs a round trip on every question. Spelling out when
# NOT to search took this from 5/10 to 9/10 on a small trigger set.
SEARCH_RULES = (
    "\nYou already know a great deal. Answer directly from your own knowledge. "
    "Call web_search ONLY when the answer depends on information that changes "
    "over time or is newer than your training. Never search to define a term, "
    "explain a concept, do arithmetic, or answer anything stable. If in doubt, "
    "answer without searching. Never say anything about tools, functions, "
    "searching, or your reasoning about whether to search.\n"
    "If the caller has shown you something and wants it found, priced, matched "
    "or bought, search using the details you can see. A photo is a reason to "
    "search, not a reason to describe."
)

SEARCH_TOOL = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Look up information that changes over time: recent events, live prices, "
            "scores, today's news, anything newer than your training data. Also for "
            "finding, pricing, matching or shopping for a specific item, including one "
            "the caller has shown you in a photo. Never for definitions, concepts, "
            "history, arithmetic or general explanations."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}]

# Spoken while the search runs, into the same synthesis context as the answer,
# so the voice carries straight on instead of going quiet.
def bridge_phrase(query, already_spoke=False, code="en"):
    # Deliberately generic. Naming the query back produces things like "let me
    # check the latest on latest news SambaNova", and it has to sound right for
    # every query, not most of them. Two sentences buys about two seconds of
    # speech, which comfortably covers a search.
    #
    # After a photo the caller has already heard "let me take a look at that",
    # and stacking a second full bridge on top reads as stalling.
    first, second = SEARCHING_PHRASE.get(code, SEARCHING_PHRASE["en"])
    return second if already_spoke else first


# Half the two seconds before a caller hears anything is the round trip to
# SambaNova, and none of it is inference. A person would not sit in silence for
# that; they would say "right" and then answer. Synthesis starts on this the
# moment the prompt lands, so first sound stops waiting on the model at all.
BACKCHANNEL = {
    "en": ("Right, let me see. ", "Okay, one second. ", "Sure, let me think. ",
           "Mm-hm, hang on. ", "Got it, just a moment. "),
    "es": ("Vale, un momento. ", "Claro, déjame ver. ", "Muy bien, un segundo. "),
    "fr": ("D'accord, un instant. ", "Bien sûr, voyons voir. ", "Très bien, une seconde. "),
    "de": ("Gut, einen Moment. ", "Klar, lassen Sie mich sehen. ", "In Ordnung, eine Sekunde. "),
    "it": ("Va bene, un momento. ", "Certo, fammi vedere. ", "Perfetto, un secondo. "),
    "pt": ("Certo, um momento. ", "Claro, deixe-me ver. ", "Muito bem, um segundo. "),
    "nl": ("Goed, een moment. ", "Zeker, even kijken. ", "Prima, een seconde. "),
}

LOOKING_PHRASE = {
    "en": ("Let me take a look at that. ", "Let me look through those. "),
    "es": ("Déjame echar un vistazo. ", "Déjame revisarlas. "),
    "fr": ("Laissez-moi regarder ça. ", "Laissez-moi les regarder. "),
    "de": ("Lassen Sie mich das ansehen. ", "Lassen Sie mich die ansehen. "),
    "it": ("Fammi dare un'occhiata. ", "Fammi guardare queste. "),
    "pt": ("Deixe-me dar uma olhada. ", "Deixe-me ver essas. "),
    "nl": ("Laat me daar even naar kijken. ", "Laat me die even bekijken. "),
}

SEARCHING_PHRASE = {
    "en": ("One moment. Let me look that up for you. ", "Right, let me find some options. "),
    "es": ("Un momento, voy a buscarlo. ", "Vale, déjame buscar opciones. "),
    "fr": ("Un instant, je cherche ça. ", "Bien, je vais trouver des options. "),
    "de": ("Einen Moment, ich schaue nach. ", "Gut, ich suche ein paar Optionen. "),
    "it": ("Un momento, lo cerco subito. ", "Bene, cerco qualche opzione. "),
    "pt": ("Um momento, vou procurar. ", "Certo, vou encontrar opções. "),
    "nl": ("Een moment, ik zoek het op. ", "Goed, ik zoek wat opties. "),
}


def backchannel(recent, code="en"):
    """Pick an acknowledgement, avoiding the last couple used."""
    pool = BACKCHANNEL.get(code, BACKCHANNEL["en"])
    fresh = [w for w in pool if w not in recent]
    return random.choice(fresh or list(pool))


# Provider errors quote the credential that failed — "Incorrect API key
# provided: ba630f*****3cca" — and a browser is the last place that belongs.
# Nothing from an exception is ever forwarded verbatim; the console keeps the
# detail, the caller gets a sentence about what to do.
SECRETISH = re.compile(
    r"(sk-[A-Za-z0-9_\-]{6,}|tvly-[A-Za-z0-9_\-]{6,}|"
    r"[A-Za-z0-9]{6}\*{3,}[A-Za-z0-9]{3,}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)

TROUBLE = (
    ("invalid_api_key", "authentication_error", "401", "incorrect api key"),
    ("payment_method_required", "402", "credits limit", "balance_units",
     "ran out of credits", "upgrade your subscription", "credit limit"),
    ("429", "rate limit", "high demand", "concurrency limit"),
    ("timeout", "timed out"),
)

SAYS = (
    "The AI service rejected our credentials. Check the API key in .env.",
    "The AI service is out of credit. Top up the account to carry on.",
    "The AI service is busy right now. Try that again in a moment.",
    "That took too long and timed out. Try again.",
)


def safe_error(exc, where="that"):
    """A sentence for the browser, with the provider's wording left behind."""
    text = str(exc).lower()
    for phrases, says in zip(TROUBLE, SAYS):
        if any(p in text for p in phrases):
            return says
    return f"Something went wrong with {where}. The server log has the detail."


def log_error(exc, context):
    """Full detail, console only — but still never the credential itself."""
    print(f"  ! {context}: {SECRETISH.sub('[redacted]', str(exc))[:400]}")


KB = Knowledge()

# The documents are English and retrieval is lexical, so a Spanish question
# matches nothing and the agent invents a shipping policy. Translating the
# question first is a round trip, but only on non-English turns, and the
# holding phrase is already covering that second anyway. Uses the cheapest
# model on the account.
# Not gpt-oss-120b despite being cheapest: it reasons before answering, and a
# 60 token budget was consumed by the reasoning, so "¿hacéis envíos a España?"
# came back as "Do". Llama answers directly.
TRANSLATOR = os.getenv("SAMBANOVA_TRANSLATE_MODEL", "Meta-Llama-3.3-70B-Instruct")


async def in_english(llm, text, code):
    if code == "en":
        return text
    try:
        reply = await llm.chat.completions.create(
            model=TRANSLATOR, max_tokens=120,
            messages=[{"role": "user", "content":
                       "Translate this customer support question into English. "
                       "Reply with only the English translation, no preamble, no "
                       f"quotes.\n\n{text}"}])
        out = (reply.choices[0].message.content or "").strip()
        return out or text
    except Exception as exc:
        log_error(exc, "translating for retrieval")
        return text



def language_rule(code):
    """Follow the caller. Switching language mid-call is the normal case in a
    contact centre, not an edge case."""
    return (
        f"\nThe caller is speaking {languages.name(code)}. Reply in "
        f"{languages.name(code)} and nothing else — not a translation, not a "
        "mixture. If they switch language again, switch with them."
    )


def policy_prompt(hits):
    """Ground policy answers in the documents, and make silence the fallback.

    Before this the agent invented opening hours and a refund policy, fluently
    and in the voice of someone who knew. That is harmless in a tech demo and
    fatal in front of a customer.
    """
    return (
        "Company documentation relevant to what they asked:\n\n"
        + kb_context(hits) +
        "\n\nThese are the real policies. Answer from them and nothing else: do "
        "not round the numbers, soften the conditions, or add exceptions that are "
        "not written above.\n"
        "Read the question again and check it is actually answered above. These "
        "sections were picked by keyword and may be about something adjacent "
        "rather than what was asked. If the specific thing they want is not "
        "stated, say you will check and come back to them — do not reason from "
        "the nearest related policy to a plausible answer.\n"
        "Where a section names the specific thing they asked about, it beats the "
        "general rule — an exclusion list applies even when a broader policy "
        "would have allowed it. Quoting the general policy over a stated "
        "exclusion is the worst thing you can do here.\n"
        "If the policy turns on a condition they have not told you, ask about "
        "that condition rather than assuming the answer that suits them.\n"
        "Still speak naturally, two or three sentences; do not read the document "
        "aloud or cite it."
    )


NO_POLICY = (
    "\nNothing in the company documentation covers what they just asked. Do not "
    "invent an answer about company policy, hours, prices, delivery or warranty "
    "— say you will find out and come back to them. Ordinary conversation and "
    "general knowledge are still fine."
)


# M3 holds 1.05M tokens, the standout number on this account, and is the wrong
# model for the live turn — it ignores what the camera saw. Wrap-up is the job
# it is actually suited to: the whole call at once, after the caller has gone,
# where latency does not matter.
SUMMARY_MODEL = os.getenv("SAMBANOVA_SUMMARY_MODEL", "MiniMax-M3")

SUMMARY_PROMPT = (
    "You are writing up a support call that has just ended. Reply with exactly "
    "these five lines and nothing else:\n"
    "SUMMARY: one sentence on what the customer wanted.\n"
    "OUTCOME: resolved, pending, or escalated.\n"
    "ACTIONS: what was promised, or none.\n"
    "SENTIMENT: how the customer sounded, in two or three words.\n"
    "QUALITY: one sentence on how the agent handled it, including anything "
    "they got wrong or should have asked."
)


async def write_up(llm, history):
    """Summary, disposition and a QA note once the call is over."""
    transcript = "\n".join(
        f"{'Customer' if m['role'] == 'user' else 'Agent'}: {m['content']}"
        for m in history if m["role"] in ("user", "assistant")
    )
    if not transcript.strip():
        return None
    reply = await llm.chat.completions.create(
        model=SUMMARY_MODEL, max_tokens=400,
        messages=[{"role": "system", "content": SUMMARY_PROMPT},
                  {"role": "user", "content": transcript}],
    )
    text = (reply.choices[0].message.content or "").strip()
    text = re.sub(r"<mm:think>.*?</mm:think>", "", text, flags=re.S).strip()
    usage = getattr(reply, "usage", None)
    return {
        "text": text,
        "model": SUMMARY_MODEL,
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "turns": len([m for m in history if m["role"] == "user"]),
    }


GENERIC_LOOK = (
    "Say what you see, concretely: the object, its colour, material and style, "
    "any damage, text, error message, lights or markings. One or two plain "
    "sentences, no headings. Say so if you cannot tell."
)

MAX_FRAMES = 8        # gemma read 6 frames in order reliably; 8 leaves headroom
MAX_IMAGE_BYTES = 1_500_000   # per frame, after the browser has resized it


def looks_like_image(url):
    return isinstance(url, str) and url.startswith("data:image/")


def clean_attachments(raw):
    """Keep only well-formed, reasonably sized image data URLs."""
    if not isinstance(raw, list):
        return []
    return [u for u in raw[:MAX_FRAMES]
            if looks_like_image(u) and len(u) <= MAX_IMAGE_BYTES]


def vision_question(prompt, count):
    """Ask for observations, not a report — and keep the asking short.

    The long version of this prompt cost 413 prompt tokens and returned nine:
    "I see a pair of black oval glasses." The short version costs 312 and
    returns more detail. Instructions are prompt tokens too, and the model was
    spending its attention on the brief rather than the picture.
    """
    if count > 1:
        return (
            f"{count} frames from a video, in order. Say what changes across them "
            "and anything that matters: damage, error text, lights, model numbers. "
            f"Two or three plain sentences, no headings. Caller said: \"{prompt}\""
        )
    return (
        "Say what you see, concretely: the object, its colour, material and style, "
        "any damage, text, error message, lights or markings. One or two plain "
        f"sentences, no headings. Say so if you cannot tell. Caller said: \"{prompt}\""
    )


def seen_prompt(description, count):
    what = "the frames" if count > 1 else "the photo"
    return (
        f"You just looked at {what} the caller sent. Here is what you saw:\n\n"
        f"{description}\n\n"
        "Now do what they actually asked. Read their message again: if they want "
        "something found, priced, identified, matched or bought, use web_search "
        "with a query built from what you just saw — do not answer from memory, "
        "and do not simply describe the picture back to them. They know what they "
        "photographed; describing it is never the answer on its own.\n"
        "Only when they genuinely asked 'what is wrong with this' or 'what is "
        "this' does saying what you see answer the question. Never mention a "
        "vision model, frames, or image analysis."
    )


def grounded_prompt(results):
    return (
        "Web search results:\n\n" + as_context(results) +
        "\n\nAnswer the user's question using these results. Speak naturally in "
        "two or three sentences. Do not mention searching, results, or sources."
    )
MAX_HISTORY = 8  # user+assistant messages retained for context
VOICE_LIMIT = 20  # how many Cartesia voices to offer in the picker
MAX_CONCURRENT = 4  # requests accepted in flight at once on one connection

# One passphrase shared on a call, and a link that outlives it, is somebody
# else spending your credits. These are per browser session.
MAX_TURNS = int(os.getenv("MAX_TURNS_PER_SESSION", "60"))
MAX_MINUTES = int(os.getenv("MAX_SESSION_MINUTES", "45"))

# Cartesia caps concurrent synthesis per subscription (2 on the current plan);
# exceeding it fails the whole context rather than queueing. SambaNova has no
# such limit at this volume, so language-model calls all start immediately and
# only synthesis waits for a slot.
TTS_CONCURRENCY = int(os.getenv("CARTESIA_CONCURRENCY", "2"))
TTS_SLOTS = None  # asyncio.Semaphore, created once the loop exists

# Filled once at startup so every browser session shares one lookup.
CATALOG = {"models": [], "voices": []}


async def load_catalog(llm, tts):
    """Ask both providers what this account can actually use. Failures are not
    fatal — the picker just falls back to whatever .env specifies."""
    try:
        listing = await llm.models.list()
        CATALOG["models"] = sorted(m.id for m in listing.data)
    except Exception as exc:
        print(f"  ! could not list SambaNova models ({exc}); using .env default")
        CATALOG["models"] = [get_model()]

    try:
        page = await tts.voices.list(limit=VOICE_LIMIT)
        items = getattr(page, "data", None) or list(page)
        CATALOG["voices"] = [
            {
                "id": v.id,
                "name": v.name,
                "language": getattr(v, "language", "") or "",
                "description": (getattr(v, "description", "") or "")[:90],
            }
            for v in items
        ]
    except Exception as exc:
        print(f"  ! could not list Cartesia voices ({exc}); using .env default")
        CATALOG["voices"] = []


class Turn:
    """Timings for one prompt, in seconds from the moment the prompt was sent."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.first_token = None
        self.last_token = None
        self.tts_start = None    # when a synthesis slot came free
        self.first_audio = None
        self.last_audio = None
        self.audio_bytes = 0
        self.server = {}         # SambaNova's own inference timings
        self.search_s = None     # seconds spent in web search, if any
        self.vision_s = None     # seconds spent reading attachments, if any
        self.filler_s = None     # when the holding phrase went to synthesis
        self.kb_hits = []        # documentation sections used, if any
        self.language = 'en'

    def now(self):
        return time.perf_counter() - self.t0

    def metrics(self):
        audio_seconds = self.audio_bytes / (SAMPLE_RATE * 2)
        total = self.last_audio or self.now()

        # What the wire cost us: our stopwatch minus SambaNova's own.
        inference = self.server.get("time_to_first_token")
        overhead = None
        if inference is not None and self.first_token is not None:
            overhead = max(0.0, self.first_token - inference)

        return {
            "server": self.server or None,
            "inference_ttft_s": inference,
            "search_s": self.search_s,
            "vision_s": self.vision_s,
            "filler_s": self.filler_s,
            "kb_hits": self.kb_hits,
            "language": self.language,
            "network_overhead_s": overhead,
            "first_token_s": self.first_token,
            "last_token_s": self.last_token,
            "tts_start_s": self.tts_start,
            "first_audio_s": self.first_audio,
            "total_s": total,
            "audio_seconds": audio_seconds,
            "realtime_factor": (audio_seconds / total) if total else None,
        }


def default_voice():
    return os.getenv("CARTESIA_VOICE_ID", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")


def pick_model(requested):
    """Only ever hand the API a model this account was told it has."""
    if requested and (not CATALOG["models"] or requested in CATALOG["models"]):
        return requested
    return get_model()


def pick_voice(requested):
    known = {v["id"] for v in CATALOG["voices"]}
    if requested and (not known or requested in known):
        return requested
    return default_voice()


class Channel:
    """Serialises writes to one browser socket.

    Turns run concurrently, so without this two tasks can interleave halves of
    a frame. Every audio frame is prefixed with its turn id, because binary
    frames carry no other way to say which answer they belong to.
    """

    def __init__(self, ws):
        self.ws = ws
        self.lock = asyncio.Lock()

    async def event(self, payload):
        async with self.lock:
            await self.ws.send_json(payload)

    async def audio(self, turn_id, pcm):
        async with self.lock:
            await self.ws.send_bytes(turn_id.to_bytes(2, "big") + pcm)


async def synthesise(chan, conn, turn_id, text, voice_id):
    """Speak one fixed string — used for the assistant's own 'which first?'
    question, which has no language model in front of it."""
    ctx = conn.context(
        model_id=os.getenv("CARTESIA_MODEL", "sonic-2"),
        voice={"mode": "id", "id": voice_id},
        output_format=output_format(),
        language="en",
    )
    await ctx.push(text)
    await ctx.no_more_inputs()
    async for res in ctx.receive():
        if res.type == "chunk" and res.audio:
            await chan.audio(turn_id, res.audio)
        elif res.type == "error":
            raise RuntimeError(res.message or res.title or "Cartesia error")


async def run_turn(chan, conn, llm, history, turn_id, prompt, model=None, voice=None,
                   can_search=True, attachments=(), on_call=False, prelook=None,
                   recent_fillers=None, speaking=None):
    """Stream one prompt through the LLM into its own Cartesia context, relaying
    text events and raw PCM frames to the browser as they arrive."""
    turn = Turn()
    recent_fillers = recent_fillers if recent_fillers is not None else []
    speaking = speaking if speaking is not None else {"code": "en"}
    was = speaking["code"]
    speaking["code"] = languages.detect(prompt, was)
    turn.language = speaking["code"]
    voice_id = pick_voice(voice)
    llm_model = pick_model(model)
    tts_model = os.getenv("CARTESIA_MODEL", "sonic-2")
    reply = ""

    # Snapshot history now: a turn started alongside others should not see
    # replies that land while it is still running.
    system = SYSTEM_PROMPT + language_rule(speaking["code"])
    if on_call:
        system += "\n" + CALL_PROMPT
    if can_search:
        system += SEARCH_RULES
    messages = [{"role": "system", "content": system}] + list(history) + [
        {"role": "user", "content": prompt}
    ]

    await chan.event({
        "type": "turn_start", "turn_id": turn_id, "prompt": prompt,
        "model": llm_model, "voice_id": voice_id,
        "attachments": prelook["count"] if prelook else len(attachments),
        "language": speaking["code"],
        "language_changed": speaking["code"] != was,
    })

    # The language model runs right away; text waits here for a synthesis slot.
    pending_text = asyncio.Queue()

    async def emit(delta):
        nonlocal reply
        reply += delta
        await chan.event({"type": "token", "turn_id": turn_id, "text": delta, "t": turn.now()})
        await pending_text.put(delta)

    async def mark_first():
        if turn.first_token is None:
            turn.first_token = turn.now()
            await chan.event({"type": "first_token", "turn_id": turn_id, "t": turn.first_token})

    async def look_at_attachments():
        """Say something, look at what was sent, and report back what was seen.

        Deliberately does NOT answer. Looking is an input to the turn, not the
        end of it — the caller may want the thing found, priced or identified,
        which needs the tools that come after.
        """
        count = prelook["count"] if prelook else len(attachments)
        await chan.event({"type": "looking", "turn_id": turn_id,
                          "count": count, "t": turn.now(),
                          "speculative": bool(prelook)})

        one_phrase, many_phrase = LOOKING_PHRASE.get(
            speaking["code"], LOOKING_PHRASE["en"])
        bridge = one_phrase if count == 1 else many_phrase
        await emit(bridge)

        t_look = time.perf_counter()
        if prelook:
            # Started the moment they began speaking, so by now it is usually
            # finished and this returns immediately.
            description, info = await prelook["task"]
        else:
            description, info = await describe_images(
                llm, attachments, vision_question(prompt, count))
        turn.vision_s = time.perf_counter() - t_look
        await chan.event({
            "type": "looked", "turn_id": turn_id, "t": turn.now(),
            "took_s": turn.vision_s, "model": info["model"],
            "prompt_tokens": info["prompt_tokens"],
            "description": description[:400],
        })
        return bridge, description

    async def generate():
        # Everything downstream builds on `convo`, so anything seen in an image
        # is still in context when the model decides whether to search.
        convo = list(messages)

        # Nothing to look at, so nothing else is covering the wait. Skip it for
        # very short turns: "hi there" does not need "right, let me see" in
        # front of it, and those answers come back quickly regardless.
        worth_covering = len(prompt.split()) > 3
        if on_call and worth_covering and not attachments and not prelook:
            filler = backchannel(recent_fillers, speaking["code"])
            recent_fillers.append(filler)
            del recent_fillers[:-2]
            turn.filler_s = turn.now()
            await emit(filler)
            convo = convo + [
                {"role": "assistant", "content": filler.strip()},
                {"role": "system", "content":
                    f"You have already said \"{filler.strip()}\" out loud. Carry "
                    "straight on into the answer. Do not greet, acknowledge or "
                    "say okay again — that has happened."},
            ]

        if attachments or prelook:
            await mark_first()
            bridge, description = await look_at_attachments()
            convo += [
                {"role": "assistant", "content": bridge},
                {"role": "system",
             "content": seen_prompt(description,
                                    prelook["count"] if prelook else len(attachments))},
            ]

        # Retrieval is local and costs microseconds, so it happens on every
        # turn rather than behind a tool call the model has to remember to use.
        # Detection is driven by the reply language and needs real evidence, so
        # it calls a short "livrez-vous en France?" English and skips the
        # translation. Let retrieval decide instead: if nothing matched, try it
        # in English before concluding the documents do not cover it. The cost
        # lands only on turns that found nothing, under the holding phrase.
        hits = KB.find(prompt)
        if not hits:
            lookup = await in_english(llm, prompt, "translate")
            if lookup and lookup.lower() != prompt.lower():
                hits = KB.find(lookup)
        turn.kb_hits = [f"{sec.doc} — {sec.heading}" for _, sec in hits]
        if hits:
            convo = convo + [{"role": "system", "content": policy_prompt(hits)}]
            await chan.event({"type": "knowledge", "turn_id": turn_id,
                              "sections": turn.kb_hits, "t": turn.now()})
        else:
            convo = convo + [{"role": "system", "content": NO_POLICY}]

        calls = []
        async for kind, payload in stream_tools(
            llm, convo, model=llm_model,
            tools=SEARCH_TOOL if can_search else None,
            stats=turn.server,
        ):
            if kind == "text":
                await mark_first()
                await emit(payload)
            elif kind == "tool_start":
                await mark_first()
            elif kind == "tool":
                calls.append(payload)

        if calls:
            try:
                query = (json.loads(calls[0]["arguments"]) or {}).get("query", "").strip()
            except ValueError:
                query = ""
            if query:
                await chan.event({"type": "searching", "turn_id": turn_id,
                                  "query": query, "t": turn.now()})

                # Start talking before the search returns. This text goes into
                # the same synthesis context as the answer that follows it.
                # A speculative look leaves `attachments` empty while still
                # having spoken "let me take a look at that", which stacked
                # three holding phrases in a row.
                spoken = bridge_phrase(query, already_spoke=bool(attachments or prelook),
                                       code=speaking["code"])
                await emit(spoken)

                t_search = turn.now()
                results = await search(query)
                turn.search_s = turn.now() - t_search
                await chan.event({
                    "type": "sources", "turn_id": turn_id, "t": turn.now(),
                    "took_s": turn.search_s,
                    "results": [
                        {"title": r["title"], "url": r["url"]}
                        for r in results.get("results", [])
                    ],
                })

                grounded = convo + [
                    {"role": "assistant", "content": spoken},
                    {"role": "system", "content": grounded_prompt(results)},
                ]
                async for kind, payload in stream_tools(llm, grounded, model=llm_model):
                    if kind == "text":
                        await emit(payload)

        turn.last_token = turn.now()
        await chan.event({"type": "text_done", "turn_id": turn_id, "t": turn.last_token})
        await pending_text.put(None)

    async def synthesize():
        if conn is None:
            while await pending_text.get() is not None:
                pass                      # consume so generate() is never blocked
            return
        async with TTS_SLOTS:
            turn.tts_start = turn.now()
            await chan.event({"type": "tts_start", "turn_id": turn_id, "t": turn.tts_start})

            ctx = conn.context(
                model_id=tts_model,
                voice={"mode": "id", "id": voice_id},
                output_format=output_format(),
                language=speaking["code"],
            )

            async def pump():
                while True:
                    delta = await pending_text.get()
                    if delta is None:
                        break
                    await ctx.push(delta)
                await ctx.no_more_inputs()

            async def drain():
                async for res in ctx.receive():
                    if res.type == "chunk" and res.audio:
                        if turn.first_audio is None:
                            turn.first_audio = turn.now()
                            await chan.event({
                                "type": "first_audio", "turn_id": turn_id,
                                "t": turn.first_audio, "sample_rate": SAMPLE_RATE,
                            })
                        turn.audio_bytes += len(res.audio)
                        await chan.audio(turn_id, res.audio)
                    elif res.type == "error":
                        raise RuntimeError(res.message or res.title or "Cartesia error")
                turn.last_audio = turn.now()

            await asyncio.gather(pump(), drain())

    await asyncio.gather(generate(), synthesize())

    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": reply})
    del history[:-MAX_HISTORY]

    await chan.event({
        "type": "done", "turn_id": turn_id, "reply": reply, "metrics": turn.metrics(),
    })


async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    llm = get_llm()
    tts = get_tts()
    history = []
    chan = Channel(ws)
    running = {}       # turn_id -> task
    next_turn_id = 1
    # Looks started while the caller was still talking, keyed by the id the
    # browser gave them.
    prelooks = {}
    recent_fillers = []   # so the same acknowledgement is not used twice running
    opened_at = time.perf_counter()
    speaking = {"code": "en"}   # the language this call is being held in

    # Losing synthesis should cost the demo its voice, not its existence. The
    # conversation, vision and search all still work; the caller reads instead
    # of listening.
    t_setup = time.perf_counter()
    manager = tts.tts.websocket_connect()
    conn = None
    audio_trouble = None
    try:
        conn = await manager.__aenter__()
    except Exception as exc:
        log_error(exc, "no synthesis this session")
        audio_trouble = safe_error(exc, "speech")
    setup_ms = (time.perf_counter() - t_setup) * 1000

    await ws.send_json(
        {
            "type": "ready",
            "socket_setup_ms": setup_ms,
            "llm_model": get_model(),
            "tts_model": os.getenv("CARTESIA_MODEL", "sonic-2"),
            "sample_rate": SAMPLE_RATE,
            "models": CATALOG["models"],
            "voices": CATALOG["voices"],
            "default_voice": default_voice(),
            "max_concurrent": MAX_CONCURRENT,
            "max_turns": MAX_TURNS,
            "max_minutes": MAX_MINUTES,
            "summary_model": SUMMARY_MODEL,
            "search_provider": search_provider(),
            "vision_model": get_vision_model(),
            "max_frames": MAX_FRAMES,
            "audio": conn is not None,
            "audio_trouble": (
                f"{audio_trouble} Replies are text only until then."
                if audio_trouble else None
            ),
        }
    )

    async def supervise(turn_id, coro):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(exc, f"turn {turn_id}")
            await chan.event({"type": "error", "turn_id": turn_id,
                              "message": safe_error(exc, "that request")})
        finally:
            running.pop(turn_id, None)
            await chan.event({"type": "idle", "turn_id": turn_id, "running": len(running)})

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue

            kind = data.get("type")

            if kind == "prelook":
                # The camera already shows whatever they are holding up, so
                # start reading it now rather than when they stop speaking.
                pid = data.get("id")
                frames = clean_attachments(data.get("images"))
                if pid is None or not frames:
                    continue
                for old in [k for k in prelooks if k < pid - 2]:
                    task = prelooks.pop(old)["task"]
                    task.cancel()
                prelooks[pid] = {
                    "count": len(frames),
                    "task": asyncio.ensure_future(
                        describe_images(llm, frames, GENERIC_LOOK)),
                }
                continue

            if kind == "prompt":
                prompt = (data.get("text") or "").strip()
                if not prompt:
                    continue

                spent = len(history) // 2
                minutes = (time.perf_counter() - opened_at) / 60
                if spent >= MAX_TURNS or minutes >= MAX_MINUTES:
                    await chan.event({
                        "type": "spent",
                        "message": (
                            f"This session has reached its limit of {MAX_TURNS} "
                            f"questions." if spent >= MAX_TURNS else
                            f"This session has been open {MAX_MINUTES} minutes."
                        ) + " Reload the page to start a new one.",
                    })
                    continue
                if len(running) >= MAX_CONCURRENT:
                    await chan.event({
                        "type": "rejected",
                        "message": f"Already working on {MAX_CONCURRENT} requests.",
                    })
                    continue

                turn_id = next_turn_id
                next_turn_id += 1
                coro = run_turn(
                    chan, conn, llm, history, turn_id, prompt,
                    model=data.get("model"),
                    voice=data.get("voice_id"),
                    can_search=bool(data.get("search", True)),
                    attachments=clean_attachments(data.get("images")),
                    on_call=bool(data.get("call")),
                    recent_fillers=recent_fillers,
                    speaking=speaking,
                    prelook=prelooks.pop(data["prelook"], None)
                    if data.get("prelook") in prelooks else None,
                )
                running[turn_id] = asyncio.ensure_future(supervise(turn_id, coro))

            elif kind == "wrapup":
                try:
                    note = await write_up(llm, history)
                except Exception as exc:
                    log_error(exc, "call write-up")
                    note = None
                await chan.event({"type": "wrapup", "note": note})
                continue

            elif kind == "say":
                if conn is None:
                    continue
                # The assistant's own "which would you like first?" question.
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                try:
                    await chan.event({"type": "say_start", "turn_id": 0})
                    await synthesise(chan, conn, 0, text, pick_voice(data.get("voice_id")))
                    await chan.event({"type": "say_done", "turn_id": 0})
                except Exception as exc:
                    log_error(exc, "spoken prompt")
                    await chan.event({"type": "error", "turn_id": 0,
                                      "message": safe_error(exc, "speaking")})
    finally:
        for entry in prelooks.values():
            entry["task"].cancel()
        for task in list(running.values()):
            task.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
        if conn is not None:
            try:
                await manager.__aexit__(None, None, None)
            except Exception:
                pass
        await tts.close()

    return ws


async def index(request):
    return web.FileResponse(os.path.join(STATIC, "index.html"))


# ------------------------------------------------------------------ access

def is_https(request):
    """True when the visitor reached us over TLS, including via a tunnel."""
    return request.headers.get("X-Forwarded-Proto", request.scheme) == "https"


def authorised(request):
    token = request.cookies.get(COOKIE_NAME)
    return bool(token) and token in SESSIONS


@web.middleware
async def fresh_static(request, handler):
    """Never let the browser cache the app's own JS and CSS.

    aiohttp's static handler sends no cache headers, so browsers hold on to
    them — which during development means testing a fix against the previous
    version of the file and concluding it did not work.
    """
    response = await handler(request)
    if request.path.startswith("/static") or request.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@web.middleware
async def gate(request, handler):
    # /static holds no secrets and the login page needs the stylesheet.
    if request.path.startswith("/static") or request.path == "/login":
        return await handler(request)

    if authorised(request):
        return await handler(request)

    if request.path == "/ws":
        return web.Response(status=401, text="unauthorized")

    raise web.HTTPFound("/login")


async def login(request):
    if request.method == "GET":
        return web.FileResponse(os.path.join(STATIC, "login.html"))

    data = await request.post()
    given = (data.get("passphrase") or "").strip()

    # Constant-time compare so the response time leaks nothing about the value.
    if not hmac.compare_digest(given, PASSPHRASE):
        raise web.HTTPFound("/login?bad=1")

    token = secrets.token_urlsafe(32)
    SESSIONS.add(token)

    response = web.HTTPFound("/")
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite="Lax",
        secure=is_https(request),
        max_age=60 * 60 * 8,
    )
    raise response


async def on_startup(app):
    global TTS_SLOTS
    TTS_SLOTS = asyncio.Semaphore(TTS_CONCURRENCY)

    llm = get_llm()
    tts = get_tts()
    try:
        await load_catalog(llm, tts)
    finally:
        await tts.close()
    print(f"  {len(CATALOG['models'])} models · {len(CATALOG['voices'])} voices available")

    # M3 reads one image well but mis-reports a batch — given three solid
    # colours it answered "Black, Gray, White" — so video frames silently
    # degrade if someone points the vision model at it.
    if "minimax" in get_vision_model().lower():
        print(f"  ! {get_vision_model()} is unreliable with several images at once;"
              " video frames will suffer. gemma-4-31B-it is the safe choice.")
    print(f"  up to {MAX_CONCURRENT} requests in flight, {TTS_CONCURRENCY} synthesising at once")
    print(f"  {len(KB)} knowledge sections loaded"
          + ("" if len(KB) else " — answers about policy will be refused, not invented"))


def main():
    for key in ("SAMBANOVA_API_KEY", "CARTESIA_API_KEY"):
        if not os.getenv(key):
            raise SystemExit(f"{key} is missing from .env")

    app = web.Application(middlewares=[fresh_static, gate])
    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_post("/login", login)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC)
    app.on_startup.append(on_startup)

    port = int(os.getenv("PORT", "8080"))
    print(f"\n  listening on http://localhost:{port}")
    print(f"  passphrase: {PASSPHRASE}")
    if GENERATED_PASSPHRASE:
        print("  (generated for this run — set DEMO_PASSPHRASE in .env to fix it)")
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
