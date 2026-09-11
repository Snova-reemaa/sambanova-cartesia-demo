import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from sambanova import stream_reply
from cartesia_tts import get_client, output_format, save_wav, play

SAMPLE_RATE = 44100
BYTES_PER_SAMPLE = 2


async def run(prompt, autoplay=True):
	os.makedirs("output", exist_ok=True)
	pcm_path = "output/reply.pcm"
	wav_path = "output/reply.wav"
	timings_path = "output/timings.json"

	voice_id = os.getenv("CARTESIA_VOICE_ID", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")
	model_id = os.getenv("CARTESIA_MODEL", "sonic-2")
	llm_model = os.getenv("SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct")

	client = get_client()
	reply_text = ""
	audio_bytes = 0

	# Connection setup is measured separately: in a real app the socket is
	# already warm when the user speaks, so folding it into "first token"
	# inflates the number by a second or two.
	t_setup = time.perf_counter()
	async with client.tts.websocket_connect() as ws:
		ctx = ws.context(
			model_id=model_id,
			voice={"mode": "id", "id": voice_id},
			output_format=output_format(),
			language="en",
		)
		setup = time.perf_counter() - t_setup

		# The clock that matters starts when we send the prompt.
		t0 = time.perf_counter()
		first_token = None
		last_token = None
		first_audio = None

		async def send_text():
			nonlocal reply_text, first_token, last_token
			async for chunk in stream_reply(prompt):
				if first_token is None:
					first_token = time.perf_counter() - t0
				reply_text += chunk
				await ctx.push(chunk)
			last_token = time.perf_counter() - t0
			await ctx.no_more_inputs()

		async def receive_audio():
			nonlocal first_audio, audio_bytes
			with open(pcm_path, "wb") as f:
				async for res in ctx.receive():
					if res.type == "chunk" and res.audio:
						if first_audio is None:
							first_audio = time.perf_counter() - t0
						audio_bytes += len(res.audio)
						f.write(res.audio)
					elif res.type == "error":
						raise RuntimeError(res.message or res.title)

		await asyncio.gather(send_text(), receive_audio())
		total = time.perf_counter() - t0

	await client.close()
	save_wav(pcm_path, wav_path)

	audio_seconds = audio_bytes / (SAMPLE_RATE * BYTES_PER_SAMPLE)
	timings = {
		"prompt": prompt,
		"reply": reply_text,
		"llm_model": llm_model,
		"tts_model": model_id,
		"voice_id": voice_id,
		"setup_s": setup,
		"first_token_s": first_token,
		"first_audio_s": first_audio,
		"last_token_s": last_token,
		"total_s": total,
		"audio_seconds": audio_seconds,
		"audio_bytes": audio_bytes,
		"chars": len(reply_text),
	}
	with open(timings_path, "w") as f:
		json.dump(timings, f, indent=2)

	def ms(t):
		return f"{t * 1000:7.0f} ms" if t is not None else "      n/a"

	print(f"\n{reply_text}\n")
	print(f"  socket setup   {ms(setup)}   (excluded from the clock below)")
	print(f"  first token    {ms(first_token)}   SambaNova")
	print(f"  first audio    {ms(first_audio)}   Cartesia — time to hearing something")
	print(f"  last token     {ms(last_token)}")
	print(f"  total          {ms(total)}")
	print(f"  audio produced {audio_seconds:7.1f} s  in {total:.1f} s wall  "
	      f"({audio_seconds / total:.1f}x realtime)")
	print(f"\n  wrote {wav_path} and {timings_path}")

	if autoplay:
		play(wav_path)


if __name__ == "__main__":
	args = [a for a in sys.argv[1:] if a != "--no-play"]
	autoplay = "--no-play" not in sys.argv[1:]
	prompt = " ".join(args) or input("prompt: ")
	asyncio.run(run(prompt, autoplay=autoplay))
