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
| Deploy | Dockerfile + Helm chart in the repo, settings wired into values.yaml |
| Smoke | `tests/smoke.py` — 6/6 locally against the container's environment |
| Voices | Native voice per language (`voices.py`), samples in `listening/voices/` |
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

1. **Deploy.** `DEPLOY.md` has the commands. Needs the full hostname for the
   `aiskvoicedemo` slot, and the `acp-starterkits-dev-gar-secret` pull
   secret present in `default`.
2. **`TRUSTED_PROXIES`** — the ingress controller's pod CIDR. Without it
   every caller behind the proxy shares one caller key, so three wrong
   codes from anybody locks out everybody.
3. **Auth0** — not built. See "Auth0, if you want it" below for exactly
   what it needs.
3. **Listen to `listening/voices/*.wav`** and tell me which to change.
   Dutch has **no native voice on this account** — a Dutch caller gets an
   English voice speaking Dutch. Either Cartesia adds one or Dutch comes
   off the list of languages this claims to speak.
4. **Cartesia credit** for the rest of the audio batch (`listening/PENDING.md`).
4. **Rotate the SambaNova and Tavily keys** — they were in the zips.
5. **Native review** of the Italian, Portuguese and Dutch letter names
   (`NEEDS_NATIVE_REVIEW` in `slots.py`).

## Auth0, if you want it

Not implemented, and nothing in the app knows about it. What it would need:

**Does it replace the passphrase?** It should. They do the same job — a gate
in front of a demo — and keeping both means two things to get past and two
places to get wrong. Auth0 gives you per-person identity, which also fixes
the one real weakness of the current limits: the caller key is session plus
IP, and with a shared passphrase anybody can get a fresh session by signing
in again. With real accounts it would key on the user.

**Redirect / callback URLs** for the Auth0 application
(`https://<aiskvoicedemo host>` throughout):

- Allowed Callback URLs: `https://<host>/auth/callback`
- Allowed Logout URLs: `https://<host>/login`
- Allowed Web Origins: `https://<host>`
- Application type: Regular Web Application

**Environment variables** it would read:

- `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`
- `AUTH0_AUDIENCE` (only if an API is involved; not needed for login alone)
- `SESSION_SECRET` for signing the session cookie

The consent gate stays either way: agreeing to the terms is separate from
proving who you are, and the record of consent is per session.

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
