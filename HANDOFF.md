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
| Smoke | `tests/smoke.py` — 6/6 against the real image in a pod-shaped container |
| Deploy | Image built for linux/amd64 under Colima and run read-only as uid 1000: 6/6, memory survives a restart, 48 files, no secrets |
| Deploy | `voxnova:8b6c05b` pushed to the starter-kits registry, linux/amd64, digest `sha256:09016b57…`; 7/7 smoke against the pushed image |
| Fix | `/terms` and `/privacy` scroll — they had inherited the call screen's `overflow:hidden`, so below the fold was unreachable on windows wider than 1180px |
| Fix | Retention runs: sweep on startup then every `RETENTION_SWEEP_HOURS` (24), guarded and cancelled on shutdown, counts logged and never addresses. `/privacy` is now true |
| Deploy | `voxnova:90946f2` pushed, linux/amd64, digest `sha256:40ba0d62…`; 7/7 smoke against the pushed image |
| Deploy | **Live at https://aiskshowcase1.cloud.snova.ai**: release `voxnova` rev 2, namespace `default`, pod 1/1, 7/7 smoke through a port-forward |
| Voices | Native voice per language (`voices.py`), samples in `listening/voices/` |
| Voices | Only native voices, enforced: each of main / billing / technical has its own native voice in all 8 languages; the pod refuses to start if Cartesia says any is in another language; a language with no voice is answered in text, never by a foreign voice; overridable per language in `values.yaml` |
| Hardening | Malformed frames, hang-ups, oversized input, session expiry, guarded socket writes |

## In progress

Nothing. Everything committed through `fd6eac1`; the tree is clean and the
offline suite is 409/409 across 13 suites.

## Verified locally, as the pod will run it

Image `voxnova:90946f2`, built for linux/amd64 (so it runs under emulation
on this Mac, which makes it slower here than it will be on the cluster) and
run exactly as the chart does — read-only root, `tmpfs` at `/tmp`, a volume
at `/data`, uid 1000, all capabilities dropped:

- `tests/smoke.py` 7/7 against it
- memory survives `docker restart`
- the read-only root holds: the app writes only to `/data`
- 48 files in the image, no `.env`, `.git`, `*.sqlite3*`, `kubeconfig`,
  `listening/`, `tests/`, `deploy/`, `scripts/`, `DEPLOY.md` or `HANDOFF.md`

One thing cost me most of this session and is worth knowing: a **stale
`server.py` from an earlier run was still listening on port 8080**, so the
smoke test was talking to it instead of the container and failing 3/6. If a
run fails in a way that makes no sense, check `lsof -nP -iTCP:8080 -sTCP:LISTEN`
before believing it.

## On the cluster now

Release `voxnova`, revision 2, namespace `default`, context `default`,
image tag `90946f2`. Installed 27 Oct 2026, moved to the `aiskshowcase1`
slot 28 Oct 2026.

Revision 2 exists because the Ingress was edited on the cluster rather
than through helm, so the live host and helm's stored values disagreed:
the next `helm upgrade` for any reason would have re-rendered the old
host and taken the site down, with nothing in the command to suggest
why. Rerunning the deploy with the repo updated put the record straight.
The diff was the two host lines and nothing else, the pod template was
unchanged and the keys checksum the same, so the pod never restarted.

