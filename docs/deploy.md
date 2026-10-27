# Deploying VoxNova to the dev cluster

VoxNova runs as one pod on the dev cluster (`ai-starter-kits-dev-1`, whose kubeconfig context is
`default`), behind the VPN, at an existing
`*.cloud.snova.ai` name over HTTPS. The browser only allows the microphone on HTTPS, so the
cluster's wildcard certificate (`cloud-snova-ai-tls`) is what makes voice work.

The setup follows `sn-agent-swarms`: the same registry, pull secret, ingress class and certificate.

## What runs

- **One container.** `server.py` serves the page, the static files and the WebSocket the voice
  streams over. It listens on `0.0.0.0:8080` inside the container (`HOST` in the Dockerfile).
- **One replica.** Login sessions and calls in progress live in the process's memory, and the
  customer memory is a SQLite file with one writer. Deploys use `Recreate`.
- **A 1 Gi volume** at `/data` for `memory.sqlite3`. It is kept if the release is uninstalled.
- **A Secret, `voxnova-keys`**, holding `SAMBANOVA_API_KEY`, `CARTESIA_API_KEY`, `DEMO_PASSPHRASE`
  and optionally `TAVILY_API_KEY` / `EXA_API_KEY`. The deploy script makes it from `.env`.
- **An Ingress** with long timeouts (one hour), because a call is one WebSocket and nginx closes
  idle connections after 60 seconds by default.

Access is the VPN plus the demo passphrase. The Auth0 sign-in from `sn-agent-swarms` can be added
later by copying its `oauth2-proxy.yaml` and ingress annotations.

## One-off setup

1. Ask Tamas which existing `*.cloud.snova.ai` name to reuse, and add it to `.env`:
   `VOXNOVA_HOST=<name>.cloud.snova.ai`
2. Check `.env` has `SAMBANOVA_API_KEY`, `CARTESIA_API_KEY` and `DEMO_PASSPHRASE`.
3. Install `kubectl`, `helm` and `gcloud`, sign in (`gcloud auth login`), and get the
   kubeconfig for the dev vcluster; its context is called `default`.
4. Let docker push to the registry: `scripts/build-image.sh setup-registry`.

## Every deploy

On the VPN, with everything committed:

```
PUSH=1 scripts/build-image.sh      # build linux/amd64 and push voxnova:<short sha>
scripts/deploy-dev.sh              # update the Secret, helm upgrade, check https://<host>/login
```

`scripts/deploy-dev.sh --dry-run` shows what it would do without changing anything, and
`--no-keys` leaves the Secret alone. Keys are never printed and never put on a command line; the
pod restarts on its own when a key changes.

## When something is wrong

```
kubectl --context default get pods -l app.kubernetes.io/instance=voxnova
kubectl --context default logs deploy/voxnova
```

- **Pod crash-looping with "SAMBANOVA_API_KEY is missing"**: the Secret is missing a key; rerun the
  deploy without `--no-keys`.
- **Page loads but the mic does not work**: the page is not on HTTPS; check the host and the TLS
  secret in `deploy/helm/voxnova/values.yaml`.
- **Calls drop after a minute of silence**: the ingress timeouts are not applied; check the
  annotations on `kubectl get ingress voxnova -o yaml`.
