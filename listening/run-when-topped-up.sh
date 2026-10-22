#!/usr/bin/env bash
# Everything that needs Cartesia, in one go, once the account has credit.
# Each step stops loudly on 402/401/429 rather than recording silence.
set -u
cd "$(dirname "$0")/.."
: "${RUN_LIVE_TESTS:=1}"; export RUN_LIVE_TESTS
PY=venv/bin/python
[ -x "$PY" ] || PY=python3

step() { printf '\n=== %s ===\n' "$1"; }

step "emotion samples for the six languages that never recorded"
$PY tests/make_emotion_samples.py fr de it pt nl ar || echo "  ^ stopped, see above"

step "the audio test"
$PY tests/live_audio.py || echo "  ^ stopped, see above"

step "the full suite, live"
$PY tests/run.py || echo "  ^ some suites failed, see above"

cat <<'NOTE'

Still to write, and not run by this script:
  - sonic-3 vs sonic-3.6 vs sonic-preview, same lines, for listening
  - laughter samples, once the laughter rules exist
  - first-audio latency before and after the Task 1 voice changes
See listening/PENDING.md.
NOTE
