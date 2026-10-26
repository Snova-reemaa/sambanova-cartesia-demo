# Deploying VoxNova

For whoever runs the first deployment. One image, one process, one small
volume. Namespace `default`, the `aiskvoicedemo` ingress slot.

## Before you start

This is a **vcluster on on-prem RKE2**, not GKE, so there is no
`gcloud container clusters get-credentials`. The kubeconfig is a file:

```bash
gsutil cp gs://acp-development-us-west2-4-to/vclusters/\
vcluster-ai-starter-kits-dev-1/kubeconfig-<you>@sambanovasystems.com.yaml \
  ~/.kube/voxnova-dev.yaml
chmod 600 ~/.kube/voxnova-dev.yaml
```

If that 404s, find yours: `gsutil ls gs://sn-user-kubeconfigs-0/`

Keep it **outside the repo**. It is a credential: never printed, never
committed. Put the path in `.env` as `KUBECONFIG`; the deploy script reads
the context name out of the file rather than assuming it, because a
vcluster's context is not necessarily the cluster's name.

You also need:

- the **aiskvoicedemo.cloud.snova.ai** DNS entry resolving
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
VOXNOVA_HOST=<the aiskvoicedemo hostname> scripts/deploy-dev.sh
```

Reads `.env`, creates or updates the secret, and runs `helm upgrade
--install` against context `ai-starter-kits-dev-1`, namespace `default`.

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
VOXNOVA=https://<host> VOXNOVA_PASSPHRASE=<the passphrase> \
  venv/bin/python tests/smoke.py
```

Six checks: sign in, verify through the demo inbox, hang up and be
remembered on a second call, be forgotten, no external hosts, fonts served
locally. All six pass locally against the container's own environment.

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

**Retention is not running yet.** `RETENTION_DAYS` is set on the pod and the
sweep is written and tested (`memory.purge_old`), but nothing calls it, so
call history is currently kept until someone deletes it. A caller asking to
be forgotten is handled immediately and in full — that path is live and
tested. Before this holds anything beyond the invented demo data, either
wire the sweep to run on startup or delete the volume between demos.

## Rolling back

```bash
helm --kube-context ai-starter-kits-dev-1 -n default rollback voxnova
```

The volume is not touched by a rollback.
