# Waiting on Cartesia credit

Everything here needs synthesis and nothing here has been run. The Cartesia
account returned HTTP 402 part way through the first emotion recording, so
the work stopped rather than filling directories with empty files that read
exactly like a negative result.

Run it in one batch once the account has credit:

```bash
cd /Users/reemaa/Desktop/sambanova_cartesia_Demo
RUN_LIVE_TESTS=1 ./listening/run-when-topped-up.sh
```

Each script stops loudly on 402, 401 or 429 and says which, rather than
reporting an empty run as a finding.

## 1. Emotion samples — six languages missing

`en` and `es` are recorded. `fr de it pt nl ar` are not.

```bash
RUN_LIVE_TESTS=1 venv/bin/python tests/make_emotion_samples.py fr de it pt nl ar
```

Listen to `listening/emotion/<lang>/`. Each language has the same apology
under neutral, calm, content and sad, plus angry and scared as a control. If
`5-check-angry` sounds the same as `1-neutral`, the parameter is being
accepted and discarded for that language — turn it off in
`EMOTION_BY_DEFAULT` and that language keeps pacing and wording instead.

For `en` and `es`, which are recorded: the six settings produce audio of
different lengths, so something is changing. Whether it changes for the
better is the listening judgement.

## 2. sonic-3 vs sonic-3.6 vs sonic-preview

Not yet written. The same lines through all three, for comparison.

First-audio timings already measured, one short line each:

| model | first audio |
|---|---|
| sonic-2 | 1654 ms |
| sonic-3 | 1187 ms |
| sonic-turbo | 1181 ms |
| sonic-preview | 1151 ms |
| **sonic-3.6** | **976 ms** |

sonic-3.6 is the fastest of them, so switching to it does not cost latency.
Which sounds the most natural is not something I can judge.

## 3. Laughter

Not yet written, and blocked on more than credit: the laughter rules need
building before there is anything to record. When they exist, `[laughter]`
needs hearing in each of the eight languages, because a laugh that lands
wrong is worse than no laugh.

What is known: `[laughter]` measurably changes the audio on sonic-3.6 in
both English and Arabic. Whether it sounds like a laugh or like someone
reading the word is unverified.

## 4. Audio test in the suite

`tests/live_audio.py` passes (3/3, 22.1s of PCM) and will need rerunning
after the model switch.

## 5. First-audio latency, before and after Task 1

Required by the brief: Task 1 must not add latency before first audio.
Needs measuring on the finished pipeline, not just the raw model.
