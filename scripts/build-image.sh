#!/usr/bin/env bash
# Build the VoxNova image, tagged with the short git SHA, for linux/amd64 (the cluster's platform,
# whatever laptop builds it).
#
#   scripts/build-image.sh                 # build and load into the local docker
#   PUSH=1 scripts/build-image.sh          # build and push to the registry
#   scripts/build-image.sh setup-registry  # one-off: let docker push to the registry (needs gcloud)
#
# Variables: REGISTRY, TAG (default: short SHA of HEAD), PLATFORM (default linux/amd64), PUSH=1.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

REGISTRY="${REGISTRY:-us-west2-docker.pkg.dev/acp-starterkits-development-57/oci-us-west2-starerkits}"
REGISTRY_HOST="${REGISTRY%%/*}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
PLATFORM="${PLATFORM:-linux/amd64}"
IMAGE="${REGISTRY}/voxnova:${TAG}"

if [ "${1:-}" = "setup-registry" ]; then
    gcloud auth configure-docker "${REGISTRY_HOST}" --quiet
    exit 0
fi

# An image tagged with a commit should contain that commit. Uncommitted changes would be built
# into an image whose tag says otherwise.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "error: uncommitted changes; commit them first so the tag ($TAG) matches what is built" >&2
    exit 1
fi

if [ "${PUSH:-0}" = 1 ]; then
    output="--push"
else
    output="--load"
fi

echo "==> Building $IMAGE ($PLATFORM)"
docker buildx build --platform "$PLATFORM" -t "$IMAGE" $output .
echo "==> Done: $IMAGE"
[ "${PUSH:-0}" = 1 ] || echo "    (loaded locally; PUSH=1 to push it to the registry)"
