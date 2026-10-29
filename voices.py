"""A native voice for every language, for every person on the line.

The rule: a language is only ever spoken by a voice recorded by a native
speaker of it. One English voice reading all eight is why Spanish sounded
like an American reading Spanish, and a specialist picked from "any other
voice on the account" could as easily have been Finnish. Cartesia's voices
carry their speaker's accent; the language parameter only governs
pronunciation. Both have to be right, so they are chosen together here.

Each of the three people a caller can talk to (the main agent, Maya in
billing, Omar in technical support) has their own native voice in each
language, so a handoff sounds like a different person who still speaks
the caller's language natively. Where a role has none, it uses that
language's main voice. Nothing ever borrows another language's voice: a
language with no voice at all is answered in text.

Every id can be overridden from the environment, and from values.yaml:

    VOICE_<LANG>              the main agent, e.g. VOICE_NL
    VOICE_BILLING_<LANG>      Maya
    VOICE_TECHNICAL_<LANG>    Omar

Set to an empty string to say "no voice for this": the main voice for a
role, text only for a language. The server checks every configured id
against Cartesia at startup and refuses to start if any is not in its
language (check()).

Chosen from Cartesia's descriptions, then listened to and approved, all
24 (28 Oct 2026): listening/voices/ has a sample of each. Billing is a
woman's voice and technical a man's, to match Maya and Omar, and each
differs from the main voice.
"""

import os

LANGUAGES = ("en", "es", "fr", "de", "it", "pt", "nl", "ar")
ROLES = ("front", "billing", "technical")

VOICES = {
    "front": {
        "en": ("Skylar",    "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"),
        "es": ("Ximena",    "3597a26f-80ef-4bd5-8101-9699bc764917"),
        "fr": ("Inès",      "7c58f4a4-a72c-42fa-a503-41b9408820f3"),
        "de": ("Clemens",   "57a3a9e0-a91c-4c94-a2bb-e6cbab3ae649"),
        "it": ("Elena",     "00e9ec78-2002-41dd-8d19-6b1d3b17a461"),
        "pt": ("Helena",    "8a6d0b8e-8cd8-4952-a41e-b7af18662135"),
        "nl": ("Noa",       "96355f3d-0179-4c9a-a8d8-11ef0779a9b8"),
        "ar": ("Reem",      "92f27ee5-d8b9-4c0a-a0c2-f401f6ab0a72"),
    },
    "billing": {
        "en": ("Morgan",    "0ee8beaa-db49-4024-940d-c7ea09b590b3"),
        "es": ("Fernanda",  "b4b8e2af-6139-466e-a93a-30c20d2e1fc5"),
        "fr": ("Louise",    "e70cceed-576e-4fc1-9fc1-f1e137f15367"),
        "de": ("Emi",       "43a317e9-f1b9-45bf-bbdb-1d4a52e46f0d"),
        "it": ("Sofia",     "90c7d657-9599-4cd0-9ed2-2568359e4d1a"),
        "pt": ("Lorena",    "a5b0fceb-cc32-4eef-8929-421f55d32e16"),
        "nl": ("Anneliese", "225ba8cf-9fc2-4371-a78c-fe38ba38898a"),
        "ar": ("Fatima",    "731ace69-ee17-41bc-8c6f-665c9f1db95c"),
    },
    "technical": {
        "en": ("Grant",     "d46abd1d-2d02-43e8-819f-51fb652c1c61"),
        "es": ("Cesar",     "4b5112be-c461-44a2-a66b-0dd7f98db4a0"),
        "fr": ("Benoît",    "5def377d-908b-4540-8bd7-3c968fcae351"),
        "de": ("Lukas",     "e00dd3df-19e7-4cd4-827a-7ff6687b6954"),
        "it": ("Marco",     "79693aee-1207-4771-a01e-20c393c89e6f"),
        "pt": ("Joao",      "c8750946-6b70-4dbf-b760-8cc554e98a0c"),
        "nl": ("Thijs",     "95e9fdaf-cf0b-4739-b1de-3350ca50774a"),
        "ar": ("Khaled",    "b068aef8-2c18-4a1d-bafb-e8f6d2239d60"),
    },
}

