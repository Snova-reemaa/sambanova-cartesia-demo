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
| Deploy | Rev 6, `voxnova:fdad519`: back to passphrase sign-in (new passphrase), Auth0 built and off, ready with VOXNOVA_AUTH=1. Policy pages public; 8/8 smoke on the pod; demo customers unchanged |
| Deploy | Rev 5, `voxnova:fdad519`: Auth0 sign-in on; the signed-in email is the caller's identity (no code for it, history tied to it, forget deletes it). Redirect chain to the tenant's login checked; demo customers unchanged |
| Deploy | Rev 4, `voxnova:e727fc4`: picker offers only native voices for the language spoken; redesigned Voice and Language pickers (keyboard, RTL, mobile sheet, cached previews). 8/8 smoke on the pod |
| Deploy | Rev 3, `voxnova:f209930`: voice on (websockets fix), native voices, forget-me race fixed, new callers, Auth0 built and off. 8/8 smoke on the pod, demo customers unchanged |
| Voices | Native voice per language (`voices.py`), samples in `listening/voices/` |
| Voices | Only native voices, enforced: each of main / billing / technical has its own native voice in all 8 languages; the pod refuses to start if Cartesia says any is in another language; a language with no voice is answered in text, never by a foreign voice; overridable per language in `values.yaml` |
| Hardening | Malformed frames, hang-ups, oversized input, session expiry, guarded socket writes |

## In progress

Nothing. Deployed `fdad519` with the passphrase as revision 6; the tree is
clean and the offline suite is 560/560 across 16 suites.

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

Release `voxnova`, **revision 6**, namespace `default`, context `default`,
image tag **`fdad519`**, **passphrase sign-in (Auth0 off)**. Installed 27
Oct 2026, moved to the `aiskshowcase1` slot, then upgraded to `f209930`
(rev 3) and `e727fc4` (rev 4) on 28 Oct 2026; Auth0 switched on (rev 5) and
back off (rev 6) on 29 Oct 2026.

Revision 6 went back to the passphrase, with a new one, on the same
image: the oauth2-proxy and its Ingress, Service and NetworkPolicy were
removed, the auth annotations dropped, and the app's AUTH_PROXY and
AUTH_CHECK_URL with them; the keys checksum changed for the new
passphrase, so the app pod restarted. The Auth0 keys stay in
`voxnova-keys` and `.env` (VOXNOVA_AUTH=0) for later. Checked after:
`/login` shows the passphrase and consent boxes; `/terms`, `/privacy` and
the stylesheet load without signing in; 8/8 smoke on the pod; the demo
customers identical to a snapshot taken before.


Revision 5 had switched Auth0 on, on a free Auth0 tenant for testing (to move
to SambaNova's tenant later, through IT): the oauth2-proxy Deployment,
Service, `/oauth2` Ingress and NetworkPolicy were added, the app's Ingress
gained the auth annotations, and `voxnova-keys` gained the three Auth0
keys, so the app pod restarted. Checked after: a stranger is sent from
`/login` to `/oauth2/start` to the tenant's login page, which loads (so
the callback URL is accepted); the app reaches the proxy on 4180; the app
logs "sign-in: Auth0"; the demo customers identical to a snapshot taken
before. **The 8-check smoke test cannot run now**: it signs in with the
passphrase, which this site refuses. A signed-in run has to go through
Auth0 in a browser. To go back to the passphrase site:
`helm --kube-context default -n default rollback voxnova 4`.

Revision 4 was the voice and language pickers.


Revision 4 is the voice and language pickers: only the language's own
three native voices are offered, in a pill-and-panel picker with cached
previews (`/voice-sample`, each voice synthesised once per pod). Image tag
the only change on the cluster; 8/8 smoke on the pod; demo customers
identical to a snapshot taken before.

