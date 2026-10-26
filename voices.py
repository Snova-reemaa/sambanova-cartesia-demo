"""A native voice per language, and the language parameter to match.

One English voice was speaking all eight, which is why Spanish sounded
like an American reading Spanish. Cartesia's voices are recorded by native
speakers and carry the accent; the language parameter governs how the text
is pronounced. Both have to be right, and they are chosen together here so
they cannot drift apart.

Every id can be overridden from the environment — VOICE_ES, VOICE_FR and
so on — so a voice can be swapped after a listen without a release.
"""

import os

# Chosen for a support line: clear, even, unhurried. The account has
# several per language; these are a starting point to listen to, not a
# verdict.
NATIVE = {
    "en": ("Skylar", "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"),
    "es": ("Ximena", "3597a26f-80ef-4bd5-8101-9699bc764917"),
    "fr": ("Inès",   "7c58f4a4-a72c-42fa-a503-41b9408820f3"),
    "de": ("Clemens", "57a3a9e0-a91c-4c94-a2bb-e6cbab3ae649"),
    "it": ("Elena",  "00e9ec78-2002-41dd-8d19-6b1d3b17a461"),
    "pt": ("Helena", "8a6d0b8e-8cd8-4952-a41e-b7af18662135"),
    "ar": ("Reem",   "92f27ee5-d8b9-4c0a-a0c2-f401f6ab0a72"),
}

# Dutch has no voice on this account — Cartesia lists none for nl. A Dutch
# caller gets an English voice speaking Dutch, which is the thing this file
# exists to stop, so it is named here rather than left to be discovered.
# Either Cartesia adds one, or Dutch comes off the list of languages the
# demo claims to speak.
MISSING = ("nl",)
FALLBACK = "en"


def for_language(code):
    """(voice id, language to synthesise in). Never silently wrong."""
    override = os.getenv(f"VOICE_{(code or '').upper()}")
    if override:
        return override, code
    if code in NATIVE:
        return NATIVE[code][1], code
    # No native voice: use the fallback voice but keep the caller's
    # language, so the pronunciation is right even though the accent is
    # not. Better than reading Dutch with English pronunciation.
    return NATIVE[FALLBACK][1], code


def name_for(code):
    override = os.getenv(f"VOICE_{(code or '').upper()}")
    if override:
        return f"set by VOICE_{code.upper()}"
    return NATIVE.get(code, (f"{NATIVE[FALLBACK][0]} (no {code} voice)",))[0]


def report():
    rows = []
    for code in list(NATIVE) + list(MISSING):
        rows.append((code, name_for(code), for_language(code)[0][:8]))
    return rows


if __name__ == "__main__":
    for code, who, vid in report():
        flag = "  <- no native voice" if code in MISSING else ""
        print(f"  {code}  {who:28} {vid}…{flag}")
