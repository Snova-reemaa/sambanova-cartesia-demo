"""Live SambaNova -> Cartesia voice demo.

Serves static/ and a websocket at /ws. One Cartesia socket is opened per browser
session and kept warm across turns, so "time to first sound" reflects what a real
user would feel rather than including a cold handshake.

    python server.py            # then open http://localhost:8080
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import random
import re
import secrets
import time

import aiohttp
from aiohttp import web, WSMsgType

# Named differently across aiohttp versions, and absent from some, so the
# write guard below builds its tuple from whatever this one actually has.
_GONE = [ConnectionResetError, ConnectionError, RuntimeError,
         asyncio.InvalidStateError]
for _name in ("ClientConnectionResetError", "ClientConnectionError",
              "ServerDisconnectedError"):
    _exc = getattr(__import__("aiohttp"), _name, None)
    if isinstance(_exc, type) and issubclass(_exc, BaseException):
        _GONE.append(_exc)
SOCKET_GONE = tuple(_GONE)
from dotenv import load_dotenv

load_dotenv()

from sambanova import (get_client as get_llm, get_model, stream_tools, stream_chat,
                       describe_images, get_vision_model)
from cartesia_tts import get_client as get_tts, output_format
from search import search, as_context, provider as search_provider
from knowledge_base import Knowledge, as_context as kb_context
import languages
import heard
import memory
import orders
import policies
import delivery
import pricing
import slots
import smarts
import voices

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

# Renaming this invalidates sessions issued under the old name: anyone
# holding one is sent back to the passphrase, which is the whole cost.
COOKIE_NAME = "voxnova_session"
# token -> when it stops being valid. A set never forgot anything, so a pod
# that stays up for weeks accumulates one entry per sign-in for ever, and a
# cookie that had expired in the browser was still honoured if anyone kept a
# copy of it. One lifetime governs both the cookie and the server's memory of
# it, so they cannot drift apart.
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
SESSIONS = {}

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

ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_order",
        "description": (
            "Look up a customer's order by its number. Returns the real status, "
            "items, dates, carrier, tracking and how many working days late it "
            "is. Call this whenever an order number is mentioned, or whenever "
            "you are about to say you will check on an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {"order_number": {"type": "string"}},
            "required": ["order_number"],
        },
    },
}

ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "resolve_order",
        "description": (
            "Carry out what you and the caller have agreed for an order: send a "
            "replacement, issue a refund, or email a returns label. Call it only "
            "after the caller has said yes. Never say it is done unless this "
            "returns a reference."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_number": {"type": "string"},
                "action": {"type": "string", "enum": ["replacement", "refund", "return_label"]},
                "reason": {"type": "string"},
            },
            "required": ["order_number", "action"],
        },
    },
}

TRANSFER_TOOL = {
    "type": "function",
    "function": {
        "name": "transfer_to_specialist",
        "description": (
            "Hand the call to a colleague who can do something you cannot. "
            "'billing' for a dispute about money already taken: a double "
            "charge, a payment they do not recognise, a refund that was agreed "
            "and has not arrived. 'technical' for working through a device that "
            "is not functioning, step by step with the caller. "
            "Never transfer a question. Policy, prices, opening hours, "
            "delivery, returns, warranty cover and what the company does or "
            "does not offer are all yours to answer — and where you do not know "
            "the answer, say you will find out. Passing the call on does not "
            "make the answer appear, it just makes the caller explain "
            "themselves twice.\n"
            "The line that catches people out is returns. Whether an item can "
            "go back, how long the window is, who pays the postage, how long a "
            "refund takes once it is sent — all policy, all yours. It only "
            "becomes billing's when money has actually gone wrong: a refund "
            "that was agreed and never arrived, a charge they did not make."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "enum": ["billing", "technical"]},
                "reason": {"type": "string"},
            },
            "required": ["team"],
        },
    },
}

ORDER_RULES = (
    "\nYou can look up orders. When a caller gives an order number, or asks "
    "where their order is, call lookup_order — never say you are checking "
    "unless you have. If you do not have a number yet, ask for it.\n"
    "Only ask when the answer actually depends on which order it is. A "
    "question about the rules — how long the return window is, what the "
    "warranty covers, when you are open — has the same answer for everybody, "
    "so answer it first. Asking for an order number instead reads as a "
    "brush-off: \"I opened the box 20 days ago, full refund right?\" was met "
    "with a request for the order number when the window alone settles it. "
    "Answer, then ask for the number if there is something to look up.\n"
    "Everything the lookup returns is true; everything it does not return, you "
    "do not know. Do not invent a delivery date, a courier, or a refund that is "
    "not in the result.\n"
    "When the lookup says an order counts as late or lost, lead with that and "
    "offer what the caller is owed — a replacement or a full refund for a lost "
    "one. \"A bit delayed\" for something eleven working days overdue is the "
    "sort of thing that makes a caller ask for a manager."
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
    "ar": ("تمام، لحظة واحدة. ", "أكيد، خلّيني أشوف. ", "حسنًا، ثانية واحدة. "),
}

LOOKING_PHRASE = {
    "en": ("Let me take a look at that. ", "Let me look through those. "),
    "es": ("Déjame echar un vistazo. ", "Déjame revisarlas. "),
    "fr": ("Laissez-moi regarder ça. ", "Laissez-moi les regarder. "),
    "de": ("Lassen Sie mich das ansehen. ", "Lassen Sie mich die ansehen. "),
    "it": ("Fammi dare un'occhiata. ", "Fammi guardare queste. "),
    "pt": ("Deixe-me dar uma olhada. ", "Deixe-me ver essas. "),
    "nl": ("Laat me daar even naar kijken. ", "Laat me die even bekijken. "),
    "ar": ("خلّيني أشوف هذا. ", "خلّيني أطّلع عليها. "),
}

SEARCHING_PHRASE = {
    "en": ("One moment. Let me look that up for you. ", "Right, let me find some options. "),
    "es": ("Un momento, voy a buscarlo. ", "Vale, déjame buscar opciones. "),
    "fr": ("Un instant, je cherche ça. ", "Bien, je vais trouver des options. "),
    "de": ("Einen Moment, ich schaue nach. ", "Gut, ich suche ein paar Optionen. "),
    "it": ("Un momento, lo cerco subito. ", "Bene, cerco qualche opzione. "),
    "pt": ("Um momento, vou procurar. ", "Certo, vou encontrar opções. "),
    "nl": ("Een moment, ik zoek het op. ", "Goed, ik zoek wat opties. "),
    "ar": ("لحظة، خلّيني أبحث عن هذا. ", "تمام، خلّيني ألقى لك خيارات. "),
}


# Said once, at the top of the first call, before anything else. Short
# enough not to be a speed bump and plain enough to be understood at speed:
# what it is, and that the call is kept. It rides in front of the first
# answer rather than being a turn of its own, so it costs the caller a
# sentence rather than an exchange.
DISCLOSURE = {
    "en": "Just so you know, I'm an AI assistant, and this call may be saved "
          "so I can remember you next time. ",
    "es": "Para que lo sepas, soy un asistente de inteligencia artificial, y "
          "esta llamada puede guardarse para recordarte la próxima vez. ",
    "fr": "Pour information, je suis un assistant IA, et cet appel peut être "
          "enregistré pour que je me souvienne de vous la prochaine fois. ",
    "de": "Nur damit Sie es wissen: Ich bin ein KI-Assistent, und dieses "
          "Gespräch wird möglicherweise gespeichert, damit ich mich beim "
          "nächsten Mal an Sie erinnere. ",
    "it": "Per tua informazione, sono un assistente di intelligenza "
          "artificiale, e questa chiamata può essere salvata per ricordarti "
          "la prossima volta. ",
    "pt": "Só para saber, sou um assistente de inteligência artificial, e "
          "esta chamada pode ser guardada para me lembrar de si da próxima "
          "vez. ",
    "nl": "Even ter info: ik ben een AI-assistent, en dit gesprek kan worden "
          "bewaard zodat ik u de volgende keer herken. ",
    "ar": "بس حاب أخبرك، أنا مساعد ذكاء اصطناعي، ويمكن تنحفظ هذي المكالمة "
          "عشان أتذكرك المرة الجاية. ",
}


def disclosure(code):
    return DISCLOSURE.get(code, DISCLOSURE["en"])


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

# Named, because "the AI service is out of credit" sent someone to check the
# wrong account: two providers are in play and only one of them had run out.
SAYS = (
    "The {what} service rejected our credentials. Check the API key in .env.",
    "The {what} service is out of credit. Top up that account to carry on.",
    "The {what} service is busy right now. Try that again in a moment.",
    "That took too long and timed out. Try again.",
)

WHICH = {"speech": "speech", "speaking": "speech"}


def safe_error(exc, where="that"):
    """A sentence for the browser, with the provider's wording left behind."""
    text = str(exc).lower()
    for phrases, says in zip(TROUBLE, SAYS):
        if any(p in text for p in phrases):
            return says.format(what=WHICH.get(where, "AI"))
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


async def in_english(llm, text, code, meter=None):
    if code == "en":
        return text
    try:
        reply = await llm.chat.completions.create(
            model=TRANSLATOR, max_tokens=120,
            messages=[{"role": "user", "content":
                       "Translate this customer support question into English. "
                       "Reply with only the English translation, no preamble, no "
                       f"quotes.\n\n{text}"}])
        if meter is not None:
            meter.add_usage(TRANSLATOR, getattr(reply, "usage", None))
        out = (reply.choices[0].message.content or "").strip()
        return out or text
    except Exception as exc:
        log_error(exc, "translating for retrieval")
        return text



def language_rule(code):
    """Follow the caller. Switching language mid-call is the normal case in a
    contact centre, not an edge case."""
    name = languages.name(code)
    rule = (
        f"\nThe caller is speaking {name}. Reply in {name} and nothing else — "
        "not a translation, not a mixture. If they switch language again, "
        "switch with them."
    )
    if code == "en":
        return rule
    # Llama drops out of the language mid-sentence, and worse, mid-word:
    # "حالة الشipment" fused an Arabic definite article to an English noun.
    # Every word here is spoken aloud, so a stray script is not a cosmetic
    # blemish — Cartesia reads it with the wrong phonology and it comes out
    # as noise.
    #
    # An earlier version of this rule listed the words to avoid by name —
    # "shipment, billing, tracking, refund, order, delivery" — and those six
    # were precisely the ones that leaked, so naming them looked like the
    # cause. Removing them changed nothing measurable, which is the useful
    # finding: "shipment" appears nowhere in the context, so the model is not
    # copying it. The list is gone anyway, because a prohibition that names
    # its own counter-examples is a bad bet, but the leak it was aimed at is
    # handled by NON_ENGLISH_TEMPERATURE below, not by this paragraph.
    rule += (
        f"\nWrite every single word in {name}, in {name} script — including any "
        "term you would reach for in English out of habit. Never start a word "
        "in one script and finish it in another, and never use Chinese, "
        f"Japanese or Korean characters. If the {name} word will not come, "
        f"describe the thing in {name} rather than borrowing from another "
        "language."
    )
    # The words this job keeps needing, so they are in reach at the moment
    # the model would otherwise fall back on the English. A Dutch reply said
    # "refund" where it should have said terugbetaling; the word it needed
    # was never in front of it.
    words = languages.glossary(code)
    if words:
        rule += f"\nThe {name} words for this work: {words}."
    return rule


# English turns are left on the provider's own default: the variety is what
# keeps the agent from sounding like a script, and nothing is at risk if it
# picks a different synonym. A non-English turn is a different trade. Llama
# 3.3 answering in Arabic produced "حالة الشipment" — an Arabic definite
# article and an English noun fused into one word — and no amount of
# instruction stopped it, because it is a sampling artefact rather than a
# misunderstanding: the model starts a word in one script and the next token
# it draws is from the other. Narrowing the distribution is the lever that
# actually reaches it.
# Everything except the speaking: the conversation, retrieval, vision and
# search all run and the caller reads the reply. Useful for exercising the
# agent without spending synthesis credits, and as a way to keep a demo
# usable when there are none left.
TEXT_ONLY = os.getenv("TEXT_ONLY", "").lower() in ("1", "true", "yes")

NON_ENGLISH_TEMPERATURE = float(os.getenv("NON_ENGLISH_TEMPERATURE", "0.3"))
# Unset, so English keeps the provider's own default. Lowering it looked like
# the obvious cure for the agent transferring calls it should have answered,
# and it is not: over the 17-case routing set the provider default scored
# 16/17 twice and 0.6 scored 16/17 then 15/17. Sampling was not what made
# those decisions wrong, so there is nothing to buy by narrowing it, and
# narrowing it costs the variety that keeps the agent from sounding scripted.
# The knob stays because it is the first thing to reach for if a model change
# makes the agent erratic. (Non-English turns are a different trade — see
# NON_ENGLISH_TEMPERATURE above.)
TEMPERATURE = float(os.environ["TEMPERATURE"]) if os.getenv("TEMPERATURE") else None


def sampling_for(code):
    return TEMPERATURE if code == "en" else NON_ENGLISH_TEMPERATURE


def speak_in(code):
    """A one-line reminder, last in the stack, after the English instructions."""
    name = languages.name(code)
    return (f"Reminder: the caller is speaking {name}. Everything above is "
            f"written in English for you, but your reply is spoken aloud to "
            f"them — write it in {name}, every word.")


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
        "stated, say plainly that you do not have it and offer to put them "
        "through to the team who would — do not promise to find out, and do "
        "not reason from the nearest related policy to a plausible answer.\n"
        "Where a section names the specific thing they asked about, it beats the "
        "general rule — an exclusion list applies even when a broader policy "
        "would have allowed it. Quoting the general policy over a stated "
        "exclusion is the worst thing you can do here.\n"
        "If the policy turns on a condition they have not told you, ask about "
        "that condition rather than assuming the answer that suits them.\n"
        "Still speak naturally, two or three sentences; do not read the document "
        "aloud or cite it."
    )


# "I'll find out and come back to you" was a promise nobody kept: there is
# no queue, no ticket and no callback, so the caller waited for something
# that was never going to arrive. Saying plainly that we do not have it, and
# offering the colleague who would, is both true and more use.
NO_POLICY = (
    "\nNothing in the company documentation covers what they just asked. Do "
    "not invent an answer about company policy, hours, prices, delivery or "
    "warranty.\n"
    "Say plainly that you do not have that information — not that you will "
    "find out, not that you will come back to them, because nobody will. "
    "Then offer to put them through to the team who would know, and transfer "
    "them if they say yes. Ordinary conversation and general knowledge are "
    "still fine."
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
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else len(text) // 4,
        "turns": len([m for m in history if m["role"] == "user"]),
    }


GENERIC_LOOK = (
    "Say what you see, concretely: the object, its colour, material and style, "
    "any damage, text, error message, lights or markings. One or two plain "
    "sentences, no headings. Say so if you cannot tell."
)

MAX_FRAMES = 8        # gemma read 6 frames in order reliably; 8 leaves headroom
# Per frame, after the browser has resized it. Screen frames are sent larger
# than camera frames — small text in a dialog has to survive the trip.
#
# Eight of these has to fit through the websocket, and at 2.5MB it did not:
# eight times that is 20MB against a 16MB frame limit, so a full batch was
# impossible on paper. Unreachable in practice — every image goes through
# the browser's resize first, and eight screen frames of pure noise, which
# is the worst case JPEG has, measured 8.78MB — but two numbers that
# contradict each other will eventually be believed by somebody. At 2MB the
# arithmetic works out exactly to the frame limit, and it is still most of
# an order of magnitude above anything measured.
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", "2000000"))

SCREEN_LOOK = (
    "This is the caller's screen. Say which app or site it is, quote any dialog, "
    "error or warning word for word, and say which buttons and menus are visible "
    "and where. Two or three plain sentences, no headings. Say so if you cannot read it."
)

SCREEN_PROMPT = (
    "The caller is sharing a window of their screen with you, and you are told "
    "what is on it when they ask about it. Guide them like someone sitting next "
    "to them: one step at a time, naming the exact button or menu and where it is "
    "on screen. Never claim you cannot see their screen."
)


def looks_like_image(url):
    return isinstance(url, str) and url.startswith("data:image/")


def clean_attachments(raw):
    """Keep only well-formed, reasonably sized image data URLs."""
    kept, _ = inspect_attachments(raw)
    return kept