Revision 3 is the first with a voice. Revisions 1 and 2 answered every
call "ready, text only": the image had cartesia without its websockets
extra, and the smoke test, all text, never noticed. The upgrade changed
the image tag and nothing else (two comments aside; the keys checksum and
the Secret's keys unchanged). After it, through a port-forward: 8/8 smoke,
"voice is on" among them; the startup log says all 24 voices are native to
their language; the eight demo customers, Riya Patel's two seeded calls
included, identical to a snapshot taken before; no other customers, and
the smoke test's throwaway identity gone. Auth0 is in the chart and off.

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

**The smoke test is safe against the deployment now.** It calls as
`smoke-test@example.invalid`, seeded before the first check and removed
with `memory.forget()` after the last, and never touches the demo
customers. Against the pod:

```bash
kubectl --context default -n default port-forward deploy/voxnova 18080:8080
VOXNOVA=http://127.0.0.1:18080 VOXNOVA_PASSPHRASE=<the passphrase> \
  VOXNOVA_EXEC="kubectl --context default -n default exec deploy/voxnova --" \
  venv/bin/python tests/smoke.py
```

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
3. **Auth0** — built and off (revision 6 is the passphrase); ready to
   re-enable with `VOXNOVA_AUTH=1`, against the free test tenant. Moving
   to SambaNova's tenant later, through IT: new issuer, client ID and
   secret in `.env`, the same callback URL, then redeploy.
4. ~~Listen to the voices~~ — **done 28 Oct 2026: all 24 approved by ear
   and locked in as the defaults.** For reference,
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

## Auth0 — built, off, ready to re-enable

Built the way sn-agent-swarms does it, **off by default** in the chart
(nothing renders until `auth.enabled=true`). It ran as revision 5 against a
free Auth0 tenant for testing, and was switched off again in revision 6.
**To re-enable: set `VOXNOVA_AUTH=1` in `.env` and run
`scripts/deploy-dev.sh <tag>`.** The issuer, client ID, client secret and
cookie secret are already in `.env` and in `voxnova-keys`; the Auth0
application already allows the callback URL. Addresses at
`sambanovasystems.com` and `sambanova.ai` can sign in (`auth.emailDomains`).

Added since revision 5, and not yet deployed:

- `/terms`, `/privacy` and their stylesheet get their own Ingress with no
  auth, so they read without signing in.
- oauth2-proxy shows our own sign-in page (`files/sign_in.html`) with the
  Terms and Privacy links, before sending people to Auth0.
- **Check before relying on it:** that page is an oauth2-proxy template.
  `tests/chart.py` proves the chart renders and mounts it, but it has not
  been loaded by oauth2-proxy itself; a template it cannot parse stops the
  proxy starting. Run it once locally, or watch the proxy pod come up 1/1
  and open `/oauth2/sign_in`, when re-enabling.

**Signed in, the email is the identity.** The call starts as that user:
their record (made on first sign-in, verified, named from the address),
their history, a greeting by name, no code for their own email. Another
email on the call is an ordinary claim, the code flow, nothing shared
until it checks out. "Forget me" deletes the signed-in email's record and
it is not made again for the rest of that call.

- **oauth2-proxy** (`quay.io/oauth2-proxy/oauth2-proxy:v7.9.0`, OIDC) as its
  own Deployment, Service and `/oauth2` Ingress on the same host; the app's
  Ingress gets `auth-url` / `auth-signin`, so nobody reaches the app without
  signing in. Only `@sambanovasystems.com` addresses (`auth.emailDomains`).
- **The passphrase goes, the consent box stays.** `/privacy` then also says
  the sign-in email is recorded with the time and policy version, kept as
  long as the session's consent record (12h, or a restart), and lists Auth0
  as a processor. Off, it says neither.
- **No header is believed.** The app asks the proxy itself who signed in
  (`AUTH_CHECK_URL`, the caller's own oauth2 cookie, 202 or nothing) and
  keys its limits on that user. Forged `X-Auth-Request-*` headers, a made-up
  cookie, a slow or failing proxy: all refused, and the failures logged
  (`tests/auth.py`). This matters because the NetworkPolicy **cannot**
  keep other pods off the app here: the ingress controller is on the host
  cluster, out of reach of a selector in this vcluster, and sn-agent-swarms
  leaves 8080 open to everything for the same reason.
- **Network paths** (`tests/chart.py`): app → proxy on 4180 open (the app's
  policy restricts Ingress only); proxy admits 4180 from the app and, since
  the controller can't be named, from anywhere; proxy egress open so it
  reaches Auth0 on 443. Not narrowed to "53 + 443": vcluster CoreDNS
  listens on 1053 behind its Service, and that rule would break sign-in.

**The Auth0 application** (Regular Web Application):

- Allowed Callback URLs: `https://aiskshowcase1.cloud.snova.ai/oauth2/callback`

**To switch it on**, in `.env` (`./scripts/check-env.sh` lists them):

```
VOXNOVA_AUTH=1
AUTH0_ISSUER_URL=https://<tenant>.us.auth0.com/
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
AUTH0_COOKIE_SECRET=...      # openssl rand -base64 32 | tr -- '+/' '-_'
```

then `scripts/deploy-dev.sh <tag>`. The three keys go into `voxnova-keys`
with the others and never into values or git. One side effect: the app's
container gets them in its environment too (it reads the whole Secret with
`envFrom`); it never uses them.

Not built: a sign-out link that also logs out of Auth0 (sn-agent-swarms has
a `/signout` redirect for that; it needs the client ID in an annotation).

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
