import os
import platform
import subprocess
import wave

from cartesia import AsyncCartesia


def get_client():
    return AsyncCartesia(api_key=os.environ["CARTESIA_API_KEY"])


def output_format():
    return {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 44100}


def save_wav(pcm_path, wav_path, sample_rate=44100):
    with open(pcm_path, "rb") as f:
        data = f.read()
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)


def play(wav_path):
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["afplay", wav_path], check=True)
        elif system == "Windows":
            subprocess.run(
                ["powershell", "-c", f'(New-Object Media.SoundPlayer "{wav_path}").PlaySync();'],
                check=True,
            )
        else:
            subprocess.run(["aplay", wav_path], check=True)
    except Exception:
        print(f"couldn't auto-play — open {wav_path} manually")