def inspect_attachments(raw):
    """Returns (kept, what was dropped and why).

    Dropping was silent: an image too large or not an image at all simply
    was not there, the agent answered about the ones that survived, and the
    caller had no way to know the photo they were asking about never
    arrived.
    """
    if not isinstance(raw, list):
        return [], ["the attachments were not a list"] if raw else []
    kept, dropped = [], []
    for url in raw[:MAX_FRAMES]:
        if not looks_like_image(url):
            dropped.append("one file was not a readable image")
        elif len(url) > MAX_IMAGE_BYTES:
            dropped.append(f"one image was too large "
                           f"({len(url) // 1_000_000}MB, the limit is "
                           f"{MAX_IMAGE_BYTES // 1_000_000}MB)")
        else:
            kept.append(url)
    if len(raw) > MAX_FRAMES:
        dropped.append(f"only the first {MAX_FRAMES} of {len(raw)} were used")
    return kept, dropped


def vision_question(prompt, count, screen=False):
    """Ask for observations, not a report — and keep the asking short.

    The long version of this prompt cost 413 prompt tokens and returned nine:
    "I see a pair of black oval glasses." The short version costs 312 and
    returns more detail. Instructions are prompt tokens too, and the model was
    spending its attention on the brief rather than the picture.
    """
    if screen:
        return SCREEN_LOOK + f' Caller said: "{prompt}"'
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


def seen_prompt(description, count, screen=False):
    if screen:
        return (
            "You just looked at the caller's screen. This is what is on it:\n\n"
            f"{description}\n\n"
            "Help with what they actually asked. Give the next step only, naming the "
            "exact button, menu or field and where it is on screen. Do not describe "
            "the screen back to them, and never mention screenshots or image analysis."
        )
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
# Messages retained for context. 8 was four exchanges — on a support call that
# is short enough to forget the order number the caller gave a minute ago, and
# SambaNova is fast enough that a longer history costs nothing audible.
MAX_HISTORY = int(os.getenv("MAX_HISTORY_MESSAGES", "30"))

# sonic-2 has no Arabic, and as of October 2026 it no longer has Italian or
# Dutch either: Cartesia answers those two with "the requested model has been
# sunsetted", which killed the turn outright. sonic-3 speaks all eight and is
# no slower — measured over three runs it reached first audio in 1223 ms
# against sonic-2's 1314 ms — so it is the default now, and CARTESIA_MODEL
# only comes into it if someone sets it.
FALLBACK_TTS_MODEL = os.getenv("CARTESIA_MODEL_AR", "sonic-3")
TTS_MODEL_BY_LANGUAGE = {}

# A language Cartesia has stopped serving on the configured model, learned at
# the point it fails rather than from a table that has to be kept in step with
# their release notes. One turn degrades; the rest of the call does not.
DROPPED = set()


class NothingToSay(Exception):
    """The turn produced no text at all, so there is nothing to synthesise."""


def out_of_credit(exc):
    """Cartesia says this in prose rather than with a code of its own."""
    text = str(exc).lower()
    return any(s in text for s in ("ran out of credits", "credit limit",
                                   "upgrade your subscription", "balance_units"))


def note_tts_failure(code, exc):
    """Was that failure the model refusing this language, or something else?"""
    if "sunset" not in str(exc).lower() or code in DROPPED:
        return False
    was = tts_model_for(code)
    DROPPED.add(code)
    print(f"  ! {was} no longer speaks {languages.name(code)} — "
          f"using {FALLBACK_TTS_MODEL} for it from here on")
    return True


def tts_model_for(code):
    if code in DROPPED:
        return FALLBACK_TTS_MODEL
    return (TTS_MODEL_BY_LANGUAGE.get(code) or os.getenv("CARTESIA_MODEL")
            or FALLBACK_TTS_MODEL)


def heard_part(reply, heard_chars):
    """What the caller actually heard of a reply they talked over.

    Recording the whole reply told the model it had said things nobody heard,
    so the next answer built on them. The browser estimates how far playback
    got; the note makes the interruption itself part of the context.
    """
    if heard_chars is None:
        return reply
    cut = reply[: max(0, int(heard_chars))].rstrip()
    if not cut:
        return "[The caller interrupted before you said anything.]"
    return cut + " — [the caller cut you off here]"
VOICE_LIMIT = 20  # how many Cartesia voices to offer in the picker
MAX_CONCURRENT = 4  # requests accepted in flight at once on one connection

# One passphrase shared on a call, and a link that outlives it, is somebody
# else spending your credits. These are per browser session.
# What one websocket message may weigh, enforced by aiohttp before anything
# is parsed, and what one spoken turn may say. Nobody dictates two thousand
# characters; anything longer is a stuck recogniser or someone pasting a
# book, and either way it is a prompt bill nobody meant to run up.
MAX_MSG_BYTES = int(os.getenv("MAX_MESSAGE_BYTES",
                              str(MAX_FRAMES * MAX_IMAGE_BYTES)))
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "2000"))

MAX_TURNS = int(os.getenv("MAX_TURNS_PER_SESSION", "60"))
MAX_MINUTES = int(os.getenv("MAX_SESSION_MINUTES", "45"))

# Cartesia caps concurrent synthesis per subscription (2 on the current plan);
# exceeding it fails the whole context rather than queueing. SambaNova has no
# such limit at this volume, so language-model calls all start immediately and
# only synthesis waits for a slot.
TTS_CONCURRENCY = int(os.getenv("CARTESIA_CONCURRENCY", "2"))
TTS_SLOTS = None  # asyncio.Semaphore, created once the loop exists

# Turns allowed to be talking to SambaNova at once, across the whole process
# rather than per connection. MAX_CONCURRENT caps one caller at four; twenty
# callers at four each is eighty, which is how a rate limit is met. A turn
# that cannot get a slot waits briefly — most waits are a second or two,
# because turns are short — and if the queue has not moved by then the
# caller is told, which is the one thing that must not be a silent hang.
MAX_LLM_AT_ONCE = int(os.getenv("MAX_CONCURRENT_LLM", "8"))
LLM_QUEUE_SECONDS = float(os.getenv("LLM_QUEUE_SECONDS", "6"))
LLM_SLOTS = None  # asyncio.Semaphore, created once the loop exists

BUSY_SAYS = {
    "en": "Sorry — a lot of people are asking at once. Give me a moment and ask again.",
    "es": "Disculpa, hay mucha gente preguntando a la vez. Dame un momento e inténtalo otra vez.",
    "fr": "Désolé, beaucoup de monde pose des questions en même temps. Un instant, puis réessayez.",
    "de": "Entschuldigung, gerade fragen sehr viele gleichzeitig. Einen Moment, dann versuchen Sie es noch einmal.",
    "it": "Scusa, in questo momento ci sono molte richieste insieme. Dammi un attimo e riprova.",
    "pt": "Desculpe, há muita gente a perguntar ao mesmo tempo. Dê-me um momento e tente outra vez.",
    "nl": "Sorry, er zijn op dit moment veel vragen tegelijk. Een moment, probeer het dan opnieuw.",
    "ar": "آسف، في ناس كثير يسألون في نفس الوقت. أعطني لحظة وحاول مرة ثانية.",
}

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
        self.order = None        # order number looked up, if any
        self.route = None        # which model answered, and why
        self.draft_lead_s = None # how far ahead an early draft started
        self.think_s = None      # how long the reasoning model took
        self.checks = {"checked": 0, "held": 0, "slowest_s": 0.0}
        self.fused = 0           # words written in two scripts and dropped
        self.mood = "neutral"
        self.meter = pricing.Meter()   # what this turn cost
        self.cost_call = None          # running total for the call, set at the end

    def now(self):
        return time.perf_counter() - self.t0

    def metrics(self):
        audio_seconds = self.audio_bytes / (SAMPLE_RATE * 2)
        total = self.last_audio or self.now()

        # What the wire cost us: our stopwatch minus SambaNova's own. Meaningless
        # for a turn answered from an early draft, whose first token arrived
        # before the turn started.
        inference = self.server.get("time_to_first_token")
        overhead = None
        if inference is not None and self.first_token is not None and self.draft_lead_s is None:
            overhead = max(0.0, self.first_token - inference)

        return {
            "server": self.server or None,
            "inference_ttft_s": inference,
            "search_s": self.search_s,
            "vision_s": self.vision_s,
            "filler_s": self.filler_s,
            "kb_hits": self.kb_hits,
            "language": self.language,
            "order": self.order,
            "route": self.route,
            "draft_lead_s": self.draft_lead_s,
            "think_s": self.think_s,
            "checks": self.checks,
            "mood": self.mood,
            "cost": self.meter.summary(),
            "cost_call": self.cost_call,
            "network_overhead_s": overhead,
            "first_token_s": self.first_token,
            "last_token_s": self.last_token,
            "tts_start_s": self.tts_start,
            "first_audio_s": self.first_audio,
            "total_s": total,
            "audio_seconds": audio_seconds,
            "realtime_factor": (audio_seconds / total) if total else None,
        }


def pick_model(requested):
    """Only ever hand the API a model this account was told it has."""
    if requested and (not CATALOG["models"] or requested in CATALOG["models"]):
        return requested
    return get_model()


# What Cartesia says each voice's language is: the catalog, plus every
# voice in voices.py, looked up by id at startup.
VOICE_LANGUAGE = {}


def known_languages():
    """Our own table's voices are known without asking: each is configured
    for one language, and check_voices refuses to start if Cartesia says
    otherwise. Without this a picked native voice was ignored whenever
    Cartesia could not be reached at startup."""
    for (role, code), vid in voices.configured().items():
        VOICE_LANGUAGE.setdefault(vid, code)


known_languages()


def native_voices():
    """What the voice picker may offer: per language, the three people on
    the line, and nothing from the rest of the account. One language at a
    time is shown, so a Thai voice, or Skylar on an Arabic call, is never
    there to pick."""
    out = []
    for code in voices.LANGUAGES:
        seen = set()
        for role in voices.ROLES:
            vid = voices.for_role(role, code)
            if vid and vid not in seen:
                seen.add(vid)
                out.append({"id": vid, "name": voices.name_for(role, code),
                            "language": code, "role": role,
                            "describe": voices.DESCRIBED.get(vid, "")})
    return out


# Preview clips, one per voice, made the first time anyone asks and kept
# for the life of the pod: 24 short lines at most, ever, rather than one
# per click. The browser keeps its own copy as well.
SAMPLES = {}
_sample_locks = {}


async def voice_sample(request):
    """The sample line in one of the picker's voices, as WAV.

    Only the voices the picker offers, each in its own language: this is
    not a way to have anything else synthesised.
    """
    vid = request.query.get("id", "")
    code = {v["id"]: v["language"] for v in native_voices()}.get(vid)
    if not code:
        return web.Response(status=404, text="not a voice the picker offers")
    if TEXT_ONLY:
        return web.Response(status=503, text="speech is switched off")
    if vid not in SAMPLES:
        async with _sample_locks.setdefault(vid, asyncio.Lock()):
            if vid not in SAMPLES:
                tts = get_tts()
                try:
                    clip = await tts.tts.generate(
                        model_id=tts_model_for(code), transcript=voices.SAMPLE[code],
                        voice={"mode": "id", "id": vid}, language=code,
                        output_format={"container": "wav", "encoding": "pcm_s16le",
                                       "sample_rate": 24000})
                    SAMPLES[vid] = await clip.read()
                except Exception as exc:
                    log_error(exc, "voice preview")
                    return web.Response(status=503, text=safe_error(exc, "speech"))
                finally:
                    await tts.close()
    return web.Response(body=SAMPLES[vid], content_type="audio/wav",
                        headers={"Cache-Control": "private, max-age=86400"})


async def check_voices(tts):
    """Refuse to start if any configured voice is not in its language.

    A voice Cartesia does not know, or knows as another language, is a
    configuration mistake that would put a foreign voice on a call, so the
    pod stops and says which setting to change. Not being able to ask (no
    network, a placeholder key in the tests) is not the same thing: that
    is logged and the configured table, which is tested, stands.
    """
    import cartesia
    for v in CATALOG["voices"]:
        VOICE_LANGUAGE[v["id"]] = v.get("language") or ""
    wanted = sorted(set(voices.configured().values()))

    async def one(vid):
        try:
            return vid, (await tts.voices.get(vid)).language or ""
        except (cartesia.NotFoundError, cartesia.BadRequestError,
                cartesia.UnprocessableEntityError):
            return vid, None

    try:
        found = dict(await asyncio.gather(*(one(v) for v in wanted)))
    except Exception as exc:
        print(f"  ! could not check the voices against Cartesia "
              f"({type(exc).__name__}); using the configured table as tested")
        return
    VOICE_LANGUAGE.update({k: v for k, v in found.items() if v is not None})

    def language_of(vid):
        if found.get(vid) is None:
            raise LookupError(vid)
        return found[vid]

    problems = voices.check(language_of)
    if problems:
        for line in problems:
            print(f"  ! voice in the wrong language: {line}")
        raise SystemExit("refusing to start: every language must be spoken by "
                         "a native voice (see above)")
    print(f"  {len(wanted)} voices checked: every one is native to its language")


def voice_for(session, code, picked=None):
    """The voice for this sentence, or None to answer it in text.

    Worked out per sentence from the language being spoken now, so when the
    caller switches language the very next sentence switches voice too. A
    voice chosen in the picker is used only for the main agent and only in
    its own language. Nothing here falls back to another language's voice.
    """
    persona = getattr(session, "persona", "front")
    if (persona == "front" and picked
            and voices.same_language(VOICE_LANGUAGE.get(picked), code)):
        return picked
    return voices.for_role(persona, code)


class Channel:
    """Serialises writes to one browser socket.

    Turns run concurrently, so without this two tasks can interleave halves of
    a frame. Every audio frame is prefixed with its turn id, because binary
    frames carry no other way to say which answer they belong to.
    """

    # A browser tab being closed is not an error, and it is the commonest way
    # a call ends. Checking `closed` before taking the lock left a gap the
    # width of a lock acquisition: the socket could go while a write waited
    # its turn, and aiohttp raised "Cannot write to closing transport" out of
    # whatever happened to be speaking. Every write goes through here, the
    # check happens inside the lock, and a connection that has gone away ends
    # the writing quietly.
    def __init__(self, ws):
        self.ws = ws
        self.lock = asyncio.Lock()

    async def _send(self, write):
        """True if it went, False if the socket has gone."""
        if self.ws.closed:
            return False
        async with self.lock:
            if self.ws.closed:
                return False
            try:
                await write()
            except SOCKET_GONE:
                return False
        return True

    async def event(self, payload):
        return await self._send(lambda: self.ws.send_json(payload))

    async def audio(self, turn_id, pcm):
        return await self._send(
            lambda: self.ws.send_bytes(turn_id.to_bytes(2, "big") + pcm))


# ================================================================ session

# Cartesia's experimental voice controls (speed, emotion) are what make the
# voice itself change with the caller's mood. If the account's model rejects
# them, the first failure turns them off for the session and the turn is
# re-synthesised without — the caller hears the reply either way.
VOICE_CONTROLS = (os.getenv("CARTESIA_VOICE_CONTROLS") or "on").lower() not in ("0", "off", "false", "no")


class Session:
    """Everything one browser connection remembers between turns."""

    def __init__(self):
        self.began = time.time()         # wall clock, so memory can compare it with a forgetting
        self.history = []
        self.speaking = {"code": "en"}   # the language this call is being held in
        self.recent_fillers = []         # so the same acknowledgement is not used twice running
        self.mood = "neutral"
        self.controls_ok = VOICE_CONTROLS
        # Per language, because they do not all behave the same and one
        # refusal should not flatten the rest of the call.
        self.emotion_ok = {}
        # Questions this call could not answer from anything it had.
        self.unanswered = []
        # The AI-and-recording line, said once per call.
        self.disclosed = False
        # Session plus address, set when the socket opens. Every attempt
        # and request limit is counted against it.
        self.key = None
        # The code we are waiting to hear, if any.
        self.awaiting_code = False
        # True only on the turn the code checked out.
        self.just_verified = False
        # Set on the turn a record is deleted, so the answer says so.
        self.just_forgot = None
        self.audio_off = False   # synthesis gave out part way through the call
        self.just_named = False  # they said who they are and nothing else yet
        self.stated_name = None  # the name they gave, for a new caller's record
        self.signed_in = None    # the Auth0 email, when sign-in is on: their identity
        self.signed_in_forgotten = False  # they said "forget me" on this call
        self.caller = None               # who is on the line, once an order is looked up
        self.said = {}                   # turn_id -> (assistant message in history, full reply)
        self.turns_used = 0              # counted, not derived: history is trimmed
        self.user_turns = 0              # agent turns that reached history
        self.saved_turns = 0             # of those, how many are already in memory
        self.drafts = {}                 # draft id -> Draft
        self.persona = "front"           # who is speaking: front, billing or technical
        self.handoff = None              # set when a transfer is asked for mid-turn
        self.actions = []                # what has actually been done on this call
        self.cost = pricing.Meter()      # running total for the call


