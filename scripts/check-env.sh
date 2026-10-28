#!/usr/bin/env bash
# Is .env ready to deploy with? Names and presence only — this never
# prints a value, and never writes anything.
set -u
cd "$(dirname "$0")/.."
missing=0

have() {
    # Reads .env without sourcing it, so nothing in it can run.
    grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" .env 2>/dev/null \
        | tail -1 | sed "s/^[^=]*=//" | tr -d '"'"'"' \r'
}

check() {   # name, required?, note
    local value; value="$(have "$1")"
    if [ -n "$value" ]; then
        printf '  set      %-22s %s\n' "$1" "${3:-}"
    elif [ "$2" = "required" ]; then
        printf '  MISSING  %-22s %s\n' "$1" "${3:-}"
        missing=$((missing + 1))
    else
        printf '  --       %-22s %s\n' "$1" "${3:-optional}"
    fi
}

expect() {  # name, wanted value, note
    local value; value="$(have "$1")"
    if [ "$value" = "$2" ]; then
        printf '  set      %-22s = %s\n' "$1" "$2"
    else
        printf '  WRONG    %-22s should be %s %s\n' "$1" "$2" "${3:-}"
        missing=$((missing + 1))
    fi
}

[ -f .env ] || { echo "  no .env in $(pwd)"; exit 1; }
echo "Credentials (values never shown)"
check SAMBANOVA_API_KEY required "the model; the app will not start without it"
check CARTESIA_API_KEY  required "speech; the app will not start without it"
check DEMO_PASSPHRASE   required "without it the pod makes a new one each restart"
check TAVILY_API_KEY    optional "web search; falls back to DuckDuckGo"
check EXA_API_KEY       optional

echo
echo "Deployment"
expect VOXNOVA_HOST aiskshowcase1.cloud.snova.ai
check  KUBECONFIG required "path to the vcluster kubeconfig, outside the repo"
check  KUBE_CONTEXT optional "read from the kubeconfig if unset"
check  TRUSTED_PROXIES required "the pod CIDR, 100.64.0.0/12 — deploy refuses without it"

echo
echo "Settings this release needs"
expect DEMO_INBOX 1 "(the code shows on screen; there is no mail sender yet)"

echo
if [ "$missing" -eq 0 ]; then
    echo "  ready"
else
    echo "  $missing thing(s) to fix before deploying"
fi
exit "$missing"
