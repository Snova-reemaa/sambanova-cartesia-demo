"""Run the tests.

    venv/bin/python tests/run.py                 everything offline, free, fast
    RUN_LIVE_TESTS=1 venv/bin/python tests/run.py     adds the ones that call out

Offline is the default on purpose. The suites that talk to SambaNova cost
money and take minutes, and a test you cannot afford to run is a test nobody
runs. Everything that can be decided without a provider — the detectors, the
retrieval, the transcript repair, the socket's behaviour under bad input — is
decided without one.

Of the live suites only tests/live_audio.py spends Cartesia credits. The
others run the server in TEXT_ONLY mode: they are about what the agent says,
not what it sounds like, and synthesis is billed by the character.
"""

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(ROOT, "venv", "bin", "python")
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

# Modules that test themselves when run directly.
OFFLINE_MODULES = [
    ("smarts", "routing tier, mood, sentence splitting"),
    ("languages", "which language, in eight"),
    ("heard", "transcript repair"),
    ("memory", "caller identification"),
    ("orders", "order numbers said out loud"),
    ("knowledge_base", "retrieval"),
    ("slots", "what a valid answer is, per field type"),
]

OFFLINE_SUITES = [
    ("tests/socket_robustness.py", "bad frames, hang-ups, oversized input"),
    ("tests/sessions.py", "sign-in tokens expire"),
    ("tests/concurrency.py", "many callers at once are told, not hung"),
    ("tests/consent.py", "nobody gets in without agreeing"),
    ("tests/verification.py", "one-time codes, lockout, retention, deletion"),
    ("tests/protocol.py", "code holds the step, the model cannot move it"),
]

LIVE_SUITES = [
    ("tests/live_languages.py", "replies stay in the caller's language"),
    ("tests/live_escalation.py", "the right person takes the call"),
    ("tests/live_unexpected.py", "questions nobody planned for"),
    ("tests/live_protocol.py", "scripted awkward callers, against the model"),
    ("tests/live_audio.py", "a turn comes back as audio (spends Cartesia)"),
]

SCORE = re.compile(r"(\d+)\s*/\s*(\d+)")


def run(target, what, module=False):
    started = time.perf_counter()
    cmd = [PYTHON, "-m", target] if module else [PYTHON, target]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    output = done.stdout + done.stderr
    scores = SCORE.findall(output)
    passed, total = (int(scores[-1][0]), int(scores[-1][1])) if scores else (0, 0)
    took = time.perf_counter() - started
    ok = done.returncode == 0 and total > 0 and passed == total
    name = target if not module else target + ".py"
    print(f"  {'ok  ' if ok else 'FAIL'} {passed:>3}/{total:<3} {took:5.1f}s  "
          f"{name:34} {what}")
    if not ok:
        for line in output.strip().splitlines()[-12:]:
            print(f"         {line}")
    return ok, passed, total


def main():
    live = bool(os.getenv("RUN_LIVE_TESTS"))
    results = []

    print("\noffline")
    for name, what in OFFLINE_MODULES:
        results.append(run(name, what, module=True))
    for path, what in OFFLINE_SUITES:
        results.append(run(path, what))

    if live:
        print("\nlive — these call SambaNova, and the last one calls Cartesia")
        for path, what in LIVE_SUITES:
            results.append(run(path, what))
    else:
        print("\nlive   skipped — set RUN_LIVE_TESTS=1 to include them")

    passed = sum(p for _, p, _ in results)
    total = sum(t for _, _, t in results)
    failed = [r for r in results if not r[0]]
    print(f"\n  {passed}/{total} checks passed across {len(results)} suites"
          + (f", {len(failed)} suite(s) failing" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