PERSONAS = {
    "billing": {"name": "Maya", "team": "billing",
                "rule": ("You are Maya from the billing team: refunds, charges and payments. "
                         "You have taken this call over from a colleague and can see "
                         "everything said so far. You can resolve orders.")},
    "technical": {"name": "Omar", "team": "technical support",
                  "rule": ("You are Omar from technical support: devices, setup and errors on "
                           "screen. You have taken this call over from a colleague and can see "
                           "everything said so far. Walk the caller through one step at a time.")},
}

# This is spoken, not generated, so it is the one line in the call that cannot
# drift — and before these existed a Spanish caller heard "Let me bring in Omar
# from our technical support team" in the middle of their own language.
HANDOFF_PHRASE = {
    "en": "Let me bring in {name} from our {team} team. They'll have everything we've talked about. ",
    "es": "Le paso con {name}, de nuestro equipo de {team}. Tiene todo lo que hemos hablado. ",
    "fr": "Je vous mets en relation avec {name}, de notre équipe {team}, qui a tout l'historique de notre conversation. ",
    "de": "Ich verbinde Sie mit {name} aus unserem Team für {team}. Dort liegt alles vor, worüber wir gesprochen haben. ",
    "it": "Le passo {name}, del nostro team di {team}. Ha già tutto quello di cui abbiamo parlato. ",
    "pt": "Vou passar para {name}, da nossa equipa de {team}. Tem tudo o que falámos. ",
    "nl": "Ik verbind u door met {name} van ons team {team}. Die heeft alles wat we besproken hebben. ",
    "ar": "خلّيني أحوّلك على {name} من فريق {team}، وعنده كل اللي تكلمنا فيه. ",
}

# The team name is inside that sentence, so translating the sentence and
# leaving "technical support" in it would not have fixed anything.
TEAM_NAME = {
    "en": {"billing": "billing", "technical": "technical support"},
    "es": {"billing": "facturación", "technical": "soporte técnico"},
    "fr": {"billing": "facturation", "technical": "support technique"},
    "de": {"billing": "Rechnungsfragen", "technical": "technischen Support"},
    "it": {"billing": "fatturazione", "technical": "assistenza tecnica"},
    "pt": {"billing": "faturação", "technical": "apoio técnico"},
    "nl": {"billing": "facturatie", "technical": "technische ondersteuning"},
    "ar": {"billing": "الفواتير", "technical": "الدعم الفني"},
}


def handoff_line(code, team):
    """What the caller hears as the call changes hands, in their language."""
    phrase = HANDOFF_PHRASE.get(code, HANDOFF_PHRASE["en"])
    names = TEAM_NAME.get(code, TEAM_NAME["en"])
    return phrase.format(name=PERSONAS[team]["name"],
                         team=names.get(team, PERSONAS[team]["team"]))

INTRO_RULE = (
    "You have just taken over this call. Introduce yourself in one short sentence "
    "with your first name and team, show in a few words that you know what the "
    "caller needs, then take the next step or ask the one question you need. Do "
    "not repeat what your colleague already said."
)


def norm(text):
    """Two transcripts of the same sentence should compare equal."""
    return re.sub(r"[^\w\s]", "", (text or "").lower()).split()


def read_opts(data):
    kept_images, dropped_images = inspect_attachments(data.get("images"))
    requested = data.get("model")
    auto = requested in (None, "", "auto")
    return {
        "model": pick_model(None if auto else requested),
        "auto": auto,
        "voice": data.get("voice_id"),
        "can_search": bool(data.get("search", True)),
        "attachments": kept_images,
        "dropped_images": dropped_images,
        "on_call": bool(data.get("call")),
        "language": data.get("language"),
        "check": bool(data.get("check", True)),
        "mode": "interpret" if data.get("mode") == "interpret" else "agent",
        "pair": data.get("pair") if data.get("pair") in languages.SUPPORTED else "ar",
        "source": "screen" if data.get("source") == "screen" else "camera",
    }


def opts_key(opts):
    """Settings that change what the model is asked, so a draft made under
    different ones cannot stand in for the real turn."""
    return (opts["model"], opts["auto"], opts["can_search"], opts["on_call"],
            opts["language"], opts["mode"], opts["source"])


def resolve_language(session, prompt, forced):
    # A language the caller picked wins. Detection only ever sees what the
    # browser transcribed, and an en-US recogniser turns French into English
    # nonsense, so for speech the picker is the only reliable signal.
    if forced in languages.SUPPORTED:
        return forced
    return languages.detect(prompt, session.speaking["code"])


# ================================================================ memory

def said_text(data, field="text"):
    """What the caller said, as a string of a sane length.

    The field arrives from a browser and need not be a string at all, and
    nothing downstream expected to be handed a number.
    """
    raw = data.get(field)
    if not isinstance(raw, str):
        if raw is None or isinstance(raw, (dict, list, bool)):
            return ""
        raw = str(raw)
    text = raw.strip()
    if len(text) > MAX_PROMPT_CHARS:
        print(f"  ~ trimmed a {len(text)} character message to {MAX_PROMPT_CHARS}")
        text = text[:MAX_PROMPT_CHARS].rstrip()
    return text


def customer_emails():
    """Addresses on the order book, for snapping a misheard one back."""
    return [o["email"] for o in orders.ORDERS.values() if o.get("email")]


def clean_up(chan, text, tell=False, turn_id=None):
    """Repair a speech transcript before anything reads it.

    Everything downstream — the language detector, retrieval, the model, the
    order lookup — was being handed whatever the browser's recogniser made of
    an order number or an address, and each of them coped differently or not
    at all. One pass here, at the point the text arrives, is the same thing a
    dictation system does, and it means the caller sees the corrected words
    rather than the model quietly working around them.
    """
    if not text:
        return text
    try:
        fixed, changes = heard.repair(text, emails=customer_emails(),
                                      orders=orders.ORDERS)
    except Exception as exc:          # never lose a turn over a tidy-up
        log_error(exc, "repairing the transcript")
        return text
    if changes and tell:
        for before, after in changes:
            print(f"  ~ heard {before!r} — reading it as {after!r}")
        asyncio.ensure_future(chan.event({
            "type": "reheard", "turn_id": turn_id, "text": fixed,
            "changes": [{"from": b, "to": a} for b, a in changes],
        }))
    return fixed


FORGET = re.compile(
    r"\b(forget me|delete my (data|details|record|account|history)|"
    r"remove my (data|details|record|account)|erase my (data|record)|"
    r"wipe my (data|record)|don'?t keep (my|anything))\b", re.I)

FORGOTTEN = {
    "en": "Done — everything I had about you is deleted. ",
    "es": "Hecho: he borrado todo lo que tenía sobre usted. ",
    "fr": "C'est fait : tout ce que j'avais sur vous est supprimé. ",
    "de": "Erledigt — alles, was ich über Sie hatte, ist gelöscht. ",
    "it": "Fatto: ho cancellato tutto quello che avevo su di lei. ",
    "pt": "Feito — apaguei tudo o que tinha sobre si. ",
    "nl": "Gedaan — alles wat ik over u had is verwijderd. ",
    "ar": "تم — حذفت كل ما كان عندي عنك. ",
}


def wants_forgetting(session, said):
    """Returns an event when their record has just been deleted.

    Only from a verified caller. Anyone can say "forget me"; deleting
    somebody's history because an unverified voice asked would be the
    same hole as handing it over to one.
    """
    if not FORGET.search(said or ""):
        return None
    c = session.caller
    if session.signed_in and not session.signed_in_forgotten:
        # Signed in, "me" is the address they signed in with, whatever
        # other address this call has been about. It is not made again for
        # the rest of this call; a later sign-in starts a fresh record.
        removed = memory.forget(session.signed_in)
        session.signed_in_forgotten = True
        session.caller = None
        session.awaiting_code = False
        session.unanswered = []
        return {"type": "forget", "done": True, "removed": removed["calls"],
                "message": "Their record is deleted: calls, name and address, "
                           "all of it. Say so plainly in one sentence and do "
                           "not offer to put it back."}
    if not c or not c.get("verified"):
        return {"type": "forget", "done": False,
                "message": "They asked to be forgotten but have not been "
                           "verified. Tell them you can do that once you "
                           "know who they are, and offer to send a code."}
    removed = memory.forget(c["email"])
    session.caller = None
    session.awaiting_code = False
    session.unanswered = []
    return {"type": "forget", "done": True, "removed": removed["calls"],
            "message": "Their record is deleted: calls, name and address, "
                       "all of it. Say so plainly in one sentence and do "
                       "not offer to put it back."}


def hear_code(session, said):
    """A code read out, or typed. Returns an event, or None if not a code.

    Both routes land here, so they share one attempt counter — three
    between them, not three each.
    """
    if not session.awaiting_code or not session.caller:
        return None
    checked = slots.check_code(said)
    if not checked.ok:
        return None
    c = session.caller
    verdict = memory.check_code(c["email"], checked.value, caller=session.key)
    if verdict == "ok":
        person = memory.customer(c["email"]) or {}
        c.update(verified=True, name=person.get("name") or c.get("claimed"),
                 calls=memory.history(c["email"]), mismatch=False)
        session.awaiting_code = False
        session.just_verified = True
        return caller_event(c, "verified")
    if verdict == "locked":
        session.awaiting_code = False
        c["throttled"] = True
        return caller_event(c, "too_many")
    event = caller_event(c, "wrong_code")
    event["left"] = memory.attempts_left(c["email"], caller=session.key)
    # One miss and the box appears, as agreed: voice first, typing after.
    event["show_typed"] = True
    return event


def identify(session, prompt):
    """Work out who is on the phone. Returns an event or None.

    Two ways in. Either an order has been looked up and they are confirming
    the address on it, or they lead with the address — "hello, it's
    r.patel@example.com" — which is how most support calls actually start and
    which did nothing at all before: session.caller only existed after a
    lookup, so giving the address first fell through to "I'm not sure what
    you're asking about".

    An address is not proof of identity, and in front of real customer data
    this is where a one-time code would go. Here it opens the same door that
    confirming it against an order already opens.
    """
    if session.signed_in:
        return identify_signed_in(session, prompt)
    c = session.caller
    if c and c["verified"]:
        return None
    # Confirming against an order has to be the address itself. Opening a
    # call can be the name — nobody should have to spell an address into a
    # microphone to be recognised.
    if not c:
        session.stated_name = stated_name(prompt) or session.stated_name
    email = memory.find_email(prompt) if c else memory.find_customer(prompt)
    if not email:
        return None
    if not c:
        return claim(session, email)
    if email == c["email"]:
        person = memory.customer(email) or {"name": email.split("@")[0]}
        c.update(verified=True, name=person["name"], calls=memory.history(email),
                 mismatch=False)
        return caller_event(c, "verified")
    c["mismatch"] = True
    c["attempts"] = c.get("attempts", 0) + 1
    return caller_event(c, "mismatch")


def claim(session, email):
    """Someone says an address is theirs: start the code, share nothing yet."""
    person = memory.customer(email)
    if not person:
        # A new caller. Only an address said out loud gets here: a name
        # alone finds nobody who is not already on file. Put them on
        # file, unverified, so the code below can be made for them, and
        # from then on it is the same path a known customer takes, with
        # the same event at the end, so the answer does not say which.
        if not delivery.available():
            session.caller = {"email": email.lower(), "order": None,
                              "verified": False, "name": None, "calls": [],
                              "mismatch": False, "claimed": None}
            return start_verification(session)
        memory.add_caller(email, session.stated_name or name_from(email))
        person = memory.customer(email)
    # Known, but not yet proven to be them. A name and an address are
    # a claim; the code is what makes it a fact. Nothing personal is
    # attached to the session until it checks out.
    session.caller = {"email": email.lower(), "order": None,
                      "verified": False, "name": None, "calls": [],
                      "mismatch": False, "claimed": person["name"]}
    return start_verification(session)


def signed_in_caller(session):
    """The signed-in user as the caller, record and all.

    With Auth0 on, the email they signed in with is who they are: Auth0
    verified it, and oauth2-proxy refuses an unverified one. So there is no
    code to read back for it. Their record is made the first time, under a
    name taken from the address, and marked verified either way.
    """
    email = session.signed_in
    memory.add_caller(email, name_from(email), verified=True)
    person = memory.customer(email) or {}
    return {"email": email, "order": None, "verified": True,
            "name": person.get("name") or name_from(email),
            "calls": memory.history(email), "mismatch": False,
            "claimed": None, "signed_in": True}


def identify_signed_in(session, prompt):
    """identify(), for a caller Auth0 has already signed in.

    Their own address changes nothing: it is already them. Any other
    address is an ordinary claim, the code flow and nothing from that
    record until it checks out, however signed in they are. A name on its
    own claims nobody here: it is too easily someone they are calling
    about rather than who they are.
    """
    email = memory.find_email(prompt)
    if not email:
        return None
    c = session.caller
    if email == session.signed_in:
        if session.signed_in_forgotten or (c and c.get("signed_in")):
            return None
        # Back to themselves after claiming another address.
        session.awaiting_code = False
        session.caller = signed_in_caller(session)
        return caller_event(session.caller, "verified")
    if c and c["email"] == email:
        return None
    return claim(session, email)


# How a caller says who they are, in each of the eight languages. Two
# kinds. Naming outright ("my name is", "me llamo", "اسمي") is taken at its
# word. Saying "I am" or "this is" is as often "I'm calling about" or "soy
# cliente", so after one of those the first word has to be capitalised
# (where the script has capitals) and not on the list below.
NAMING = {
    "en": ("my name is", "my name's"),
    "es": ("me llamo", "mi nombre es"),
    "fr": ("je m'appelle", "mon nom est"),
    "de": ("ich heiße", "ich heisse", "mein name ist"),
    "it": ("mi chiamo", "il mio nome è"),
    "pt": ("meu nome é", "me chamo", "chamo-me"),
    "nl": ("ik heet", "mijn naam is"),
    "ar": ("اسمي",),
}
INTRODUCING = {
    "en": ("this is", "i am", "i'm"),
    "es": ("soy", "le habla"),
    "fr": ("je suis", "c'est"),
    "de": ("ich bin", "hier ist", "hier spricht"),
    "it": ("sono", "parla"),
    "pt": ("sou", "aqui é", "fala"),
    "nl": ("ik ben", "u spreekt met", "je spreekt met"),
    "ar": ("أنا", "انا", "معك"),
}
NOT_A_NAME = set("""
    calling looking trying having just not here fine good ok okay a an the so
    and about with from at on in my your new still really sorry back again
    well very customer
    cliente yo el la un una muy aquí nuevo nueva bien llamando para por con
    que no
    client le une très ici nouveau nouvelle pour en pas désolé désolée moi
    kunde kundin der die das ein eine sehr hier neu neuer neue gut nicht es
    wegen
    il lo molto qui nuovo nuova bene per non io di stato
    o um uma muito novo nova bem não eu
    klant het een erg heel nieuw nieuwe goed niet bezig
    أريد اريد عندي بخير هنا عميل عميلة جديد اتصل أتصل لست آسف اسمي من في
    """.split())
# Lower-case words that belong inside a name when a capitalised one follows:
# "Jan de Vries", "Ana dos Santos", "Ludwig van Beethoven".
PARTICLES = {"de", "da", "das", "do", "dos", "del", "della", "di", "van",
             "von", "der", "den", "ter", "le", "la", "bin", "ibn", "al", "el"}
_NAME_WORDS = r"([^\W\d_][\w'\-]*(?:\s+[^\W\d_][\w'\-]*){0,3})"


