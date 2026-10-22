"""One turn, with the voice on.

The only suite that spends Cartesia credits, and the only one that can tell
you synthesis still works: everything else runs in TEXT_ONLY. Kept to a
single short turn for that reason.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.live import Caller, run                             # noqa: E402


async def cases(http, server):
    results = []
    async with Caller(http, server, audio=True) as caller:
        turn = await caller.say("what are your opening hours")
        reply, pcm = turn["reply"] or "", turn["audio_bytes"]
        seconds = pcm / (44100 * 2)
        results.append((bool(reply), f"the agent answered: {reply[:60]}"))
        results.append((turn["error"] is None, f"no error: {turn['error']}"))
        results.append((seconds > 0.5,
                        f"{seconds:.1f}s of audio came back ({pcm} bytes)"))
    return results


if __name__ == "__main__":
    raise SystemExit(run(cases, audio=True))
