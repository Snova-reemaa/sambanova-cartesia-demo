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
#   VOXNOVA_HOST (required: the *.cloud.snova.ai name to serve on), KUBE_CONTEXT (default ai-starter-kits-dev-1),
#   NAMESPACE (default default), and the keys SAMBANOVA_API_KEY, CARTESIA_API_KEY, DEMO_PASSPHRASE,
#   TAVILY_API_KEY, EXA_API_KEY.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

REGISTRY="us-west2-docker.pkg.dev/acp-starterkits-development-57/oci-us-west2-starerkits"
RELEASE="voxnova"
SECRET_NAME="voxnova-keys"
KEY_NAMES="SAMBANOVA_API_KEY CARTESIA_API_KEY DEMO_PASSPHRASE TAVILY_API_KEY EXA_API_KEY"
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
ENV_NAMES=" VOXNOVA_HOST KUBE_CONTEXT NAMESPACE $KEY_NAMES "
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

KUBE_CONTEXT="${KUBE_CONTEXT:-ai-starter-kits-dev-1}"
NAMESPACE="${NAMESPACE:-default}"
if [ -z "${VOXNOVA_HOST:-}" ]; then
    echo "error: VOXNOVA_HOST is not set — the full hostname for the aiskvoicedemo slot (an existing *.cloud.snova.ai name; Tamas has it)" >&2
    exit 1
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
    ${checksum_args[@]+"${checksum_args[@]}"} --wait --timeout 5m)
[ "$DRY_RUN" = 1 ] && helm_args+=(--dry-run=server)
log "helm upgrade --install $RELEASE (tag $TAG)"
helm "${helm_args[@]}"

[ "$DRY_RUN" = 1 ] && { log "Dry run: nothing changed"; exit 0; }

# --- 5. check it answers -------------------------------------------------------------------------
kc get pods -l "app.kubernetes.io/instance=$RELEASE"
url="https://$VOXNOVA_HOST/login"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url" || true)"
if [ "$code" = 200 ]; then
    log "Up: https://$VOXNOVA_HOST"
else
    echo "warning: $url answered $code; check: kubectl --context $KUBE_CONTEXT -n $NAMESPACE logs deploy/$RELEASE" >&2
fi