def _phrases(table):
    said = sorted({p for ps in table.values() for p in ps}, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, said)) + r")\s+"
                      + _NAME_WORDS, re.I)


NAMING_RE, INTRODUCING_RE = _phrases(NAMING), _phrases(INTRODUCING)


def _cased(word):
    return word[:1].lower() != word[:1].upper()


def stated_name(prompt):
    """The name a caller gave for themselves in this sentence, or None."""
    text = memory.EMAIL.sub(" ", (prompt or "").replace("’", "'"))
    outright = NAMING_RE.search(text)
    found = outright or INTRODUCING_RE.search(text)
    if not found:
        return None
    said = found.group(1).split()
    if not outright and _cased(said[0]) and not said[0][0].isupper():
        return None
    words = []
    for i, word in enumerate(said[:4]):
        nxt = said[i + 1] if i + 1 < len(said) else ""
        if (words and word in PARTICLES and nxt and _cased(nxt)
                and nxt[0].isupper()):
            words.append(word)
            continue
        if word.lower() in NOT_A_NAME:
            break
        words.append(word)
    while words and words[-1] in PARTICLES:
        words.pop()
    if not words:
        return None
    checked = slots.check_name(" ".join(words))
    return checked.value if checked.ok else None


def name_from(email):
    """r.patel@example.com -> "R Patel", for a caller who gave no name."""
    local = email.split("@")[0]
    return " ".join(p.capitalize() for p in re.split(r"[._\-+]+", local) if p) or local


def start_verification(session):
    """Send a code, or say plainly that we cannot.

    The same thing is said whether or not the address was on file before
    this call, and new callers are put on file before getting here, so the
    answer cannot be used to find out who is a customer.
    """
    c = session.caller
    if not delivery.available():
        # Openly absent, never silently skipped.
        c["unavailable"] = True
        return caller_event(c, "unavailable")
    code, why = memory.make_code(c["email"], caller=session.key)
    if code:
        sent, on_screen = delivery.send_code(
            c["email"], code, minutes=memory.CODE_MINUTES)
        if sent:
            session.awaiting_code = True
            event = caller_event(c, "code_sent")
            if on_screen:
                # The demo inbox, and labelled as such wherever it shows.
                event["demo_code"] = on_screen
                event["demo_note"] = ("Demo mode: code shown on screen. "
                                      "In production it's emailed.")
            return event
    if why in ("locked", "caller locked", "too many", "caller asked too often"):
        c["throttled"] = True
        return caller_event(c, "too_many")
    # Unknown address, or nothing could be sent. Same words either way.
    session.awaiting_code = True
    return caller_event(c, "code_sent")


def caller_event(c, status):
    return {
        "type": "caller", "status": status,
        "order": c.get("order"), "email": memory.mask(c.get("email")),
        "name": c.get("name") if c.get("verified") else None,
        "signed_in": bool(c.get("signed_in")),
        "calls": [
            {"at": x["at"], "summary": x["summary"], "outcome": x.get("outcome"),
             "actions": x.get("actions"), "seeded": bool(x.get("seeded"))}
            for x in (c.get("calls") or [])
        ] if c.get("verified") else [],
    }


def caller_rules(session):
    c = session.caller
    if not c:
        return ""
    if c["verified"]:
        # The caller's last words were six digits. Without this the model
        # either looks them up as an order number or answers them as a
        # question — "I don't have any information about a code like that"
        # — when what just happened is that they got in.
        just_now = ("\nThose six digits were the code, and it was right: "
                    "they are now verified. Say nothing about the code at "
                    "all. Treat this turn as the moment they have just "
                    "introduced themselves — greet them by name, warmly and "
                    "briefly, and ask what you can do for them.\n"
                    if session.just_verified else "")
        if c.get("signed_in"):
            # Auth0 already said who this is. Asking for an email or a code
            # would be asking them to prove it twice.
            just_now = ("\nThey are signed in, so you already know who they are. "
                        "Never ask for their email address or a code. "
                        + ("This is the start of the call: greet them by name, "
                           "warmly and briefly, and ask what you can do for them.\n"
                           if session.user_turns == 0 else "\n"))
        return (
            just_now + "\n" + memory.as_context(c["name"], c.get("calls") or []) +
            "\nUse this the way a good agent who remembers them would: if a previous "
            "call is relevant, bring it up once, briefly and in your own words — "
            "for example what was promised last time and whether it happened. Never "
            "read the history out as a list, and do not mention it at all if it is "
            "irrelevant to what they are asking now.\n"
            "If they have only just said who they are and asked nothing yet, greet "
            "them by name and ask what you can do — that is not a moment to hand "
            "the call to anyone, because nobody has asked for anything.\n"
            "They have just confirmed their email address, so do not read it back. "
            "The transcript of an address spoken aloud is half wrong in any case — "
            "it came through once as \"our dot patel\" — and repeating that at them "
            "sounds like you did not understand. Greet them by name and carry on."
        )
    # Waiting on a code. What the agent may say here is fixed, because it
    # is the one place a wrong word leaks whether an address is on file.
    if c.get("unavailable"):
        return ("\nVerification is not set up on this deployment, so you "
                "cannot confirm who they are and must not share anything "
                "from a previous call. Say that you can't pull up their "
                "account right now, and help them with what they need "
                "anyway. Do not imply you will try again later.")
    if c.get("throttled"):
        return ("\nThere have been too many code attempts. Say that you "
                "can't try again just yet, ask them to give it a quarter of "
                "an hour, and help with anything that does not need their "
                "account.")
    if session.awaiting_code:
        return ("\nA six digit code is on its way to that address. Say only "
                "that — new caller or not, it is the same sentence. Never "
                "say whether the address was already known, how many "
                "people share a name, or anything about them.\n"
                "Ask them to read the code back. Say nothing about their "
                "account or previous calls until it checks out. If they "
                "cannot find it, say it may take a moment and that they can "
                "type it into the box on the page instead.")
    if c.get("mismatch"):
        return (
            f"\nThe email the caller gave does not match order {c['order']}. Tell them "
            "kindly that it does not match and ask them to check it. Say nothing about "
            "their account or previous calls until it does. Never say the right email."
        )
    return (
        f"\nYou have looked up order {c['order']}. You can discuss the order itself. "
        "Before discussing the caller's account or anything from previous calls, ask "
        "them to confirm the email address on the order — once, naturally. Never say "
        "the email address yourself."
    )


WRAP_FIELDS = ("summary", "outcome", "actions", "sentiment", "quality")

# Questions the documentation did not cover. Worth keeping: each one is
# either a gap in the knowledge base or a thing this agent should not be
# asked, and both are only visible in aggregate.
UNANSWERED_FIELD = "unanswered"


def parse_write_up(text):
    out = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in WRAP_FIELDS and value.strip():
            out[key] = value.strip()
    return out


# Work that outlives the socket it was started from. Held in a set because
# asyncio keeps only a weak reference to a running task and will collect one
# nobody is holding, mid-flight.
BACKGROUND = set()


def in_background(coro, what, seconds=30):
    """Run something after the caller has gone, without making them wait."""
    async def run():
        try:
            await asyncio.wait_for(coro, seconds)
        except asyncio.TimeoutError:
            print(f"  ! {what} gave up after {seconds}s")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(exc, what)

    task = asyncio.ensure_future(run())
    BACKGROUND.add(task)
    task.add_done_callback(BACKGROUND.discard)
    return task


async def remember_call(llm, session, note=None):
    """Write the call to the caller's history, once, if they were verified."""
    c = session.caller
    if not c or not c.get("verified") or session.user_turns <= session.saved_turns:
        return None
    if note is None:
        note = await write_up(llm, session.history)
    if not note:
        return None
    fields = parse_write_up(note["text"])
    if session.unanswered:
        fields[UNANSWERED_FIELD] = " | ".join(session.unanswered[:20])
    if not memory.save_call(c["email"], time.strftime("%Y-%m-%d %H:%M"), fields,
                            since=session.began):
        # They were forgotten while this call was going on, from this call
        # or another. Saving it would bring them back.
        print("  call not saved: the caller asked to be forgotten during it")
        return None
    session.saved_turns = session.user_turns
    c["calls"] = memory.history(c["email"])
    return fields


# ================================================================ fact-check

CHECK_MODEL = os.getenv("SAMBANOVA_CHECK_MODEL") or "Meta-Llama-3.3-70B-Instruct"

CHECK_PROMPT = (
    "You check one sentence a phone support agent is about to say out loud, "
    "against the records below.\n\nRecords:\n\n{sources}\n\n"
    "What the caller said: {asked}\n\nAlready said: {before}\n\n"
    "Sentence: {sentence}\n\n"
    "Reply HOLD only if the sentence contradicts the records, or states a specific "
    "number, date, price, time limit or policy that the records do not contain. "
    "These are all OK: paraphrase, numbers written as words, summaries of the "
    "records, offers the records allow, questions, greetings and empathy. "
    "Also OK, and the things most often held by mistake:\n"
    "- Applying a rule in the records to what the caller has just described. "
    "Their own account of their situation is a given, not something to verify, "
    "and working out what the rule means for them is the job.\n"
    "- Repeating back something the caller themselves supplied, an order "
    "number above all. \"Your order number is 44901\" was held as an "
    "unsupported number when the caller had just read those digits out; "
    "confirming what you heard is not a claim about the records.\n"
    "- Arithmetic on numbers the caller supplied or the records contain.\n"
    "- Ordinary words about the present — now, currently, today, still, "
    "already, at this point. They are speech, not a claim about the date. "
    "\"Covered for six months, so it is not covered now\" was held for the "
    "word \"now\" when the six months was in the records and the caller had "
    "just said nine; that is the mistake to avoid.\n"
    "- Two fields of the same record are both true at once. An order can be "
    "\"in transit\" and also past the threshold that makes it count as lost; "
    "saying both is a description, not a contradiction. Only call it a "
    "contradiction when the record denies what the sentence asserts.\n"
    "- The plain meaning of a field. \"In transit\" means it has been "
    "dispatched, so \"it has shipped\" is the same fact in the caller's "
    "words, not a new one.\n"
    "Hold what the agent would be inventing, not what it is concluding. "
    "Reply exactly OK, or HOLD: followed by what is unsupported, in under twelve words."
)

# When something is held back, the model gets one chance to say it again from
# the records before the agent falls back to "let me double-check".
RETRY_RULE = (
    "Some of what you were about to say was not supported by the records, so it "
    "was not spoken:\n{held}\n\nCarry straight on from what you have already "
    "said and give the caller the answer, using only what the records and "
    "documents above actually state. If they do not cover something, say you "
    "will check it. Do not apologise, and never mention checking, records or "
    "anything being held back."
)

# Said when the checker pulled what the agent was about to say. The old
# wording — "let me double-check that before I confirm it" — was the same
# promise as "I'll find out and come back": there is nobody to check with
# and no second attempt coming, so the caller was left waiting on it. These
# say what is true, which is that we do not have it confirmed, and offer the
# colleague who would.
REPAIR = {
    "en": "Actually, I don't have that confirmed, so I'd rather not guess. "
          "I can put you through to the team who would know — shall I? ",
    "ar": "في الحقيقة، ما عندي هذي المعلومة مؤكدة، وما أبي أخمّن. "
          "أقدر أحوّلك على الفريق اللي يعرفها، تحب؟ ",
    "es": "La verdad es que no tengo eso confirmado y prefiero no suponer. "
          "Puedo pasarte con el equipo que lo sabría, ¿te parece? ",
    "fr": "En fait, je n'ai pas cette information confirmée et je préfère ne "
          "pas deviner. Je peux vous passer l'équipe qui saurait, voulez-vous ? ",
    "de": "Ehrlich gesagt habe ich das nicht bestätigt, und raten möchte ich "
          "nicht. Ich kann Sie an das Team weiterleiten, das es weiß — soll ich? ",
    "it": "A dire il vero non ho quel dato confermato e preferisco non tirare a "
          "indovinare. Posso passarti al team che lo saprebbe, vuoi? ",
    "pt": "Na verdade não tenho essa informação confirmada e prefiro não "
          "adivinhar. Posso passar para a equipa que saberia, quer? ",
    "nl": "Eerlijk gezegd heb ik dat niet bevestigd en ik wil niet gokken. "
          "Ik kan u doorverbinden met het team dat het weet — zal ik dat doen? ",
}


async def check_sentence(llm, sentence, sources, before, asked="", meter=None):
    t = time.perf_counter()
    try:
        reply = await asyncio.wait_for(llm.chat.completions.create(
            model=CHECK_MODEL, max_tokens=30, temperature=0,
            messages=[{"role": "user", "content": CHECK_PROMPT.format(
                sources=sources[:6000], before=before[-600:] or "(nothing yet)",
                asked=(asked or "(nothing yet)")[-400:],
                sentence=sentence.strip())}]), timeout=6)
        verdict = (reply.choices[0].message.content or "").strip()
        if meter is not None:
            meter.add_usage(CHECK_MODEL, getattr(reply, "usage", None))
    except Exception as exc:
        # Fail open: a checker that is down should not silence the agent. The
        # browser is told the sentence went out unchecked.
        log_error(exc, "fact-check")
        return True, "unchecked", time.perf_counter() - t
    took = time.perf_counter() - t
    if verdict.upper().startswith("HOLD"):
        return False, verdict.split(":", 1)[-1].strip() or "not in the records", took
    return True, "", took


# One word, two writing systems, no space between them: "الشipment", where the
# model began the Arabic for "the shipment" and finished the word in English.
# It is never legitimate — no language in this demo writes a single word in two
# scripts — and it survived every prompt I wrote and every temperature from 1.0
# down to 0.1 unchanged, which is what says it is not a sampling problem.
#
# Cartesia is given one language per generation, so it reads the Latin tail
# with Arabic phonology and the caller hears noise. Dropping the word costs a
# noun; speaking it costs the sentence. The count goes on the turn so this
# stays visible rather than becoming a silent papering-over.
# A word is a run of letters plus the Arabic vowel marks, which are combining
# characters and so not \w — without them "خلّيني" splits at the shadda.
_W = r"[\w\u064b-\u0652\u0670]"
_AR = r"[\u0600-\u06ff\u0750-\u077f]"
_CJK = r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]"
_LAT = r"[A-Za-z]"
FUSED = re.compile(
    f"{_W}*(?:{_AR}(?:{_LAT}|{_CJK})|(?:{_LAT}|{_CJK}){_AR}|{_LAT}{_CJK}|{_CJK}{_LAT}){_W}*"
)


def unfuse(sentence):
    """Drop any word written in two scripts. Returns (clean, dropped)."""
    dropped = FUSED.findall(sentence)
    if not dropped:
        return sentence, []
    out = FUSED.sub("", sentence)
    # Removing a word leaves a double space behind, and strands the space in
    # front of whatever punctuation followed it.
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([،,.؟?!:;])", r"\1", out)
    # The gate hands sentences over with their trailing space, and the next
    # one is appended straight onto this: strip() here gave "حالة.هل".
    trailing = sentence[len(sentence.rstrip()):]
    return out.strip() + trailing, dropped


