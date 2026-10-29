#!/usr/bin/env bash
# Deploy VoxNova to the dev cluster from a laptop on the VPN. Modelled on sn-agent-swarms'
# scripts/deploy-dev.sh.
#
#   scripts/deploy-dev.sh                 # deploy the image for HEAD's short SHA
#   scripts/deploy-dev.sh <tag>           # deploy a given tag
#   scripts/deploy-dev.sh --dry-run       # show what it would do; helm runs with --dry-run=server
#   scripts/deploy-dev.sh --no-keys       # leave the key Secret as it is
#
# Settings come from the environment, else from .env (read as data, never run):
#   VOXNOVA_HOST (required: the *.cloud.snova.ai name to serve on), KUBE_CONTEXT (read from the kubeconfig, else ai-starter-kits-dev-1),
#   NAMESPACE (default default), and the keys SAMBANOVA_API_KEY, CARTESIA_API_KEY, DEMO_PASSPHRASE,
#   TAVILY_API_KEY, EXA_API_KEY.
#   Auth0 (off unless VOXNOVA_AUTH=1): AUTH0_ISSUER_URL, and the keys AUTH0_CLIENT_ID,
#   AUTH0_CLIENT_SECRET, AUTH0_COOKIE_SECRET, which go into the same Secret as the others.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

REGISTRY="us-west2-docker.pkg.dev/acp-starterkits-development-57/oci-us-west2-starerkits"
RELEASE="voxnova"
SECRET_NAME="voxnova-keys"
KEY_NAMES="SAMBANOVA_API_KEY CARTESIA_API_KEY DEMO_PASSPHRASE TAVILY_API_KEY EXA_API_KEY AUTH0_CLIENT_ID AUTH0_CLIENT_SECRET AUTH0_COOKIE_SECRET"
REQUIRED_KEYS="SAMBANOVA_API_KEY CARTESIA_API_KEY DEMO_PASSPHRASE"

DRY_RUN=0
NO_KEYS=0
TAG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --no-keys) NO_KEYS=1; shift ;;
        -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) echo "unknown flag: $1" >&2; exit 2 ;;
        *) TAG="$1"; shift ;;
    esac
done

log() { echo "==> $*"; }

