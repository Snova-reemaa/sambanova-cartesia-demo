"""One short line per language, in each person's own voice in it.

For listening to, nothing more. Writes listening/voices/<lang>.wav for the
main agent and <lang>-billing.wav, <lang>-technical.wav for Maya and Omar.
Spends a little Cartesia credit: twenty-four short lines.
"""
import asyncio, os, sys, wave
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests.harness import paid_api_trouble                    # noqa: E402
import voices                                                 # noqa: E402

OUT = os.path.join(ROOT, "listening", "voices")
RATE = 44100
LINES = voices.SAMPLE

async def main(only):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except Exception:
        pass
    if not os.getenv("RUN_LIVE_TESTS"):
        print("  spends Cartesia credit — set RUN_LIVE_TESTS=1")
        return 0
    from cartesia import AsyncCartesia
    model = os.getenv("CARTESIA_MODEL", "sonic-3.6")
    client = AsyncCartesia(api_key=os.environ["CARTESIA_API_KEY"])
    os.makedirs(OUT, exist_ok=True)
    try:
        for code, line, role in [(c, l, r) for c, l in LINES.items()
                                 for r in voices.ROLES]:
            if only and code not in only:
                continue
            vid, lang = voices.for_role(role, code), code
            if not vid:
                print(f"  {code}  {role:9}  no voice: text only")
                continue
            ws = await client.tts.websocket()
            pcm = bytearray()
            try:
                async for chunk in await ws.send(
                        model_id=model, transcript=line,
                        voice={"mode": "id", "id": vid}, language=lang,
                        output_format={"container": "raw",
                                       "encoding": "pcm_s16le",
                                       "sample_rate": RATE},
                        stream=True):
                    pcm += getattr(chunk, "audio", b"") or b""
            except Exception as exc:
                trouble = paid_api_trouble(exc)
                print(f"  !! STOPPED — {trouble or type(exc).__name__}: {str(exc)[:70]}")
                print("  !! Says nothing about the voices. Top up and rerun.")
                await ws.close()
                return 2
            finally:
                await ws.close()
            path = os.path.join(OUT, f"{code}.wav" if role == "front"
                                else f"{code}-{role}.wav")
            with wave.open(path, "wb") as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(RATE)
                f.writeframes(bytes(pcm))
            print(f"  {code}  {role:9}  {voices.name_for(role, code):12} {len(pcm)/(RATE*2):4.1f}s")
    finally:
        await client.close()
    print(f"\n  written to {OUT}")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(set(sys.argv[1:]))))
