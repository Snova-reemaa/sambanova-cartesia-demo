"""Record the same line, in each language, under each emotion, to listen to.

Whether emotion control does anything useful in a given language is a
listening judgement and nothing else. The API accepts the parameter for all
eight of ours, which only means it does not refuse. So: same sentence, same
voice, same model, one file per setting, and somebody plays them.

    RUN_LIVE_TESTS=1 venv/bin/python tests/make_emotion_samples.py
    RUN_LIVE_TESTS=1 venv/bin/python tests/make_emotion_samples.py ar de

Writes listening/emotion/<lang>/<setting>.wav. This spends Cartesia credit —
roughly 8 languages x 6 settings x one short line.
"""

import asyncio
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import paid_api_trouble                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "listening", "emotion")
RATE = 44100

# One line per language, same meaning: an apology and a promise, which is
# where a change in feeling should be audible if it is audible anywhere.
LINES = {
    "en": "I'm really sorry about that. Let me sort it out for you right now.",
    "es": "Siento mucho lo ocurrido. Déjame resolverlo ahora mismo.",
    "fr": "Je suis vraiment désolé. Je vais régler ça tout de suite.",
    "de": "Das tut mir wirklich leid. Ich kümmere mich sofort darum.",
    "it": "Mi dispiace davvero. Lo risolvo subito per te.",
    "pt": "Lamento imenso o sucedido. Vou resolver isso já.",
    "nl": "Het spijt me echt. Ik los dit meteen voor u op.",
    "ar": "أنا آسف جدًا على هذا. خلّيني أحلّها لك حالًا.",
}

# The four the agent may actually use, then two it never should — included
# only so you can hear whether the control is doing anything at all. If
# "angry" sounds identical to "neutral", the control is being ignored.
SETTINGS = {
    "1-neutral":   None,
    "2-calm":      {"speed": "slow", "emotion": ["positivity:low", "curiosity:low"]},
    "3-content":   {"speed": "normal", "emotion": ["positivity"]},
    "4-sad":       {"speed": "slow", "emotion": ["sadness"]},
    "5-check-angry":  {"speed": "normal", "emotion": ["anger"]},
    "6-check-scared": {"speed": "normal", "emotion": ["surprise:high"]},
}


def write_wav(path, pcm):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm)


async def main(only):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except Exception:
        pass
    if not os.getenv("RUN_LIVE_TESTS"):
        print("  this spends Cartesia credit — set RUN_LIVE_TESTS=1 to record")
        return 0
    if not os.getenv("CARTESIA_API_KEY"):
        print("  CARTESIA_API_KEY is not set")
        return 1

    from cartesia import AsyncCartesia
    model = os.getenv("CARTESIA_MODEL", "sonic-3.6")
    voice = os.getenv("CARTESIA_VOICE_ID")
    client = AsyncCartesia(api_key=os.environ["CARTESIA_API_KEY"])
    sizes = {}

    try:
        for code, line in LINES.items():
            if only and code not in only:
                continue
            for name, controls in SETTINGS.items():
                spec = {"mode": "id", "id": voice}
                if controls:
                    spec["__experimental_controls"] = controls
                socket = await client.tts.websocket()
                pcm = bytearray()
                note = ""
                try:
                    async for chunk in await socket.send(
                            model_id=model, transcript=line, voice=spec,
                            language=code,
                            output_format={"container": "raw",
                                           "encoding": "pcm_s16le",
                                           "sample_rate": RATE},
                            stream=True):
                        pcm += getattr(chunk, "audio", b"") or b""
                except Exception as exc:
                    trouble = paid_api_trouble(exc)
                    if trouble:
                        # Not a result about emotion. Stop, rather than fill
                        # the rest of the run with empty files that read
                        # exactly like "the control did nothing".
                        print(f"\n  !! STOPPED — {trouble}: {str(exc)[:90]}")
                        print("  !! Nothing recorded from here on. This says "
                              "nothing about emotion;")
                        print("  !! it says the account cannot be charged. "
                              "Top up and rerun.")
                        await socket.close()
                        return 2
                    note = f"  REFUSED: {str(exc)[:60]}"
                finally:
                    await socket.close()
                if pcm:
                    write_wav(os.path.join(OUT, code, f"{name}.wav"), bytes(pcm))
                sizes.setdefault(code, {})[name] = len(pcm)
                print(f"  {code}  {name:16} {len(pcm) / (RATE * 2):5.2f}s{note}")
    finally:
        await client.close()

    # If every setting is the same length to the byte, the control changed
    # nothing — worth saying out loud before anyone spends time listening.
    # A language that recorded nothing says nothing either way, and must not
    # be reported as "the control did nothing": the first run of this hit
    # Cartesia's credit limit half way down and six languages came back
    # empty, which reads identically to a control being ignored.
    print(f"\n  written to {OUT}")
    incomplete = []
    for code, by_name in sizes.items():
        lengths = [v for v in by_name.values() if v]
        if not lengths:
            incomplete.append(code)
            print(f"  {code}  nothing recorded — cannot say anything about it")
            continue
        if len(lengths) < len(SETTINGS):
            incomplete.append(code)
        same = len(set(lengths)) <= 1
        verdict = ("every setting identical in length — the control is doing "
                   "nothing measurable" if same else
                   "lengths differ, so the control is doing something")
        missing = (f" ({len(SETTINGS) - len(lengths)} of {len(SETTINGS)} missing)"
                   if len(lengths) < len(SETTINGS) else "")
        print(f"  {code}  {verdict}{missing}")
    if incomplete:
        print(f"\n  INCOMPLETE for {', '.join(incomplete)} — rerun those once the\n"
              "  Cartesia account has credit:  RUN_LIVE_TESTS=1 "
              f"venv/bin/python tests/make_emotion_samples.py {' '.join(incomplete)}")
    print("\n  Listen before trusting any of this: a different length only means\n"
          "  the audio changed, not that it changed for the better.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(set(sys.argv[1:]))))