class Gate:
    """Sits between the language model and the speaker, one sentence at a time.

    Sentences that make a checkable claim — a number, a date, a policy — are
    checked against what the agent actually knows this turn (the documents,
    the order record, the search results) before Cartesia gets them. Checks
    run in parallel and sentences are released in order, so only the first
    claim costs a round trip; after that, checking happens while earlier
    sentences are being spoken. Anything unsupported is held back.
    """

    def __init__(self, llm, sources, release, on_held, enabled, turn, asked=""):
        self.llm = llm
        self.asked = asked
        self.sources = sources         # grows during the turn
        self.release = release
        self.on_held = on_held
        self.enabled = enabled
        self.turn = turn
        self.buf = ""
        self.said = ""
        self.queue = asyncio.Queue()
        self.held = []
        # Once a claim has been pulled, the rest of the same answer was
        # written on top of it and cannot be trusted to stand alone.
        self.spoiled = False
        self.worker = asyncio.ensure_future(self._drain())

    async def feed(self, delta):
        self.buf += delta
        done, self.buf = smarts.split_sentences(self.buf)
        for sentence in done:
            self._submit(sentence)

    def _submit(self, sentence):
        sentence, dropped = unfuse(sentence)
        if dropped:
            self.turn.fused += len(dropped)
            print(f"  ~ dropped {len(dropped)} half-translated word(s): {dropped}")
        if not sentence.strip():
            return
        check = None
        if self.enabled and self.sources and smarts.has_claim(sentence):
            check = asyncio.ensure_future(check_sentence(
                self.llm, sentence, "\n\n".join(self.sources), self.said,
                self.asked, self.turn.meter))
        self.said += sentence
        self.queue.put_nowait((sentence, check))

    async def _drain(self):
        while True:
            item = await self.queue.get()
            if item is None:
                return
            sentence, check = item
            if check is not None:
                ok, reason, took = await check
                self.turn.checks["checked"] += 1
                self.turn.checks["slowest_s"] = max(self.turn.checks["slowest_s"], took)
                if not ok:
                    self.turn.checks["held"] += 1
                    self.held.append(sentence)
                    self.spoiled = True
                    await self.on_held(sentence, reason)
                    continue
            if self.spoiled:
                # "Batteries are covered for a year." was held, and what
                # followed it — "Would you like that?" — was released on its
                # own, referring to an offer the caller never heard. A
                # sentence written after a removed one is a dangling
                # reference more often than it is a usable answer, and the
                # rewrite below covers the same ground properly.
                self.turn.checks["held"] += 1
                self.held.append(sentence)
                await self.on_held(sentence, "follows a sentence that was held")
                continue
            await self.release(sentence)

    def boundary(self):
        """A fresh generation is about to feed into the same turn.

        A model that stops to call a tool often leaves a part-sentence behind,
        and what comes back after the tool is appended straight onto it:
        "...what the order number is?" and "Actually, let me double-check"
        arrived as "is?Actually", which the sentence splitter cannot break
        because it needs whitespace after the mark — so it reached Cartesia
        as one word.
        """
        if self.buf and not self.buf[-1].isspace():
            self.buf += " "

    async def close(self):
        if self.buf.strip():
            self._submit(self.buf)
        self.buf = ""
        self.queue.put_nowait(None)
        await self.worker

    def cancel(self):
        self.worker.cancel()


# ================================================================ prepare

class Feed:
    """A model stream that starts now and is read later.

    A draft starts generating while the caller is still talking; if the turn
    that arrives matches it, the turn reads what was already produced and then
    carries on live. If it does not match, the draft is cancelled and nothing
    it produced is ever heard.
    """

    def __init__(self, factory):
        self.items = []
        self.done = False
        self.error = None
        self.wake = asyncio.Event()
        self.task = asyncio.ensure_future(self._run(factory))

    async def _run(self, factory):
        try:
            async for item in factory():
                self.items.append(item)
                self.wake.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
        finally:
            self.done = True
            self.wake.set()

    async def read(self):
        i = 0
        while True:
            while i < len(self.items):
                yield self.items[i]
                i += 1
            if self.done:
                if self.error:
                    raise self.error
                return
            self.wake.clear()
            if i < len(self.items) or self.done:
                continue
            await self.wake.wait()

    def cancel(self):
        if not self.task.done():
            self.task.cancel()


TALKER_RULE = (
    "A colleague with more time is working out the full answer and it will be "
    "spoken straight after you. Say one or two short sentences that show you "
    "understood the question and set up the answer — what you are going to look "
    "at — without giving the answer itself, any numbers or any conclusion. Do "
    "not ask a question, and never mention a colleague or waiting."
)

THINKER_RULE = (
    "Work out the answer carefully, then reply with only what should be said "
    "out loud next. The caller has already heard a short acknowledgement, so "
    "start directly with the substance: no greeting, no 'okay', 'so' or 'sure'. "
    "Two to four spoken sentences, no lists, no markdown."
)


async def collect_text(llm, messages, model, stats=None):
    """A complete reply from a reasoning model, as one piece of text."""
    parts = []
    async for delta in stream_chat(llm, messages, model=model, stats=stats):
        parts.append(delta)
    yield "".join(parts).strip()


class Prepared:
    """Everything a turn needs before its first spoken word, made in one place
    so an early draft and a real turn are built identically."""

    def __init__(self):
        self.convo = []
        self.hits = []
        self.sources = []
        self.route = None
        self.mood = "neutral"
        self.talk = None
        self.thinker = None
        self.stats = {}
        self.think_stats = {}
        self.meter = pricing.Meter()
        self.temperature = None
        self.started = time.perf_counter()

    def cancel(self):
        for feed in (self.talk, self.thinker):
            if feed is not None:
                feed.cancel()


def build_system(session, code, opts, mood_now, route_):
    system = SYSTEM_PROMPT + language_rule(code) + ORDER_RULES
    if opts["on_call"]:
        system += "\n" + CALL_PROMPT
    if opts["source"] == "screen":
        system += "\n" + SCREEN_PROMPT
    if opts["can_search"] and route_["tier"] != "deep":
        system += SEARCH_RULES
    if mood_now in smarts.MOOD_RULES:
        system += "\n" + smarts.MOOD_RULES[mood_now]
    system += caller_rules(session)
    persona = PERSONAS.get(session.persona)
    if persona:
        system += "\n" + persona["rule"]
    system += ("\nWhen you and the caller agree a replacement, refund or returns label, "
               "carry it out with resolve_order and give them the reference it returns.")
    return system


async def prepare(llm, session, prompt, opts, code, extras=(), intro=None):
    p = Prepared()
    p.temperature = sampling_for(code)
    p.mood = session.mood if intro else smarts.mood(prompt, session.mood)

    number = orders.find_number(prompt)
    has_order = bool(number and number in orders.ORDERS)
    # The caller read their order number down a microphone and the browser
    # wrote "for for eight one to". The lookup copes, but the model is
    # reading the same transcript: left alone it does not see a number to
    # look up, and when it does answer it reads the mishearing back. Say what
    # the digits were, only when they resolve to a real order and the
    # transcript does not already contain them.
    heard_as = (f"The caller said their order number out loud and it came "
                f"through the microphone as words. It is {number}. Use that, "
                f"and say it back as digits." if has_order and number not in prompt
                else None)
    if opts["auto"] and not opts["attachments"] and not intro:
        p.route = smarts.route(prompt, CATALOG["models"], opts["model"], has_order,
                               code=code)
    else:
        p.route = {"tier": "standard", "model": opts["model"],
                   "reason": "took over the call" if intro
                   else ("chosen in the picker" if not opts["auto"]
                         else ("reading your screen" if opts["source"] == "screen" else "looking at a photo"))}

    system = build_system(session, code, opts, p.mood, p.route)
    if intro:
        # Nobody has said anything new: the specialist speaks first.
        messages = ([{"role": "system", "content": system + "\n" + INTRO_RULE}]
                    + list(session.history)
                    + [{"role": "system", "content": f"Why you were brought in: {intro.get('reason') or 'see above'}"}])
    else:
        messages = ([{"role": "system", "content": system}]
                    + list(session.history) + [{"role": "user", "content": prompt}])

    # Retrieval is local and costs microseconds, so it happens on every turn
    # rather than behind a tool call the model has to remember to use. Only
    # pay for a translation round trip when the question might not be English.
    # A specialist's opening has no new question; look up why they were called.
    if heard_as:
        extras = list(extras) + [{"role": "system", "content": heard_as}]

    query = (intro or {}).get("reason") or prompt
    hits = KB.find(query) if query else []
    if query and not hits and (code != "en" or languages.needs_translation(query)):
        lookup = await in_english(llm, query, "translate", p.meter)
        if lookup and lookup.lower() != query.lower():
            hits = KB.find(lookup)
    p.hits = hits
    if hits:
        p.sources.append(kb_context(hits))
    convo = messages + list(extras) + [
        {"role": "system", "content": policy_prompt(hits) if hits else NO_POLICY}]
    # Those last two are written in English and they are the final thing the
    # model reads, which is enough to pull the whole reply into English: every
    # "what are your opening hours" in French, Italian, Portuguese and Dutch
    # came back in English until the language was restated after them.
    if code != "en":
        convo.append({"role": "system", "content": speak_in(code)})
    if getattr(session, "just_forgot", None):
        convo.append({"role": "system", "content": session.just_forgot})
    p.convo = convo

    if p.route["tier"] == "deep":
        talk_convo = convo + [{"role": "system", "content": TALKER_RULE}]
        think_convo = convo + [{"role": "system", "content": THINKER_RULE}]
        p.talk = Feed(lambda: stream_tools(llm, talk_convo, model=p.route["model"],
                                           stats=p.stats, temperature=p.temperature))
        p.thinker = Feed(lambda: collect_text(llm, think_convo, p.route["thinker"], p.think_stats))
    else:
        tools = (SEARCH_TOOL if opts["can_search"] else []) + [ORDER_TOOL, ACTION_TOOL]
        # Withholding this tool whenever the knowledge base had a hit was the
        # obvious fix for the over-eager transfers and it made things worse:
        # 13/17 against 16/17 for the tool description alone. "Charged twice
        # for the same order" matches the heading "When an order is late",
        # and "pair my phone with the speaker" matches "Phone support", so
        # the callers who most needed a specialist were the ones who could no
        # longer reach one. Retrieval score does not separate the two either
        # — the questions the front agent should keep score 3.48 to 10.52 and
        # the ones it should pass on score 0.00 to 5.43. So the decision is
        # left to the model, and the tool description carries it: over the
        # same 17 cases the original description averaged 13.0 right (14, 13,
        # 12) and the one below averaged 15.6 (16, 16, 16, 15, 15). It is a
        # judgement made by a language model, so it is not deterministic and
        # the last case or two move between runs.
        # Not on the turn where they have just said who they are. "Hi, it's
        # Patel" was handed to billing five times out of five — nobody had
        # asked for anything yet — and telling the model not to made no
        # difference at all, so the tool is simply not there for that turn.
        if session.persona == "front" and not session.just_named:
            tools.append(TRANSFER_TOOL)
        p.talk = Feed(lambda: stream_tools(llm, convo, model=p.route["model"],
                                           tools=tools, stats=p.stats,
                                           temperature=p.temperature))
    return p


class Draft:
    def __init__(self, text, task, history_len, key, code):
        self.words = norm(text)
        self.task = task
        self.history_len = history_len
        self.key = key
        self.code = code
        self.started = time.perf_counter()

    def matches(self, prompt, session, key, code):
        return (self.words == norm(prompt) and self.history_len == len(session.history)
                and self.key == key and self.code == code)

    def cancel(self):
        if self.task.done():
            if not self.task.cancelled() and self.task.exception() is None:
                self.task.result().cancel()
        else:
            self.task.cancel()


# ================================================================ synthesis

def voice_spec(voice_id, controls=None):
    spec = {"mode": "id", "id": voice_id}
    if controls:
        spec["__experimental_controls"] = controls
    return spec


async def synthesise(chan, conn, turn_id, text, voice_id, language="en"):
    """Speak one fixed string — used for the assistant's own 'which first?'
    question, which has no language model in front of it."""
    ctx = conn.context(
        model_id=tts_model_for(language),
        voice=voice_spec(voice_id),
        output_format=output_format(),
        language=language,
    )
    await ctx.push(text)
    await ctx.no_more_inputs()
    async for res in ctx.receive():
        if res.type == "chunk" and res.audio:
            await chan.audio(turn_id, res.audio)
        elif res.type == "error":
            raise RuntimeError(res.message or res.title or "Cartesia error")


FINALLY_RULE = (
    "Answer the caller now, in two or three spoken sentences, using only what "
    "is above. Do not call any tool and do not mention looking anything up."
)

INTERPRET_RULE = (
    "You are a live interpreter between two people on a call. Translate what the "
    "speaker just said into {target}. Output only the translation, as it should be "
    "spoken: natural, the same register and tone, nothing added, nothing answered. "
    "Keep names, numbers and order numbers exactly."
)


# ================================================================ a turn