It owns exactly six objects and nothing else in that shared namespace was
touched — every resource count went up by one, and secrets by two (the key
Secret and helm's own release record):

```
deployment/voxnova        1/1 running
service/voxnova           ClusterIP
ingress/voxnova           nginx, aiskshowcase1.cloud.snova.ai
persistentvolumeclaim/voxnova-data   Bound, 1Gi
networkpolicy/voxnova     selects app.kubernetes.io/name=voxnova only
secret/voxnova-keys       Opaque
```

**Live at https://aiskshowcase1.cloud.snova.ai** (on the VPN) — `/login`
answers 200 over the wildcard certificate.

It was first deployed on `aiskshowcase1.cloud.snova.ai`, which never
resolved: that name had been deleted a month earlier, so the record was
never going to appear. Tamas moved it to the `aiskshowcase1` slot on
28 Oct 2026. Nothing else claims that host.

Also reachable without DNS, for debugging:

```bash
kubectl --context default -n default port-forward deploy/voxnova 18080:8080
```

The pod IP is in `100.64.0.0/12`, so `TRUSTED_PROXIES` is correct for this
cluster.

**Never run `memory.reset()` against the deployed pod again.** It drops
the customers and calls tables outright — every real caller's history
along with the demo fixtures, with no backup and no undo. It was run once,
immediately after the first deploy, to undo the smoke test's deletion of
Riya Patel; that was safe only because the deployment was minutes old and
held nothing but seed data. That window has closed.

**The smoke test as written is destructive against a real deployment.**
Its "forget me" check genuinely deletes `r.patel@example.com`, who is the
customer the demo is built around, and `tests/smoke.py` has that address
hard-coded. Before running it against the cluster again, change it to use
a test-only identity — an address belonging to nobody, seeded for the run
— and delete just that one record afterwards:

```bash
kubectl --context default -n default exec deploy/voxnova -- \
  python -c "import memory; memory.forget('smoke-test@example.invalid')"
```

`forget()` removes one customer and their calls and nothing else, which is
the difference that matters. Until the test is changed, run it against a
local container rather than the pod.

## Blocked on you

1. **Deploy.** `DEPLOY.md` has the commands. Cluster and registry access
   are already granted under CP-3072 — no access request is needed. The
   kube context is `ai-starter-kits-dev-1` and the host is
   `aiskshowcase1.cloud.snova.ai`.
2. **The kubeconfig** — the one thing left. Fetch it to a path outside the
   repo, `chmod 600`, and put the path in `.env` as `KUBECONFIG`; then
   `./scripts/check-env.sh` is clean. `VOXNOVA_HOST`, `TRUSTED_PROXIES` and
   `DEMO_INBOX` are set. DNS is settled — the host cluster fills the record
   in once the Ingress exists, so the hostname stays
   `aiskshowcase1.cloud.snova.ai`. The image is pushed; nothing on the
   cluster has been touched.

   Deploy with the tag that was pushed and tested, not with HEAD:

   ```bash
   scripts/deploy-dev.sh 90946f2
   ```

   Left to default it would take HEAD's short SHA, which is a
   documentation-only commit that was never built or pushed, and step 2 of
   the script would stop with "not in the registry".
   `TRUSTED_PROXIES=100.64.0.0/12` is settled, and because
   `rke2-ingress-nginx` runs with `use-forwarded-headers: true` the app now
   reads the rightmost untrusted `X-Forwarded-For` entry, with a test that a
   spoofed header cannot change a caller's rate-limit key.
3. **Auth0** — not built. See "Auth0, if you want it" below for exactly
   what it needs.
4. **Listen to `listening/voices/*.wav`** and tell me which to change.
   24 voices, chosen from Cartesia's descriptions rather than by ear:
   `<lang>.wav` is the main agent, `<lang>-billing.wav` Maya,
   `<lang>-technical.wav` Omar. Dutch was never missing: Cartesia has 11
   Dutch voices in its public library, just none owned by this account.
   `listening/voices/nl-candidates/` has the four Dutch candidates for the
   main voice (Noa is set; Isa, Anneliese and Femke are there to compare).
   Swap any of them in `values.yaml` under `voices:` without a rebuild.
5. **Cartesia credit** for the rest of the audio batch (`listening/PENDING.md`).
6. **Rotate the SambaNova and Tavily keys** — they were in the zips.
7. **Native review** of the Italian, Portuguese and Dutch letter names
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
(`https://aiskshowcase1.cloud.snova.ai` throughout):

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
- **New callers are put on file before they verify.** Saying an address
  nobody has creates an unverified record under the name they gave (picked
  out of "my name is…" in any of the eight languages, else from the
  address), then the usual code. The answer is identical for known and new
  addresses, so it reveals nothing. Unverified records can't be found by
  name, and one that never verifies has no calls, so the retention sweep
  removes it. The cost arrives with SMTP: any address a caller says can be
  sent a code, held back only by 3 an hour per address and 6 an hour per
  caller. Revisit that before switching SMTP on.
- **"Forget me" assumes one replica.** A call's write-up runs after hang-up
  and used to land after "forget me", re-creating the caller. `forget()` now
  leaves a marker (a hash of the address and the time) and `save_call()`
  refuses any call that began before it. The markers live only in this
  process's memory, which is right for one pod and wrong for two: a late
  write-up on another replica would not see them and could bring a
  forgotten caller back. Before scaling past one replica, move them to a
  shared store, e.g. a `forgotten_markers` table holding only the hash and
  the timestamp. The chart pins `replicas: 1` and says so beside it.
