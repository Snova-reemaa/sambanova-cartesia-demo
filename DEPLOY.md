# Deploying VoxNova

For whoever runs the first deployment. One image, one process, one small
volume. Namespace `default`, the `aiskvoicedemo` ingress slot.

## Before you start

You need:

- the full hostname for the **aiskvoicedemo** slot (an existing
  `*.cloud.snova.ai` name — not a new domain)
- push access to
  `us-west2-docker.pkg.dev/acp-starterkits-development-57/oci-us-west2-starerkits`
- `kubectl` context `aisk-dev-1`, namespace `default`
- a `.env` with the keys below (never committed, never in the image)

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
--install` against context `aisk-dev-1`, namespace `default`.

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

Set `env.TRUSTED_PROXIES` in `values.yaml` to the **ingress controller's pod
CIDR**.

The app counts verification attempts and code requests per caller, and works
out who the caller is from the session cookie plus their IP address. Behind
an ingress every request arrives from the ingress pod, so without this
**every caller shares one identity**: three wrong codes from anybody locks
out everybody for fifteen minutes, and six code requests exhausts the hourly
allowance for the whole deployment.

`X-Forwarded-For` is only believed from a peer listed here. That is
deliberate — it is a request header like any other, and trusting it from
anywhere lets a client mint a fresh identity per request and make the limits
meaningless.

Find the CIDR with:

```bash
kubectl -n ingress-nginx get pods -o jsonpath='{.items[*].status.podIP}'
```

A prefix is enough, e.g. `TRUSTED_PROXIES: "10.4."`. Several are
comma-separated.

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
Retention deletes call history after `RETENTION_DAYS` (default 30).

## Rolling back

```bash
helm --kube-context aisk-dev-1 -n default rollback voxnova
```

The volume is not touched by a rollback.