# A word or two for the picker, from Cartesia's own description of each.
DESCRIBED = {
    "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4": "approachable",
    "3597a26f-80ef-4bd5-8101-9699bc764917": "steady, clear",
    "7c58f4a4-a72c-42fa-a503-41b9408820f3": "confident, articulate",
    "57a3a9e0-a91c-4c94-a2bb-e6cbab3ae649": "clear, articulate",
    "00e9ec78-2002-41dd-8d19-6b1d3b17a461": "composed",
    "8a6d0b8e-8cd8-4952-a41e-b7af18662135": "articulate, even",
    "96355f3d-0179-4c9a-a8d8-11ef0779a9b8": "soft, clear",
    "92f27ee5-d8b9-4c0a-a0c2-f401f6ab0a72": "assured, helpful",
    "0ee8beaa-db49-4024-940d-c7ea09b590b3": "polished",
    "b4b8e2af-6139-466e-a93a-30c20d2e1fc5": "approachable",
    "e70cceed-576e-4fc1-9fc1-f1e137f15367": "methodical, supportive",
    "43a317e9-f1b9-45bf-bbdb-1d4a52e46f0d": "calm",
    "90c7d657-9599-4cd0-9ed2-2568359e4d1a": "polished",
    "a5b0fceb-cc32-4eef-8929-421f55d32e16": "calm, articulate",
    "225ba8cf-9fc2-4371-a78c-fe38ba38898a": "clear, articulate",
    "731ace69-ee17-41bc-8c6f-665c9f1db95c": "polite, steady",
    "d46abd1d-2d02-43e8-819f-51fb652c1c61": "reliable, clear",
    "4b5112be-c461-44a2-a66b-0dd7f98db4a0": "steady, warm",
    "5def377d-908b-4540-8bd7-3c968fcae351": "clear, methodical",
    "e00dd3df-19e7-4cd4-827a-7ff6687b6954": "confident",
    "79693aee-1207-4771-a01e-20c393c89e6f": "friendly",
    "c8750946-6b70-4dbf-b760-8cc554e98a0c": "clear",
    "95e9fdaf-cf0b-4739-b1de-3350ca50774a": "clear, methodical",
    "b068aef8-2c18-4a1d-bafb-e8f6d2239d60": "composed",
}

# What a voice says when previewed, and in listening/voices/: one short
# support line per language, in that language.
SAMPLE = {
    "en": "Hello, you're through to support. I can see your order here — how can I help?",
    "es": "Hola, ha llamado a atención al cliente. Veo su pedido aquí. ¿En qué puedo ayudarle?",
    "fr": "Bonjour, vous êtes au service client. Je vois votre commande. Comment puis-je vous aider ?",
    "de": "Guten Tag, Sie sind beim Kundenservice. Ich sehe Ihre Bestellung. Wie kann ich helfen?",
    "it": "Buongiorno, è in contatto con l'assistenza. Vedo il suo ordine. Come posso aiutarla?",
    "pt": "Olá, ligou para o apoio ao cliente. Estou a ver a sua encomenda. Como posso ajudar?",
    "nl": "Hallo, u spreekt met de klantenservice. Ik zie uw bestelling. Waarmee kan ik helpen?",
    "ar": "مرحبًا، وصلت إلى خدمة العملاء. أرى طلبك هنا. كيف أقدر أساعدك؟",
}

# The two settings that came before this table set one voice for a
# specialist in every language. Honoured for English only, which is the
# only way they can be right.
LEGACY = {"billing": "VOICE_BILLING", "technical": "VOICE_TECHNICAL"}


def setting(role, code):
    """The environment variable that overrides this role in this language."""
    code = (code or "").upper()
    return f"VOICE_{code}" if role == "front" else f"VOICE_{role.upper()}_{code}"


def _own(role, code):
    """This role's own voice for this language, or None. Never another's."""
    override = os.getenv(setting(role, code))
    if override is None and code == "en" and role in LEGACY:
        override = os.getenv(LEGACY[role]) or None
    if override is not None:
        return override.strip() or None
    return VOICES.get(role, {}).get(code, (None, None))[1]


def for_role(role, code):
    """The voice for this person in this language, or None for text only.

    A role with no voice of its own uses the language's main voice. There
    is no further fallback, and that is the point.
    """
    if code not in LANGUAGES:
        return None
    role = role if role in ROLES else "front"
    return _own(role, code) or (_own("front", code) if role != "front" else None)


def for_language(code):
    """(main voice id or None, language to synthesise in)."""
    return for_role("front", code), code


def name_for(role, code):
    vid = for_role(role, code)
    if not vid:
        return "none (text only)"
    for r in (role, "front"):
        name, default = VOICES.get(r, {}).get(code, (None, None))
        if default == vid:
            return name
    return "set by " + setting(role, code)


def configured():
    """{(role, code): voice id} for every pair that has a voice."""
    return {(role, code): for_role(role, code)
            for role in ROLES for code in LANGUAGES if for_role(role, code)}


def same_language(cartesia_language, code):
    """Cartesia says "pt" or "pt-BR"; either is Portuguese."""
    return (cartesia_language or "").lower().split("-")[0].split("_")[0] == code


def check(language_of):
    """Every configured voice against what Cartesia says its language is.

    `language_of(voice_id)` returns the voice's language, or raises
    LookupError when Cartesia has no such voice. Returns a list of
    problems, one line each, naming the setting to change; empty is good.
    Anything else it raises (no network, a bad key) is left to the caller,
    because "could not check" is not "wrong".
    """
    problems = []
    for (role, code), vid in sorted(configured().items()):
        try:
            lang = language_of(vid)
        except LookupError:
            problems.append(f"{setting(role, code)}={vid}: Cartesia has no such voice")
            continue
        if not same_language(lang, code):
            problems.append(f"{setting(role, code)}={vid}: a {lang or 'unknown'} "
                            f"voice, not {code}")
    return problems


def report():
    return [(code, role, name_for(role, code), (for_role(role, code) or "")[:8])
            for code in LANGUAGES for role in ROLES]


if __name__ == "__main__":
    for code, role, who, vid in report():
        print(f"  {code}  {role:9}  {who:26} {vid}")
