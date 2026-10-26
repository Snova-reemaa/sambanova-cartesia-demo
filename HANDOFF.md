# VoxNova — handoff

Updated after every committed piece. Offline tests: `venv/bin/python tests/run.py`
(free, ~45s). Live: `RUN_LIVE_TESTS=1 venv/bin/python tests/run.py`.

## Done

| | |
|---|---|
| FIX FIRST 1 | English no longer leaks into non-English replies. 28/28, twice |
| FIX FIRST 2 | Image limits consistent (8 x 2MB = the frame cap); dropped attachments are explained, not silent |
| FIX FIRST 3 | Pod-wide LLM cap (`MAX_CONCURRENT_LLM`), queue, then told — never a silent hang |
| FIX FIRST 4 | Uncovered questions say so and offer a transfer instead of promising a callback; logged with the call |
| Task 2 | Consent gate (UI + server + `/ws`), `/terms`, `/privacy`, in-call disclosure in 8 languages, fonts self-hosted |
| Task 3 (part) | Verification store: codes, lockout keyed on the caller, retention, deletion |
| Task 3 (part) | `slots.py` — validation per field type, 68 checks |
| Task 3 (part) | `flow.py` — code-driven state machine, 42 checks; live eval 29/30 |
| Task 3 (part) | Caller key = session + X-Forwarded-For, trusted only from our proxy |
| Task 3 (part) | Verification wired in: name or address starts a code, reading it back verifies, history only then |
| Task 3 (part) | Code delivery — demo inbox on, SMTP written and off |
| Task 3 (part) | Typed-code box: voice first, appears after one miss, shares the attempt count |
| Task 3 (part) | Returning-customer memory: verified callers get their history, nobody else |
| Task 3 (part) | "Forget me" — verified callers only, deletes everything, says so |
| Hardening | Malformed frames, hang-ups, oversized input, session expiry, guarded socket writes |

## In progress

- Typed-code box in the page.

## Next, in order

1. Typed-code box in the page
3. Returning-customer memory across calls
4. "Forget me"
5. Deploy: Dockerfile and Helm chart into the repo, build, smoke test
6. Native voice per language (config table, one sample each)

## Blocked on you

1. **Which cluster, namespace and ingress host.** The chart you sent needs
   `ingress.host` and expects the `voxnova-keys` secret and the
   `acp-starterkits-dev-gar-secret` pull secret to exist.
2. **`TRUSTED_PROXIES`** — the ingress controller's pod CIDR. Without it
   every caller behind the proxy shares one caller key, so the per-caller
   limits apply to everybody at once.
3. **Cartesia credit** for the deferred audio batch (`listening/PENDING.md`).
4. **Rotate the SambaNova and Tavily keys** — they were in the zips.
5. **Native review** of the Italian, Portuguese and Dutch letter names
   (`NEEDS_NATIVE_REVIEW` in `slots.py`).

## Deferred backlog

Laughter rules and samples; real address backends (Loqate / Ideal Postcodes
/ Google — none implemented, all refuse clearly); admin delete-by-email
script; two-call transcript; sonic-3 vs 3.6 vs preview listening;
first-audio latency before/after Task 1; more evals.

## Known and deliberate

- **The demo inbox is not verification.** The code appears on the caller's
  own screen; anyone looking at it passes. It demonstrates the flow.
- `main.py` is a separate CLI measurement tool, not used by the server.
- Arabic uses English letter names for Latin letters, as Arabic speakers do.
