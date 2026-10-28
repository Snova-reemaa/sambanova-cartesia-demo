# Deploying VoxNova

For whoever runs the first deployment. One image, one process, one small
volume. Namespace `default`, the `aiskshowcase1` ingress slot.

## Before you start

This is a **vcluster on on-prem RKE2**, not GKE, so there is no
`gcloud container clusters get-credentials`. The kubeconfig is a file, and
it is a credential: keep it **outside the repo**, `chmod 600`, never
printed and never committed.

```
KUBECONFIG=/Users/<you>/.kube/aisk-dev-1.config
```

Put the path in `.env` as `KUBECONFIG`, absolute — a `~` is not expanded
either by the deploy script or by `docker --env-file`. The context and
cluster in that file are both called **`default`**, not the cluster's name,
which is why the script reads the context out of the file rather than
assuming it.

The cluster's API server presents a **self-signed CA**, which is embedded
in that kubeconfig after checking its SHA1 fingerprint with whoever issued
it. TLS then verifies normally. **Never** reach for
`insecure-skip-tls-verify`: it would accept any certificate for the API
server, on the VPN, where a wrong answer is silent.

**The VPN must be on, through a sj1 GlobalProtect portal.** Without it the
API server is simply unreachable and the script stops at step 1.

## The namespace is shared

`default` on this cluster holds a lot of other teams' work — at the last
count 27 deployments, 15 ingresses and 23 volumes. Everything this chart
makes is named `voxnova` or `voxnova-data` and labelled
`app.kubernetes.io/name=voxnova`. Touch nothing else. In particular,
`sambanova-voice-assistant-prod` is a different team's release with a
similar name and no ingress of its own; it is not this.

You also need:

- the **aiskshowcase1.cloud.snova.ai** DNS entry resolving
- push access to
  `us-west2-docker.pkg.dev/acp-starterkits-development-57/oci-us-west2-starerkits`
- a `.env` with the keys below. Check it with `./scripts/check-env.sh`,
  which reports names and presence only and never prints a value.

## 1. Build and push

```bash
PUSH=1 scripts/build-image.sh
```

Builds for `linux/amd64` and pushes under the short git SHA. Without
`PUSH=1` it builds and loads locally.

## 2. Deploy

```bash
VOXNOVA_HOST=<the aiskshowcase1 hostname> scripts/deploy-dev.sh
```

Reads `.env`, creates or updates the secret, and runs `helm upgrade
--install` against the context named in the kubeconfig (`default`),
namespace `default`.

## 3. Secrets — names and keys, no values

One Kubernetes Secret, **`voxnova-keys`**, created by the deploy script from
your `.env`. Its keys become environment variables:

| Key | Needed | What it is |
|---|---|---|
| `SAMBANOVA_API_KEY` | **yes** | the language model; the app will not start without it |
| `CARTESIA_API_KEY` | **yes** | speech; the app will not start without it |
| `DEMO_PASSPHRASE` | **yes in practice** | the shared passphrase on the login page. Leave it out and the pod generates one per restart and prints it, which means nobody can get in after a restart |
| `TAVILY_API_KEY` | optional | web search. Without it search falls back to DuckDuckGo |
| `EXA_API_KEY` | optional | alternative search provider |

Also needed, and **not** created by the script — it must already exist in
`default`:

- **`acp-starterkits-dev-gar-secret`** — the registry pull secret
  (`kubernetes.io/dockerconfigjson`), the same one sn-agent-swarms uses.

## 4. TRUSTED_PROXIES — please do not skip this

```
TRUSTED_PROXIES=100.64.0.0/12
```

That is the pod CIDR. The deploy script refuses to run without it.

The app counts verification attempts and code requests per caller, and works
out who the caller is from the session cookie plus their address. Behind an
ingress every request arrives from the ingress pod, so without this **every
caller shares one identity**: three wrong codes from anybody locks out
everybody for fifteen minutes, and six code requests exhausts the hour for
the whole deployment. It looks fine until two people use it at once.

One detail that matters here. `rke2-ingress-nginx` (in `kube-system`, not
`ingress-nginx`) runs with `use-forwarded-headers: "true"`, so it *appends*
the address it saw to whatever `X-Forwarded-For` the client sent:

```
X-Forwarded-For: <anything the client made up>, <the real address>
```

The app therefore reads the **rightmost** entry that is not itself inside
`TRUSTED_PROXIES`, not the first. Reading the first would let any client put
an address at the front and get a fresh identity per request. There is a
test for exactly that in `tests/verification.py`.

## 5. Check it

```bash
VOXNOVA=http://127.0.0.1:18080 VOXNOVA_PASSPHRASE=<the passphrase> \
  venv/bin/python tests/smoke.py
```

Seven checks: sign in, verify through the demo inbox, hang up and be
remembered on a second call, be forgotten, no external hosts, fonts served
locally, policy pages scroll. All seven pass against the image in a
container configured the way the pod configures it.

**Point it at a local container, not at the deployment.** The
"be forgotten" check really does delete `r.patel@example.com`, who is the
customer the demo is built around, and the address is hard-coded in
`tests/smoke.py`. Run it against the pod and the demo stops working, with
nothing but `memory.reset()` to put it back — and that drops every real
caller's history too. Either run it locally, or give the test a
test-only identity first and clean up with
`memory.forget('<that address>')`, which removes one customer and nothing
else.

The readiness and liveness probes both GET `/login`, which returns 200
without authentication by design.

## What is demo-only

- **The demo inbox** (`DEMO_INBOX=1`). The verification code appears on the
  caller's own screen, labelled "Demo mode: code shown on screen. In
  production it's emailed." It demonstrates the flow and **proves nothing
  about identity** — anyone looking at that screen passes. Switch it off and
  set `SMTP_HOST` / `SMTP_FROM` / `SMTP_USER` / `SMTP_PASSWORD` once there is
  an approved sender. With neither configured, verification reports itself
  unavailable and callers are helped without their history, never silently
  unverified.
- **The order book** is eight invented orders in `orders.json`. No real
  customer data.
- `/terms` and `/privacy` are drafts pending review by SambaNova Legal, and
  say so at the top.

## Storage

A 1Gi PVC mounted at `/data`, holding `memory.sqlite3` — customer names,
addresses and call summaries. It is kept when the release is uninstalled.

Retention runs in the pod: a sweep on startup and then every
`RETENTION_SWEEP_HOURS` (default 24). It deletes calls older than
`RETENTION_DAYS` (default 30), any customer left with no calls, and the
verification codes and send counts held against them. The eight demo
customers are exempt, by name, so the demo still works after a month. A
failed sweep is logged and the next one still runs — it cannot crash-loop
the pod. The log gives counts only, never an address.

`/privacy` tells the caller the same period, read from the same variable,
so changing `RETENTION_DAYS` changes both together. A caller asking to be
forgotten is handled immediately, separately from the sweep.

## Rolling back

```bash
helm --kube-context default -n default rollback voxnova
```

The volume is not touched by a rollback.