# --- .env: only these names are taken, and the environment wins over the file ----------------
ENV_NAMES=" VOXNOVA_HOST KUBE_CONTEXT NAMESPACE TRUSTED_PROXIES KUBECONFIG VOXNOVA_AUTH AUTH0_ISSUER_URL $KEY_NAMES "
from_file=""
if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in ''|'#'*) continue ;; esac
        line="${line#export }"
        case "$line" in *=*) ;; *) continue ;; esac
        key="${line%%=*}"; val="${line#*=}"
        case "$ENV_NAMES" in *" $key "*) ;; *) continue ;; esac
        case "$val" in \"*\") val="${val#\"}"; val="${val%\"}" ;; \'*\') val="${val#\'}"; val="${val%\'}" ;; esac
        if [ -z "${!key+x}" ] && [ -n "$val" ]; then export "$key=$val"; from_file="$from_file $key"; fi
    done < .env
fi
[ -n "$from_file" ] && log "From .env:$from_file"

# A vcluster on on-prem RKE2, not GKE: the kubeconfig is a file fetched
# from storage rather than something gcloud writes. Keep it outside the
# repo and chmod 600 — it is a credential.
if [ -n "${KUBECONFIG:-}" ]; then
    [ -f "$KUBECONFIG" ] || { echo "error: KUBECONFIG=$KUBECONFIG does not exist" >&2; exit 1; }
    export KUBECONFIG
    # The context in that file is whatever the vcluster called itself,
    # which is not necessarily the cluster's name. Read it rather than
    # assume it.
    if [ -z "${KUBE_CONTEXT:-}" ]; then
        KUBE_CONTEXT="$(kubectl config current-context 2>/dev/null)"
        [ -n "$KUBE_CONTEXT" ] && log "Context from kubeconfig: $KUBE_CONTEXT"
    fi
fi
KUBE_CONTEXT="${KUBE_CONTEXT:-ai-starter-kits-dev-1}"
NAMESPACE="${NAMESPACE:-default}"
if [ -z "${VOXNOVA_HOST:-}" ]; then
    echo "error: VOXNOVA_HOST is not set — the full hostname for the aiskshowcase1 slot" >&2
    exit 1
fi

# Behind the ingress every request arrives from the ingress pod. Without
# this the app cannot tell callers apart, so they share one identity:
# three wrong verification codes from anybody locks out everybody for
# fifteen minutes, and six code requests exhausts the hour for the whole
# deployment. That is a quiet failure — everything looks fine until two
# people use it at once — so it is a refusal rather than a warning.
if [ -z "${TRUSTED_PROXIES:-}" ]; then
    cat >&2 <<'NOTE'
error: TRUSTED_PROXIES is not set.

  It must be the ingress controller's pod CIDR, or a prefix of it, so the
  app can tell one caller from another behind the proxy. Left empty, every
  caller shares one rate-limit identity and three wrong codes from anybody
  locks out everybody.

  Find it with:
    kubectl --context "$KUBE_CONTEXT" -n ingress-nginx get pods       -o jsonpath='{.items[*].status.podIP}'

  Then put it in .env, e.g.  TRUSTED_PROXIES=10.4.

  To deploy without it anyway (single-tester demo only):
    ALLOW_SHARED_CALLER_KEY=1 scripts/deploy-dev.sh
NOTE
    [ -n "${ALLOW_SHARED_CALLER_KEY:-}" ] || exit 1
    log "ALLOW_SHARED_CALLER_KEY set — deploying with one shared caller key"
fi
kc() { kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" "$@"; }

# --- 1. cluster reachable ---------------------------------------------------------------------
log "Context: $KUBE_CONTEXT, namespace: $NAMESPACE, host: $VOXNOVA_HOST"
if ! kubectl --context "$KUBE_CONTEXT" get ns "$NAMESPACE" >/dev/null 2>&1; then
    echo "error: cannot reach namespace '$NAMESPACE' on context '$KUBE_CONTEXT'" >&2
    echo "hint: is the VPN on?" >&2
    exit 1
fi

# --- 2. image exists ----------------------------------------------------------------------------
TAG="${TAG:-$(git rev-parse --short HEAD)}"
IMAGE="${REGISTRY}/voxnova:${TAG}"
log "Image: $IMAGE"
if ! gcloud artifacts docker images describe "$IMAGE" >/dev/null 2>&1; then
    echo "error: $IMAGE is not in the registry" >&2
    echo "hint: PUSH=1 scripts/build-image.sh, or gcloud is not signed in (gcloud auth login)" >&2
    exit 1
fi

# --- Auth0, only when asked for: the passphrase gives way to sign-in, so the three Auth0 keys
# and the issuer become required instead of it.
auth_args=()
if [ "${VOXNOVA_AUTH:-0}" = 1 ]; then
    REQUIRED_KEYS="SAMBANOVA_API_KEY CARTESIA_API_KEY AUTH0_CLIENT_ID AUTH0_CLIENT_SECRET AUTH0_COOKIE_SECRET"
    if [ -z "${AUTH0_ISSUER_URL:-}" ]; then echo "error: VOXNOVA_AUTH=1 needs AUTH0_ISSUER_URL" >&2; exit 1; fi
    auth_args=(--set "auth.enabled=true" --set "auth.issuerUrl=$AUTH0_ISSUER_URL")
    log "Auth0 sign-in: on"
fi

# --- 3. keys into the Secret (never printed, never on a command line) ---------------------------
tmpfile=""
cleanup() { if [ -n "$tmpfile" ]; then rm -f "$tmpfile"; fi; return 0; }
trap cleanup EXIT
checksum_args=()

if [ "$NO_KEYS" = 0 ]; then
    for name in $REQUIRED_KEYS; do
        if [ -z "${!name:-}" ]; then echo "error: $name is not set" >&2; exit 1; fi
    done
    tmpfile="$(mktemp)"; chmod 600 "$tmpfile"
    names=""
    for name in $KEY_NAMES; do
        if [ -n "${!name:-}" ]; then printf '%s=%s\n' "$name" "${!name}" >> "$tmpfile"; names="$names $name"; fi
    done
    log "Secret $SECRET_NAME:$names"
    # A hash of the values (never the values) goes on the pod, so it restarts when a key changes.
    sum="$( (shasum -a 256 "$tmpfile" 2>/dev/null || sha256sum "$tmpfile") | cut -c1-16)"
    checksum_args=(--set "secretChecksum=$sum")
    if [ "$DRY_RUN" = 1 ]; then
        log "(dry run) would apply the Secret"
    else
        kc create secret generic "$SECRET_NAME" --from-env-file="$tmpfile" --dry-run=client -o yaml \
            | kc apply -f - >/dev/null
    fi
else
    log "Keeping the existing Secret $SECRET_NAME (--no-keys)"
    # Keep the pod's checksum as it is, so it is not restarted for nothing.
    current="$(helm --kube-context "$KUBE_CONTEXT" -n "$NAMESPACE" get values "$RELEASE" -o json 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("secretChecksum",""))' 2>/dev/null || true)"
    [ -n "$current" ] && checksum_args=(--set "secretChecksum=$current")
fi

# --- 4. helm ------------------------------------------------------------------------------------
helm_args=(upgrade --install "$RELEASE" deploy/helm/voxnova
    --kube-context "$KUBE_CONTEXT" --namespace "$NAMESPACE"
    --set "image.tag=$TAG" --set "ingress.host=$VOXNOVA_HOST"
    --set "env.TRUSTED_PROXIES=${TRUSTED_PROXIES:-}"
    ${auth_args[@]+"${auth_args[@]}"}
    ${checksum_args[@]+"${checksum_args[@]}"} --wait --timeout 5m)
[ "$DRY_RUN" = 1 ] && helm_args+=(--dry-run=server)
log "helm upgrade --install $RELEASE (tag $TAG)"
helm "${helm_args[@]}"

[ "$DRY_RUN" = 1 ] && { log "Dry run: nothing changed"; exit 0; }

# --- 5. check it answers -------------------------------------------------------------------------
kc get pods -l "app.kubernetes.io/instance=$RELEASE"
url="https://$VOXNOVA_HOST/login"
answer="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "$url" || true)"
code="${answer%% *}"
if [ "$code" = 200 ]; then
    log "Up: https://$VOXNOVA_HOST"
elif [ "${VOXNOVA_AUTH:-0}" = 1 ] && [ "$code" = 302 ] && [[ "$answer" == *"/oauth2/start"* ]]; then
    # Behind Auth0 a stranger is sent to sign in, which is the site answering.
    log "Up behind Auth0: https://$VOXNOVA_HOST (sends strangers to sign in)"
else
    echo "warning: $url answered $code; check: kubectl --context $KUBE_CONTEXT -n $NAMESPACE logs deploy/$RELEASE" >&2
fi