async def run_turn(chan, conn, llm, session, turn_id, prompt, opts, interrupt, draft=None,
                   prelook=None, intro=None):
    """One prompt through to speech: prepare (or adopt an early draft), then
    stream the model through the fact-check gate into its own Cartesia
    context, relaying text events and raw PCM frames to the browser."""
    turn = Turn()
    attachments = opts["attachments"]
    interpreting = opts["mode"] == "interpret"
    was = session.speaking["code"]

    if interpreting:
        source_lang = languages.detect(prompt, "en")
        pair = opts["pair"] if opts["pair"] != "en" else "ar"
        if source_lang == "en":
            code = pair
        else:
            code = "en"
            pair = source_lang
        turn.language = code
    else:
        code = resolve_language(session, prompt, opts["language"])
        session.speaking["code"] = code
        turn.language = code
        # Known before a word is generated, so the voice is set from the start.
        turn.mood = session.mood if intro else smarts.mood(prompt, session.mood)

    # Whoever is speaking, in their native voice for the language of this
    # turn, chosen afresh every turn so a switch of language is heard on
    # the very next sentence. None means no native voice: text only.
    voice_id = voice_for(session, code, opts.get("voice"))
    reply = ""
    notes = []
    held_any = []

    # Identity first: a caller who has just confirmed their email changes what
    # the model is told, which is also why a draft made before it is no use.
    who = None
    if not interpreting and not intro:
        session.just_verified = False
        session.just_forgot = None
        # Deleting happens here, in code, before the model sees the turn.
        # What the model does is say so — emit is not defined this early,
        # and a fixed sentence pushed in front of the answer would be a
        # worse shape than telling it what just happened.
        forget = wants_forgetting(session, prompt)
        if forget:
            await chan.event({"type": "caller",
                              "status": "forgotten" if forget["done"]
                              else "forget_refused"})
            notes.append(forget["message"])
            # The last word, because NO_POLICY comes after everything else
            # and would otherwise have the agent say it does not know how
            # to delete anything, on the turn it has just deleted it all.
            session.just_forgot = forget["message"]
        who = hear_code(session, prompt) or identify(session, prompt)
    session.just_named = bool(who)
    if who:
        if draft:
            draft.cancel()
        draft = None

    await chan.event({
        "type": "turn_start", "turn_id": turn_id, "prompt": prompt,
        "model": opts["model"], "voice_id": voice_id,
        "attachments": prelook["count"] if prelook else len(attachments),
        "language": code,
        "language_changed": (not interpreting) and code != was,
        "interpreting": {"from": source_lang if interpreting else None, "to": code}
        if interpreting else None,
        "speaker": ({"name": PERSONAS[session.persona]["name"], "team": PERSONAS[session.persona]["team"]}
                    if session.persona in PERSONAS else None),
        "intro": bool(intro),
    })
    if who:
        await chan.event(who)

    # The language model runs right away; text waits here for a synthesis slot.
    pending_text = asyncio.Queue()

    async def release(text):
        nonlocal reply
        # Everything the caller hears passes through here, including the fixed
        # phrases that never went near the gate: the handoff line, and the
        # "Actually, let me double-check" the gate adds when it holds a
        # sentence back. The last sentence of a generation arrives without a
        # trailing space, so those fused onto it — "the order number
        # is?Actually, let me double-check" reached Cartesia as one word.
        if reply and not reply[-1].isspace() and text[:1].isalnum():
            text = " " + text
        reply += text
        await chan.event({"type": "token", "turn_id": turn_id, "text": text, "t": turn.now()})
        await pending_text.put(text)

    async def on_held(sentence, reason):
        held_any.append((sentence.strip(), reason))
        print(f"  ~ held back: {sentence.strip()[:120]!r} ({reason})")
        await chan.event({"type": "held", "turn_id": turn_id, "sentence": sentence.strip(),
                          "reason": reason, "t": turn.now()})

    sources = []
    gate = Gate(llm, sources, release, on_held,
                enabled=opts["check"] and not interpreting, turn=turn, asked=prompt)

    said_anything = {"yes": False}

    async def emit(delta):
        if delta and delta.strip():
            said_anything["yes"] = True
        await gate.feed(delta)

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
        screen = opts["source"] == "screen"
        await chan.event({"type": "looking", "turn_id": turn_id, "count": count,
                          "t": turn.now(), "speculative": bool(prelook), "screen": screen})

        one_phrase, many_phrase = LOOKING_PHRASE.get(code, LOOKING_PHRASE["en"])
        bridge = one_phrase if count == 1 else many_phrase
        await emit(bridge)

        t_look = time.perf_counter()
        if prelook:
            # Started the moment they began speaking, so by now it is usually
            # finished and this returns immediately.
            description, info = await prelook["task"]
        else:
            description, info = await describe_images(
                llm, attachments, vision_question(prompt, count, screen))
        turn.vision_s = time.perf_counter() - t_look
        turn.meter.add(info.get("model"), info.get("prompt_tokens") or 0, len(description) // 4)
        await chan.event({
            "type": "looked", "turn_id": turn_id, "t": turn.now(),
            "took_s": turn.vision_s, "model": info["model"],
            "prompt_tokens": info["prompt_tokens"],
            "description": description[:400], "screen": screen,
        })
        return bridge, description

    async def generate_interpretation():
        await chan.event({"type": "route", "turn_id": turn_id, "tier": "interpret",
                          "model": TRANSLATOR, "reason": f"{languages.name(pair if code == 'en' else 'en')} "
                          f"to {languages.name(code)}"})
        messages = [{"role": "system", "content": INTERPRET_RULE.format(target=languages.name(code))},
                    {"role": "user", "content": prompt}]
        async for delta in stream_chat(llm, messages, model=TRANSLATOR, stats=turn.server):
            await mark_first()
            await emit(delta)
        turn.meter.add_usage(TRANSLATOR, turn.server)

    last = {}            # the conversation and model the reply was last written from

    async def generate_answer():
        prepared = None

        # Before anything else this call produces. It was first written to
        # ride in front of the holding phrase, which is wrong: that phrase
        # is skipped for short turns, for attachments, when a speculative
        # draft is used — which is most calls — and whenever the caller is
        # not on a call at all. A line that has to be said cannot be
        # attached to one that is optional.
        if not session.disclosed and not intro and not interpreting:
            session.disclosed = True
            await mark_first()
            await emit(disclosure(code))

        if draft is not None:
            try:
                prepared = await draft.task
            except Exception as exc:
                log_error(exc, "early draft")
                prepared = None
            if prepared is not None:
                turn.draft_lead_s = time.perf_counter() - draft.started
                await chan.event({"type": "draft_used", "turn_id": turn_id,
                                  "lead_s": turn.draft_lead_s})

        if prepared is None:
            extras = []
            # Nothing to look at, so nothing else is covering the wait. Skip it
            # for very short turns: "hi there" does not need "right, let me see"
            # in front of it, and those answers come back quickly regardless.
            worth_covering = len(prompt.split()) > 3
            if opts["on_call"] and worth_covering and not attachments and not prelook and not intro:
                filler = backchannel(session.recent_fillers, code)
                session.recent_fillers.append(filler)
                del session.recent_fillers[:-2]
                turn.filler_s = turn.now()
                await emit(filler)
                extras += [
                    {"role": "assistant", "content": filler.strip()},
                    {"role": "system", "content":
                        f"You have already said \"{filler.strip()}\" out loud. Carry "
                        "straight on into the answer. Do not greet, acknowledge or "
                        "say okay again — that has happened."},
                ]
            if attachments or prelook:
                await mark_first()
                bridge, description = await look_at_attachments()
                # `extras` lasts one turn; `notes` goes into history. Without
                # this, anything seen but not said out loud is gone next turn —
                # a caller held up a mug, the agent read it, and three turns
                # later said "I don't know what's written on the mug".
                notes.append(
                    f"What the camera showed earlier{' (their screen)' if opts['source'] == 'screen' else ''}:\n"
                    f"{description}\n"
                    "Still true unless they have moved the camera. Answer from "
                    "it rather than asking them to show you again.")
                extras += [
                    {"role": "assistant", "content": bridge},
                    {"role": "system", "content": seen_prompt(
                        description, prelook["count"] if prelook else len(attachments),
                        opts["source"] == "screen")},
                ]
            prepared = await prepare(llm, session, prompt, opts, code, extras, intro=intro)

        turn.server = prepared.stats
        turn.meter.merge(prepared.meter)
        turn.route = prepared.route
        turn.mood = prepared.mood
        if prepared.mood != session.mood or prepared.mood != "neutral":
            await chan.event({"type": "mood", "turn_id": turn_id, "mood": prepared.mood,
                              "says": smarts.MOOD_SAYS.get(prepared.mood)})
        session.mood = prepared.mood
        await chan.event({"type": "route", "turn_id": turn_id, **prepared.route})

        sources.extend(prepared.sources)
        turn.kb_hits = [f"{sec.doc} — {sec.heading}" for _, sec in prepared.hits]
        if prepared.hits:
            await chan.event({"type": "knowledge", "turn_id": turn_id,
                              "sections": turn.kb_hits, "t": turn.now(),
                              "excerpts": [{"doc": sec.doc, "heading": sec.heading,
                                            "text": first_sentences(sec.body)}
                                           for _, sec in prepared.hits[:2]]})

        if prepared.thinker is not None:
            await chan.event({"type": "thinking", "turn_id": turn_id,
                              "model": prepared.route["thinker"],
                              "t": max(0.0, prepared.started - turn.t0)})

        convo = prepared.convo
        last.update(convo=convo, model=prepared.route["model"])
        calls = []
        async for kind, payload in prepared.talk.read():
            if kind == "text":
                await mark_first()
                await emit(payload)
            elif kind == "tool_start":
                await mark_first()
            elif kind == "tool":
                calls.append(payload)
        turn.meter.add_usage(prepared.route["model"], prepared.stats)

        if prepared.thinker is not None:
            answer = ""
            try:
                async for text in prepared.thinker.read():
                    answer = text
            except Exception as exc:
                log_error(exc, "reasoning model")
            turn.think_s = time.perf_counter() - prepared.started
            turn.meter.add_usage(prepared.route["thinker"], prepared.think_stats)
            await chan.event({"type": "thought", "turn_id": turn_id, "t": turn.now(),
                              "took_s": turn.think_s, "model": prepared.route["thinker"],
                              "ok": bool(answer)})
            if answer:
                await emit(" " + answer)
            else:
                # The reasoning model failed: answer with the talker instead of
                # leaving the caller with only a lead-in.
                async for kind, payload in stream_tools(
                        llm, convo + [{"role": "system", "content":
                                       "Now give the answer itself, in two or three spoken sentences."}],
                        model=prepared.route["model"]):
                    if kind == "text":
                        await emit(payload)

        # An order lookup is local and instant, so it needs no holding phrase
        # and no second round trip to decide whether to make one.
        looked = [c for c in calls if c.get("name") == "lookup_order"]
        if looked:
            try:
                asked = (json.loads(looked[0]["arguments"]) or {}).get("order_number", "")
            except ValueError:
                asked = ""
            number = orders.find_number(asked) or orders.find_number(prompt)
            # It sometimes calls the tool before asking for a number. Say
            # nothing rather than reporting a lookup that never happened.
            record = orders.look_up(number) if number else None
            if record:
                turn.order = record["number"] if record.get("found") else None
                await chan.event({
                    "type": "order", "turn_id": turn_id, "t": turn.now(),
                    "number": record.get("number"), "found": record.get("found"),
                    "status": record.get("status"),
                    "days_late": record.get("days_late"),
                    "counts_as_lost": record.get("counts_as_lost"),
                    "items": record.get("items"), "paid": record.get("paid"),
                    "carrier": record.get("carrier"), "tracking": record.get("tracking"),
                    "last_scan": record.get("last_scan"), "promised": record.get("promised"),
                    "delivered_on": record.get("delivered_on"),
                })
                detail = ("Order lookup result. This is the real record — answer "
                          "from it and do not add anything it does not say.\n\n"
                          + orders.describe(record))
                sources.append(orders.describe(record))
                notes.append(f"Earlier order lookup ({record.get('number')}):\n"
                             + orders.describe(record))
                applies = [(sc, sec) for sc, sec in order_policy(record)
                           if f"{sec.doc} — {sec.heading}" not in turn.kb_hits]
                if applies:
                    policy_text = kb_context(applies)
                    sources.append(policy_text)
                    detail += ("\n\nThe policy that applies to this order:\n\n" + policy_text)
                    turn.kb_hits += [f"{sec.doc} — {sec.heading}" for _, sec in applies]

                # Whose order it is decides whose memory this is — once they
                # confirm the email on it.
                email = (orders.ORDERS.get(record.get("number") or "", {}) or {}).get("email")
                c = session.caller
                if record.get("found") and email and not (c and c["verified"] and c["email"] == email):
                    session.caller = {"email": email.lower(), "order": record["number"],
                                      "verified": False, "name": None}
                    await chan.event(caller_event(session.caller, "pending"))
                    detail += ("\n\nTo pull up their account, ask them to confirm the email "
                               "address on this order. Never say the email yourself.")
            else:
                detail = ("You called the order lookup without a number. Ask the "
                          "caller for their order number — do not claim to have "
                          "checked anything.")
            convo = convo + [{"role": "system", "content": detail}]
            last.update(convo=convo)
            follow = {}
            gate.boundary()
            # No tools on this round. It was offered web_search, and tool
            # calls made here are never collected — `calls` only holds what
            # the first stream returned — so when it answered the lookup with
            # another tool call instead of words, the call was dropped and
            # the caller got an order card, a cost, a timing, and silence.
            # The record is already in front of it; there is nothing to look up.
            async for kind, payload in stream_tools(
                    llm, convo, model=prepared.route["model"], stats=follow,
                    temperature=prepared.temperature):
                if kind == "text":
                    await emit(payload)
            turn.meter.add_usage(prepared.route["model"], follow)

        acted = [c for c in calls if c.get("name") == "resolve_order"]
        if acted:
            detail = await carry_out(acted[0], prompt, session, turn, turn_id, chan, sources, notes)
            convo = convo + [{"role": "system", "content": detail}]
            last.update(convo=convo)
            follow = {}
            gate.boundary()
            async for kind, payload in stream_tools(llm, convo, model=prepared.route["model"],
                                                    stats=follow,
                                                    temperature=prepared.temperature):
                if kind == "text":
                    await emit(payload)
            turn.meter.add_usage(prepared.route["model"], follow)

        moved = [c for c in calls if c.get("name") == "transfer_to_specialist"]
        if moved and session.persona == "front":
            try:
                args = json.loads(moved[0]["arguments"]) or {}
            except ValueError:
                args = {}
            team = args.get("team") if args.get("team") in PERSONAS else "billing"
            session.handoff = {"team": team, "reason": args.get("reason") or prompt}
            who_next = PERSONAS[team]
            await emit(handoff_line(code, team))

        searched = [c for c in calls if c.get("name") == "web_search"]
        if searched:
            try:
                query = (json.loads(searched[0]["arguments"]) or {}).get("query", "").strip()
            except ValueError:
                query = ""
            if query:
                await chan.event({"type": "searching", "turn_id": turn_id,
                                  "query": query, "t": turn.now()})

                # Start talking before the search returns. This text goes into
                # the same synthesis context as the answer that follows it.
                spoken = bridge_phrase(query, already_spoke=bool(attachments or prelook),
                                       code=code)
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
                if results.get("results") or results.get("answer"):
                    sources.append(as_context(results)[:3000])
                    notes.append(f"Earlier web search for \"{query}\":\n"
                                 + as_context(results)[:1200])

                grounded = convo + [
                    {"role": "assistant", "content": spoken},
                    {"role": "system", "content": grounded_prompt(results)},
                ]
                last.update(convo=grounded)
                follow = {}
                gate.boundary()
                async for kind, payload in stream_tools(llm, grounded, model=prepared.route["model"],
                                                        stats=follow,
                                                        temperature=prepared.temperature):
                    if kind == "text":
                        await emit(payload)
                turn.meter.add_usage(prepared.route["model"], follow)

    async def generate():
        try:
            if interpreting:
                await generate_interpretation()
            else:
                await generate_answer()
                # Every tool round above emits only on "text", so a round that
                # comes back as another tool call emits nothing at all — and a
                # turn can finish having said not one word, which reaches the
                # caller as an order card and silence. Whatever the loop did,
                # answer before closing. No tools this time, so it cannot
                # happen twice.
                if not said_anything["yes"] and last.get("convo"):
                    print(f"  ~ turn {turn_id} said nothing after its tools; answering again")
                    async for kind, payload in stream_tools(
                            llm, last["convo"] + [{"role": "system", "content": FINALLY_RULE}],
                            model=last.get("model") or opts["model"],
                            temperature=sampling_for(code)):
                        if kind == "text":
                            await emit(payload)
            await gate.close()
            if held_any and last.get("convo") and not interpreting:
                # Try once more from the records, checked the same way, rather
                # than leave the caller with only "let me double-check".
                first_round = list(held_any)
                held_any.clear()
                retry = Gate(llm, sources, release, on_held, enabled=opts["check"],
                             turn=turn, asked=prompt)
                said_so_far = reply.strip() or "(nothing yet)"
                listing = "\n".join(f"- {s} ({why})" for s, why in first_round)
                again = last["convo"] + [
                    {"role": "assistant", "content": said_so_far},
                    {"role": "system", "content": RETRY_RULE.format(held=listing)},
                ]
                follow = {}
                try:
                    gate.boundary()
                    # Not prepared.temperature: that is a local of
                    # generate_answer and this is a sibling function, so
                    # reaching for it raised NameError, the except below
                    # swallowed it into a log line, and every rewrite silently
                    # did nothing for a day.
                    async for kind, payload in stream_tools(llm, again, model=last["model"],
                                                            stats=follow,
                                                            temperature=sampling_for(code)):
                        if kind == "text":
                            await retry.feed(payload)
                    await retry.close()
                except Exception as exc:
                    retry.cancel()
                    log_error(exc, "rewriting held sentences")
                    held_any.append(("", "rewrite failed"))
                turn.meter.add_usage(last["model"], follow)
                await chan.event({"type": "recovered", "turn_id": turn_id,
                                  "ok": not held_any, "held": len(first_round)})
            if held_any:
                # Still unsupported after a rewrite: say so rather than leave a gap.
                await release(REPAIR.get(code, REPAIR["en"]))
            if turn.checks["checked"]:
                await chan.event({"type": "checked", "turn_id": turn_id, **turn.checks})
        finally:
            turn.last_token = turn.now()
            await chan.event({"type": "text_done", "turn_id": turn_id, "t": turn.last_token})
            await pending_text.put(None)

    async def synthesize():
        if conn is not None and not session.audio_off and voice_id is None:
            # No native voice for this language, and a foreign one is never
            # used instead: this reply is read, not heard, and the page says
            # why on the turn.
            await chan.event({"type": "no_voice", "turn_id": turn_id,
                              "language": code,
                              "message": f"No {languages.name(code)} voice is set up, "
                                         "so this reply is text only."})
        if conn is None or session.audio_off or voice_id is None:
            while await pending_text.get() is not None:
                pass                      # consume so generate() is never blocked
            return
        async with TTS_SLOTS:
            turn.tts_start = turn.now()
            await chan.event({"type": "tts_start", "turn_id": turn_id, "t": turn.tts_start})

            sent = []
            state = {"finished": False}

            async def attempt(controls, model_id=None):
                ctx = conn.context(
                    model_id=model_id or tts_model_for(code),
                    voice=voice_spec(voice_id, controls),
                    output_format=output_format(),
                    language=code,
                )

                async def pump():
                    pushed = False
                    for piece in list(sent):        # replay after a retry
                        await ctx.push(piece)
                        pushed = True
                    while not state["finished"]:
                        delta = await pending_text.get()
                        if delta is None:
                            state["finished"] = True
                            break
                        sent.append(delta)
                        turn.meter.chars += len(delta)
                        await ctx.push(delta)
                        pushed = True
                    if not pushed:
                        # Closing an empty context makes Cartesia answer "No
                        # valid transcripts passed", and because synthesis and
                        # generation are gathered together that error is the
                        # one the caller sees — in place of whatever actually
                        # stopped the answer being written. There is nothing
                        # to say, so say nothing and let the real failure
                        # through. Raising rather than returning because
                        # drain() is waiting on a context that will now never
                        # be closed.
                        state["finished"] = True
                        raise NothingToSay()
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

                tasks = [asyncio.ensure_future(pump()), asyncio.ensure_future(drain())]
                try:
                    await asyncio.gather(*tasks)
                except NothingToSay:
                    for t in tasks:
                        t.cancel()
                    return
                except asyncio.CancelledError:
                    for t in tasks:
                        t.cancel()
                    raise
                except BaseException:
                    for t in tasks:
                        t.cancel()
                    raise

            # Pacing for every language, emotion only where it is wanted.
            controls = (smarts.voice_controls(
                            turn.mood, code,
                            emotion_ok=session.emotion_ok.get(code, True))
                        if session.controls_ok and not interpreting else None)
            try:
                await attempt(controls)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if turn.first_audio is not None:
                    raise
                # Cartesia dropped Italian and Dutch from sonic-2 without the
                # model id changing, so the first anyone knew was a dead turn.
                # Take the hint and finish the sentence on sonic-3.
                if note_tts_failure(code, exc):
                    await attempt(controls, FALLBACK_TTS_MODEL)
                elif out_of_credit(exc):
                    # The connection opened fine and the credits ran out
                    # part way through the call, so the "no synthesis this
                    # session" path above never got a chance to fire and the
                    # turn died instead. The answer is already written; the
                    # caller should get to read it.
                    log_error(exc, "synthesis credits exhausted; text only now")
                    session.audio_off = True
                    await chan.event({"type": "audio_off", "turn_id": turn_id,
                                      "message": safe_error(exc, "speech")})
                elif controls:
                    # One language refusing emotion used to switch the whole
                    # session back to a flat voice, including the languages
                    # that were perfectly happy with it. Drop it for this
                    # language only and keep the pacing, which no model has
                    # ever refused.
                    log_error(exc, f"voice controls refused for {code}; "
                                   "pacing only from here")
                    session.emotion_ok[code] = False
                    again = smarts.voice_controls(turn.mood, code, emotion_ok=False)
                    if again == controls:
                        session.controls_ok = False
                        again = None
                    await attempt(again)
                else:
                    raise

    def remember(text):
        if interpreting:
            return
        if not intro:
            session.history.append({"role": "user", "content": prompt})
        for note in notes:
            session.history.append({"role": "system", "content": note})
        said_msg = {"role": "assistant", "content": text}
        session.history.append(said_msg)
        session.user_turns += 1
        session.said[turn_id] = (said_msg, reply)
        for old in [k for k in session.said if k < turn_id - 20]:
            session.said.pop(old, None)
        del session.history[:-MAX_HISTORY]

    # One slot for the whole turn, held across every model call it makes.
    if LLM_SLOTS is not None:
        try:
            await asyncio.wait_for(LLM_SLOTS.acquire(), LLM_QUEUE_SECONDS)
        except asyncio.TimeoutError:
            # Saturated. Saying so is the point: a turn that quietly waits
            # for ever is the failure this exists to prevent.
            print(f"  ! turn {turn_id}: no model slot after "
                  f"{LLM_QUEUE_SECONDS}s, {MAX_LLM_AT_ONCE} already running")
            await chan.event({"type": "busy", "turn_id": turn_id,
                              "message": BUSY_SAYS.get(code, BUSY_SAYS["en"])})
            await chan.event({"type": "text_done", "turn_id": turn_id,
                              "t": turn.now()})
            return None
        held_slot = True
    else:
        held_slot = False

    try:
        await asyncio.gather(generate(), synthesize())
    except asyncio.CancelledError:
        # Talked over. Keep what they heard, so the next answer does not build
        # on sentences that never reached them — and free the synthesis slot,
        # which the browser stopping playback never did.
        remember(heard_part(reply, interrupt.get("heard_chars")))
        raise
    finally:
        if held_slot:
            LLM_SLOTS.release()
        gate.cancel()
        if draft is not None and draft.task.done() and not draft.task.cancelled() \
                and draft.task.exception() is None:
            draft.task.result().cancel()

    # Nothing to answer from: no documentation matched, no order was looked
    # up, no search ran. Either the knowledge base has a gap or this is not
    # a thing the agent should be asked, and both are only visible in
    # aggregate — so the question is kept with the call.
    if (prompt and not intro and not interpreting
            and not turn.kb_hits and turn.order is None and turn.search_s is None
            and prompt not in session.unanswered):
        session.unanswered.append(prompt)

    remember(reply)

    session.cost.merge(turn.meter)
    turn.cost_call = call_cost(session)
    await chan.event({
        "type": "done", "turn_id": turn_id, "reply": reply, "metrics": turn.metrics(),
    })

    # A transfer asked for during this turn: the specialist speaks next, in
    # their own voice, without waiting for the caller.
    if session.handoff and not intro:
        handoff, session.handoff = session.handoff, None
        session.persona = handoff["team"]
        who_next = PERSONAS[handoff["team"]]
        await chan.event({"type": "handoff", "turn_id": turn_id, "team": handoff["team"],
                          "name": who_next["name"], "label": who_next["team"],
                          "reason": handoff["reason"]})
        return {"handoff": handoff}
    return None


ORDER_POLICY_QUERY = {
    "lost": "order late lost replacement refund",
    "late": "order late delivery charge refunded",
    "delivered": "return window refund faulty",
    "returning": "how long refund takes return",
    "refunded": "how long refund takes",
    "cancelled": "how long refund takes",
    "preparing": "standard delivery dispatch",
    "in transit": "tracking delivery late",
}


def order_policy(record):
    """The policy sections that decide what can happen to this order next.

    The question itself ("where's my order?") rarely names the policy, so the
    lookup brings it, and the model and the fact-checker both see it.
    """
    if not record or not record.get("found"):
        return []
    if record.get("counts_as_lost"):
        key = "lost"
    elif record.get("counts_as_late"):
        key = "late"
    else:
        key = record.get("status") or "in transit"
    query = ORDER_POLICY_QUERY.get(key) or ORDER_POLICY_QUERY["in transit"]
    return KB.find(query, k=2)


def call_cost(session):
    total = session.cost.summary()
    total["human_usd"] = pricing.human_cost()
    return total


def first_sentences(body, limit=170):
    text = " ".join(body.split())
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "))
    return (cut[:end + 1] if end > 60 else cut.rstrip() + ("…" if len(text) > limit else "")).strip()


ACTION_PREFIX = {"replacement": "RP", "refund": "RF", "return_label": "RL"}
ACTION_SAYS = {"replacement": "replacement sent", "refund": "refund issued",
               "return_label": "returns label emailed"}


async def carry_out(call, prompt, session, turn, turn_id, chan, sources, notes):
    """Do what was agreed — mocked, like the order lookup — and say what was done."""
    try:
        args = json.loads(call.get("arguments") or "{}") or {}
    except ValueError:
        args = {}
    action = args.get("action") if args.get("action") in ACTION_PREFIX else None
    number = (orders.find_number(args.get("order_number", "")) or orders.find_number(prompt)
              or (session.caller or {}).get("order"))
    record = orders.look_up(number) if number else None
    if not action or not record or not record.get("found"):
        return ("The action could not be carried out: no valid order or action. Tell the "
                "caller you could not do it yet and ask for the order number.")
    if record.get("status") in ("refunded", "cancelled"):
        return (f"Order {number} is already {record['status']}; nothing more can be done to it. "
                "Tell the caller that plainly.")
    ref = f"{ACTION_PREFIX[action]}-{number}-{secrets.token_hex(2).upper()}"
    items = ", ".join(i["name"] for i in record.get("items", []))
    email = (orders.ORDERS.get(number, {}) or {}).get("email")
    done = {"kind": action, "ref": ref, "order": number, "items": items,
            "amount": record.get("paid"), "email": memory.mask(email) if email else None}
    session.actions.append(done)
    await chan.event({"type": "action", "turn_id": turn_id, "t": turn.now(), **done})
    said = (f"Action completed: {ACTION_SAYS[action]} for order {number} ({items}), "
            f"reference {ref}.")
    if action == "refund":
        said += f" Amount £{record.get('paid', 0):.2f}, to the original payment method."
    if action == "return_label" and email:
        said += " Sent to the email address on the order."
    sources.append(said)
    notes.append(said)
    return (said + "\nTell the caller it is done and read the reference out in short groups. "
            "Say only what is stated here or in the documents about what happens next — no "
            "dates or timings that are not written down.")


# ================================================================ the socket

async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=MAX_MSG_BYTES)
    await ws.prepare(request)

    llm = get_llm()
    tts = get_tts()
    session = Session()
    session.key = caller_key(request)
    # With Auth0 on, whoever signed in is the caller from the first word:
    # their record, their history, no code. The session's user came from
    # oauth2-proxy at sign-in, never from a header.
    held = session_for(request) or {}
    if AUTH_PROXY and held.get("user"):
        session.signed_in = held["user"]
        session.caller = signed_in_caller(session)
    chan = Channel(ws)
    running = {}       # turn_id -> task
    next_turn_id = 1
    # Looks started while the caller was still talking, keyed by the id the
    # browser gave them.
    prelooks = {}
    interrupts = {}    # turn_id -> {"heard_chars": n} set on barge-in
    opened_at = time.perf_counter()

    # Losing synthesis should cost the demo its voice, not its existence. The
    # conversation, vision and search all still work; the caller reads instead
    # of listening.
    t_setup = time.perf_counter()
    manager = tts.tts.websocket_connect()
    conn = None
    audio_trouble = None
    if TEXT_ONLY:
        print("  TEXT_ONLY is set — answering without synthesis")
    else:
        try:
            conn = await manager.__aenter__()
        except Exception as exc:
            log_error(exc, "no synthesis this session")
            audio_trouble = safe_error(exc, "speech")
    setup_ms = (time.perf_counter() - t_setup) * 1000

    # Through the channel like everything else. This one sits immediately
    # after the TTS connection, the slowest part of setting a call up, so it
    # is the write most likely to find the tab already gone.
    await chan.event(
        {
            "type": "ready",
            "socket_setup_ms": setup_ms,
            "llm_model": get_model(),
            "tts_model": tts_model_for("en"),
            "sample_rate": SAMPLE_RATE,
            "models": CATALOG["models"],
            "voices": native_voices(),
            "default_voice": voices.for_role("front", "en"),
            "max_concurrent": MAX_CONCURRENT,
            "max_turns": MAX_TURNS,
            "max_minutes": MAX_MINUTES,
            "summary_model": SUMMARY_MODEL,
            "check_model": CHECK_MODEL,
            "think_model": smarts.think_model(CATALOG["models"]),
            "quick_model": smarts.quick_model(CATALOG["models"], get_model()),
            "search_provider": search_provider(),
            "vision_model": get_vision_model(),
            "max_frames": MAX_FRAMES,
            "human_cost": pricing.human_cost(),
            "audio": conn is not None,
            "audio_trouble": (
                f"{audio_trouble} Replies are text only until then."
                if audio_trouble else None
            ),
        }
    )
    if ws.closed:
        # They closed the tab while synthesis was connecting. Nothing below
        # has started yet, so there is nothing to unwind.
        await tts.close()
        return ws
    if session.signed_in:
        await chan.event(caller_event(session.caller, "verified"))

    def drop_drafts(keep=None):
        for did in [d for d in session.drafts if d != keep]:
            session.drafts.pop(did).cancel()

    async def supervise(turn_id, coro, opts=None):
        nonlocal next_turn_id
        try:
            outcome = await coro
            if outcome and outcome.get("handoff") and opts is not None:
                # The specialist's first words are a turn of their own: it can
                # be interrupted, timed and replayed like any other.
                intro_id = next_turn_id
                next_turn_id += 1
                interrupts[intro_id] = {}
                intro_opts = dict(opts, attachments=[], on_call=opts["on_call"])
                running[intro_id] = asyncio.ensure_future(supervise(intro_id, run_turn(
                    chan, conn, llm, session, intro_id, "", intro_opts,
                    interrupts[intro_id], intro=outcome["handoff"])))
        except asyncio.CancelledError:
            await chan.event({"type": "cancelled", "turn_id": turn_id})
            raise
        except Exception as exc:
            log_error(exc, f"turn {turn_id}")
            await chan.event({"type": "error", "turn_id": turn_id,
                              "message": safe_error(exc, "that request")})
        finally:
            running.pop(turn_id, None)
            interrupts.pop(turn_id, None)
            await chan.event({"type": "idle", "turn_id": turn_id, "running": len(running)})

    def over_limit():
        minutes = (time.perf_counter() - opened_at) / 60
        return session.turns_used >= MAX_TURNS or minutes >= MAX_MINUTES

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue


            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            if not isinstance(kind, str):
                continue

            # One bad frame used to end the call. Only the JSON parse was
            # guarded, so a payload that was not an object, a text field
            # that was not a string, or an id that could not be a dict key
            # raised straight out of this loop and took the socket with
            # it: the caller lost the call and saw nothing, while the
            # traceback went to a log nobody was watching. Anything a
            # message gets wrong now costs that message and no more.
            try:

                if kind == "prelook":
                    # The camera (or shared screen) already shows whatever they mean,
                    # so start reading it now rather than when they stop speaking.
                    pid = data.get("id")
                    frames, missed = inspect_attachments(data.get("images"))
                    if missed:
                        await chan.event({"type": "attachments_dropped",
                                          "turn_id": 0, "reasons": missed})
                    if pid is None or not frames:
                        continue
                    for old in [k for k in prelooks if k < pid - 2]:
                        task = prelooks.pop(old)["task"]
                        task.cancel()
                    look = SCREEN_LOOK if data.get("source") == "screen" else GENERIC_LOOK
                    prelooks[pid] = {
                        "count": len(frames),
                        "task": asyncio.ensure_future(describe_images(llm, frames, look)),
                    }
                    continue

                if kind == "draft":
                    # The caller has paused mid-sentence (or stopped typing). Start
                    # answering what they have said so far; if that turns out to be
                    # the whole question, the answer is already on its way.
                    text = clean_up(chan, said_text(data))
                    did = data.get("id")
                    if not text or did is None or over_limit() or len(running) >= MAX_CONCURRENT:
                        continue
                    opts = read_opts(data)
                    if opts["attachments"] or opts["mode"] == "interpret" or data.get("prelook"):
                        continue
                    if session.caller and not session.caller["verified"] and memory.find_email(text):
                        continue        # verifying changes the prompt; do it live
                    drop_drafts()
                    code = resolve_language(session, text, opts["language"])
                    session.drafts[did] = Draft(
                        text, asyncio.ensure_future(prepare(llm, session, text, opts, code)),
                        len(session.history), opts_key(opts), code)
                    continue

                if kind == "prompt":
                    # next_turn_id, not turns_used: drafts and cancelled turns
                    # make them diverge, and the browser matches this against the
                    # turn_start that follows.
                    prompt = clean_up(chan, said_text(data),
                                      tell=True, turn_id=next_turn_id)
                    opts_preview = read_opts(data)
                    if opts_preview["dropped_images"]:
                        # Say which pictures did not make it, before answering
                        # about the ones that did.
                        await chan.event({
                            "type": "attachments_dropped",
                            "turn_id": next_turn_id,
                            "reasons": opts_preview["dropped_images"],
                        })
                    if not prompt:
                        continue

                    if over_limit():
                        drop_drafts()
                        await chan.event({
                            "type": "spent",
                            "message": (
                                f"This session has reached its limit of {MAX_TURNS} "
                                f"questions." if session.turns_used >= MAX_TURNS else
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

                    opts = read_opts(data)
                    draft = session.drafts.pop(data.get("draft"), None) if data.get("draft") is not None else None
                    drop_drafts()
                    if draft is not None:
                        code = resolve_language(session, prompt, opts["language"])
                        if not draft.matches(prompt, session, opts_key(opts), code) \
                                or opts["mode"] == "interpret":
                            draft.cancel()
                            draft = None

                    turn_id = next_turn_id
                    next_turn_id += 1
                    session.turns_used += 1
                    interrupts[turn_id] = {}
                    coro = run_turn(
                        chan, conn, llm, session, turn_id, prompt, opts,
                        interrupts[turn_id], draft=draft,
                        prelook=prelooks.pop(data["prelook"], None)
                        if data.get("prelook") in prelooks else None,
                    )
                    running[turn_id] = asyncio.ensure_future(supervise(turn_id, coro, opts))

                elif kind == "cancel":
                    # The caller talked over this turn, or pressed stop.
                    try:
                        tid = int(data.get("turn_id"))
                    except (TypeError, ValueError):
                        continue
                    heard = data.get("heard_chars")
                    heard = int(heard) if isinstance(heard, (int, float)) else None
                    task = running.get(tid)
                    if task is not None and not task.done():
                        interrupts.setdefault(tid, {})["heard_chars"] = heard
                        task.cancel()
                    elif tid in session.said and heard is not None:
                        # Finished generating, but playback was cut short.
                        msg_, full = session.said[tid]
                        msg_["content"] = heard_part(full, heard)
                    continue

                elif kind == "wrapup":
                    try:
                        note = await write_up(llm, session.history)
                    except Exception as exc:
                        log_error(exc, "call write-up")
                        note = None
                    saved = None
                    if note:
                        session.cost.add(SUMMARY_MODEL, note.get("prompt_tokens") or 0,
                                         note.get("completion_tokens") or 0)
                        try:
                            saved = await remember_call(llm, session, note)
                        except Exception as exc:
                            log_error(exc, "saving to memory")
                    await chan.event({"type": "wrapup", "note": note,
                                      "remembered": bool(saved),
                                      "cost_call": call_cost(session),
                                      "actions": session.actions,
                                      "caller": session.caller.get("name") if session.caller else None})
                    if saved and session.caller:
                        await chan.event(caller_event(session.caller, "verified"))
                    continue

                elif kind == "memory_reset":
                    memory.reset()
                    session.caller = None
                    session.saved_turns = session.user_turns
                    await chan.event({"type": "memory", "reset": True})
                    continue

                elif kind == "say":
                    if conn is None:
                        continue
                    # The assistant's own "which would you like first?" question.
                    text = said_text(data)
                    if not text:
                        continue
                    spoken = session.speaking["code"]
                    voice = voice_for(session, spoken, data.get("voice_id"))
                    if voice is None:
                        continue          # no native voice: the text is already on screen
                    try:
                        await chan.event({"type": "say_start", "turn_id": 0})
                        await synthesise(chan, conn, 0, text, voice, language=spoken)
                        await chan.event({"type": "say_done", "turn_id": 0})
                    except Exception as exc:
                        log_error(exc, "spoken prompt")
                        await chan.event({"type": "error", "turn_id": 0,
                                          "message": safe_error(exc, "speaking")})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_error(exc, f"handling a {kind!r} message")
                continue
    finally:
        drop_drafts()
        for entry in prelooks.values():
            entry["task"].cancel()
        for task in list(running.values()):
            task.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
        # A refresh or a closed tab ends the call as surely as hanging up
        # does: write it to the caller's history so the next call remembers
        # it. Not awaited — summarising a call is a round trip to the model,
        # and it was happening in the teardown of a socket whose owner had
        # already gone, holding the handler open for seconds at a time.
        in_background(remember_call(llm, session), "saving the call to memory")
        if conn is not None:
            try:
                await manager.__aexit__(None, None, None)
            except Exception:
                pass
        await tts.close()

    return ws


async def index(request):
    return web.FileResponse(os.path.join(STATIC, "index.html"))


# A very small amount of markdown, because the policies are written as text
# and a dependency to render them would be a dependency to review.
def as_html(text, title):
    out, para = [], []

    def flush():
        # The source is wrapped for reading, so consecutive lines are one
        # paragraph. Emitting one <p> per line broke every sentence in three.
        if para:
            out.append(f"<p>{bold(escape(' '.join(para)))}</p>")
            para.clear()

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            out.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("# "):
            flush()
            out.append(f"<h1>{escape(line[2:])}</h1>")
        elif line.startswith("- "):
            flush()
            out.append(f"<li>{bold(escape(line[2:]))}</li>")
        elif line.strip():
            para.append(line.strip())
        else:
            flush()
    flush()
    body = "\n".join(out).replace("</li>\n<li>", "</li><li>")
    body = re.sub(r"(<li>.*?</li>)", r"<ul>\1</ul>", body, flags=re.S)
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{escape(title)} — VoxNova</title>"
            f"<link rel=\"stylesheet\" href=\"/static/style.css\"></head>"
            f"<body class=\"policy\"><main class=\"policysheet\">{body}"
            f"<p class=\"policyback\"><a href=\"/login\">Back</a></p>"
            f"</main></body></html>")


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def bold(text):
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


async def terms(request):
    return web.Response(text=as_html(policies.terms_text(), "Terms of Use"),
                        content_type="text/html")


async def privacy(request):
    return web.Response(text=as_html(policies.privacy_text(), "Privacy Notice"),
                        content_type="text/html")


# ------------------------------------------------------------------ access

# Only our own ingress may claim who the client is.
#
# rke2-ingress-nginx runs with use-forwarded-headers=true, which means it
# *appends* the peer it actually saw to whatever X-Forwarded-For the client
# sent. So the header arriving here looks like:
#
#     <anything the client made up>, <the address nginx really saw>
#
# The trustworthy entry is the rightmost one, not the first. Reading the
# first — which is what this did — means a client can put any address it
# likes at the front and get a different rate-limit identity for every
# request, which makes three-attempts-then-locked meaningless.
#
# The general rule is "rightmost address that is not itself one of ours":
# walk from the right, skip our own proxies, take the first one left.
TRUSTED_PROXIES = tuple(
    p.strip() for p in (os.getenv("TRUSTED_PROXIES") or "").split(",")
    if p.strip())


def _networks():
    """TRUSTED_PROXIES as networks. A bare address counts as a /32."""
    out = []
    for entry in TRUSTED_PROXIES:
        try:
            out.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            print(f"  ! TRUSTED_PROXIES: {entry!r} is not an address or a "
                  "CIDR and is ignored")
    return tuple(out)


TRUSTED_NETWORKS = _networks()


def _is_ours(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in TRUSTED_NETWORKS)


def client_ip(request):
    """The caller's address, as far as it can be trusted.

    Returns the peer unless that peer is one of ours, in which case the
    rightmost address in X-Forwarded-For that is not also one of ours.
    """
    peer = request.remote or ""
    if not TRUSTED_NETWORKS or not _is_ours(peer):
        # Not behind a proxy we know, so the header is just something the
        # client sent and is ignored entirely.
        return peer
    chain = [part.strip() for part
             in request.headers.get("X-Forwarded-For", "").split(",")
             if part.strip()]
    for address in reversed(chain):
        if not _is_ours(address):
            return address
    # Everything in the chain is ours, or there was no chain.
    return chain[-1] if chain else peer


# Signed in through Auth0 at the ingress (oauth2-proxy), set by the chart
# when auth.enabled. The passphrase is then not asked for; consent still is.
AUTH_PROXY = os.getenv("AUTH_PROXY", "").lower() in ("1", "true", "yes", "on")
# The passphrase field and its sentence on the login page, marked there.
PASSPHRASE_PARTS = re.compile(r"<!-- passphrase.*?<!-- /passphrase -->", re.S)


# Where to ask oauth2-proxy who a caller is. The chart points it at the
# proxy's Service inside the cluster.
AUTH_CHECK_URL = os.getenv("AUTH_CHECK_URL", "")


async def verified_user(request):
    """The Auth0 user this caller really is, asked of oauth2-proxy, or None.

    No header in the request is ever believed, X-Auth-Request-Email
    included, whoever sent it. nginx does set that header from the proxy's
    answer, but the NetworkPolicy cannot be relied on to keep everything
    else away from the pod: the ingress controller runs on the host cluster,
    out of reach of a selector here, and the pod network is all
    TRUSTED_PROXIES. So the caller's own oauth2 cookie is handed to the
    proxy, and only a 202 with an email counts. A request that went round
    the ingress has no such cookie, or a forged one the proxy rejects.
    """
    cookie = request.headers.get("Cookie")
    if not AUTH_PROXY or not AUTH_CHECK_URL or not cookie:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(AUTH_CHECK_URL, headers={"Cookie": cookie},
                                allow_redirects=False) as r:
                if r.status >= 500:
                    print(f"  ! oauth2-proxy answered {r.status}; treating the "
                          "caller as not signed in")
                if r.status != 202:
                    return None
                user = (r.headers.get("X-Auth-Request-Email") or "").strip().lower()
                return user or None
    except Exception as exc:
        # Shut, not open: no answer is no sign-in.
        print(f"  ! could not ask oauth2-proxy who this is ({type(exc).__name__})")
        return None


def caller_key(request):
    """Who is doing this, for the attempt and request limits.

    Session and address together. The session alone is one logout from
    being a different person; the address alone is a shared office. With
    Auth0 it is the signed-in user, which signing in again cannot reset.
    """
    held = session_for(request) or {}
    if held.get("user"):
        return hashlib.sha256(f"user|{held['user']}".encode()).hexdigest()[:32]
    token = request.cookies.get(COOKIE_NAME) or "-"
    return hashlib.sha256(
        f"{token}|{client_ip(request)}".encode()).hexdigest()[:32]


def is_https(request):
    """True when the visitor reached us over TLS, including via a tunnel."""
    return request.headers.get("X-Forwarded-Proto", request.scheme) == "https"


def session_for(request):
    """The live session behind this request, or None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    now = time.time()
    if len(SESSIONS) > 1:
        for old_token, held in list(SESSIONS.items()):
            if held["until"] <= now:
                SESSIONS.pop(old_token, None)
    held = SESSIONS.get(token)
    if held is None:
        return None
    if held["until"] <= now:
        SESSIONS.pop(token, None)
        return None
    return held


def consented(request):
    """Agreed, and to the wording currently being served.

    A new version is a new agreement: consent to text nobody can read any
    more is not consent to the text on the page now.
    """
    held = session_for(request)
    return bool(held) and held.get("policy") == policies.VERSION


def authorised(request):
    return session_for(request) is not None


@web.middleware
async def fresh_static(request, handler):
    """Never let the browser cache the app's own JS and CSS.

    aiohttp's static handler sends no cache headers, so browsers hold on to
    them — which during development means testing a fix against the previous
    version of the file and concluding it did not work.
    """
    response = await handler(request)
    # /login was missing here, so a blank response cached while the server was
    # down kept being served after it came back — a white page with no title,
    # on a server returning a perfectly good one.
    if request.path.startswith("/static") or request.path in (
            "/", "/login", "/terms", "/privacy"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@web.middleware
async def gate(request, handler):
    # /static holds no secrets and the login page needs the stylesheet.
    if (request.path.startswith("/static")
            or request.path in ("/login", "/terms", "/privacy")):
        return await handler(request)

    if authorised(request):
        # Signed in, but to older wording: ask again rather than carry on.
        if not consented(request):
            if request.path == "/ws":
                return web.Response(status=403, text="consent required")
            raise web.HTTPFound("/login?changed=1")
        return await handler(request)

    if request.path == "/ws":
        return web.Response(status=401, text="unauthorized")

    raise web.HTTPFound("/login")


async def login(request):
    if request.method == "GET":
        if AUTH_PROXY:
            # Auth0 has already said who this is; what is left is the consent.
            with open(os.path.join(STATIC, "login.html"), encoding="utf-8") as f:
                page = PASSPHRASE_PARTS.sub("", f.read())
            return web.Response(text=page, content_type="text/html")
        return web.FileResponse(os.path.join(STATIC, "login.html"))

    data = await request.post()
    user = None
    if AUTH_PROXY:
        # No passphrase: Auth0 is the gate, and the proxy, not any header,
        # says who signed in. Round the ingress, or with a made-up header,
        # there is no user and nothing is given.
        user = await verified_user(request)
        if not user:
            return web.Response(status=401, text="sign in through the site")
    else:
        given = (data.get("passphrase") or "").strip()
        # Constant-time compare so the response time leaks nothing about the value.
        if not hmac.compare_digest(given, PASSPHRASE):
            raise web.HTTPFound("/login?bad=1")

    # Checked here as well as in the page. The box is a convenience; this is
    # the part that cannot be skipped by posting the form directly.
    if (data.get("consent") or "").strip().lower() not in ("yes", "on", "true", "1"):
        raise web.HTTPFound("/login?noconsent=1")

    token = secrets.token_urlsafe(32)
    now = time.time()
    SESSIONS[token] = {
        "until": now + SESSION_HOURS * 3600,
        "consent_at": now,
        "policy": policies.VERSION,
        "user": user,
    }
    print(f"  consent recorded, policy {policies.VERSION}, "
          f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now))} UTC")

    response = web.HTTPFound("/")
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite="Lax",
        secure=is_https(request),
        max_age=int(SESSION_HOURS * 3600),
    )
    raise response


# How often the retention sweep runs. The period is not the policy: the
# policy is RETENTION_DAYS, and the sweep only decides how soon after a
# record ages out it actually goes.
SWEEP_HOURS = float(os.getenv("RETENTION_SWEEP_HOURS", "24"))


def sweep_once():
    """One pass, reported as counts. Never the addresses themselves — this
    line goes to the pod's stdout, where a deleted customer's email would
    outlive the customer."""
    swept = memory.purge_old()
    if swept["calls"] or swept["customers"] or swept["codes"]:
        print(f"  retention: {swept['calls']} call(s), "
              f"{swept['customers']} customer(s), {swept['codes']} code(s) "
              f"removed, older than {memory.RETENTION_DAYS:g} days")
    return swept


async def retention(app):
    """Sweep on startup and then every SWEEP_HOURS, for as long as we run.

    /privacy tells the caller their summary, name and email are deleted
    after RETENTION_DAYS. Nothing enforced that until this existed: the
    sweep was written and tested but never called, so the notice described
    something that did not happen.

    A failed sweep must not take the server with it — the pod would
    crash-loop on a full disk or a locked database and nobody could call
    at all, which is worse than history being a day late. So every pass is
    guarded and the loop carries on to the next one.
    """
    while True:
        try:
            await asyncio.to_thread(sweep_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(exc, "retention sweep")
        try:
            await asyncio.sleep(SWEEP_HOURS * 3600)
        except asyncio.CancelledError:
            raise


async def on_startup(app):
    global TTS_SLOTS, LLM_SLOTS
    TTS_SLOTS = asyncio.Semaphore(TTS_CONCURRENCY)
    LLM_SLOTS = asyncio.Semaphore(MAX_LLM_AT_ONCE)
    memory.init()
    app["retention"] = asyncio.create_task(retention(app))

    llm = get_llm()
    tts = get_tts()
    try:
        await load_catalog(llm, tts)
        await check_voices(tts)
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
    print(f"  {len(orders.ORDERS)} orders loaded from {os.path.basename(orders.PATH)}")
    print(f"  customer memory in {os.path.basename(memory.PATH)}")
    print(f"  {len(KB)} knowledge sections loaded"
          + ("" if len(KB) else " — answers about policy will be refused, not invented"))
    print(f"  keeping call history {memory.RETENTION_DAYS:g} days, swept every "
          f"{SWEEP_HOURS:g}h")


async def on_cleanup(app):
    """Stop the sweep before the loop closes, so shutdown is not a cancelled
    task warning on the way out."""
    task = app.get("retention")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log_error(exc, "stopping the retention sweep")


def main():
    for key in ("SAMBANOVA_API_KEY", "CARTESIA_API_KEY"):
        if not os.getenv(key):
            raise SystemExit(f"{key} is missing from .env")

    app = web.Application(middlewares=[fresh_static, gate])
    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    if os.getenv("EXPOSE_WHOAMI"):
        # For the proxy-trust test only, and off unless asked for.
        async def whoami(request):
            return web.Response(text=caller_key(request))
        app.router.add_get("/whoami", whoami)
    app.router.add_get("/terms", terms)
    app.router.add_get("/privacy", privacy)
    app.router.add_get("/voice-sample", voice_sample)
    app.router.add_post("/login", login)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    port = int(os.getenv("PORT", "8080"))
    # Loopback by default, so running it on a laptop does not put it on the
    # network. A container sets HOST=0.0.0.0 because the kubelet reaches it
    # from outside the pod's own namespace.
    host = os.getenv("HOST", "127.0.0.1")
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "") else host
    print(f"\n  listening on http://{shown}:{port}" + (f" (bound to {host})"
                                                        if host != "127.0.0.1" else ""))
    # Only ever print one we made up. A passphrase set in .env is a secret,
    # and in a container this line goes wherever the pod's stdout goes.
    if AUTH_PROXY:
        print("  sign-in: Auth0 at the ingress (no passphrase; consent still asked)")
    elif GENERATED_PASSPHRASE:
        print(f"  passphrase: {PASSPHRASE}")
        print("  (generated for this run — set DEMO_PASSPHRASE in .env to fix it)")
    else:
        print("  passphrase: set from DEMO_PASSPHRASE")
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
