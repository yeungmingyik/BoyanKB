#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
with_app=false
case "${1:-}" in
  '') ;;
  --with-app) with_app=true ;;
  *) printf '%s\n' 'Usage: bash scripts/build-image.sh [--with-app]' >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  printf '%s\n' 'Usage: bash scripts/build-image.sh [--with-app]' >&2
  exit 2
fi
command -v docker >/dev/null 2>&1 || { printf '%s\n' 'Docker is required.' >&2; exit 1; }
docker info >/dev/null
commit_id="$(git rev-parse --short HEAD 2>/dev/null || printf unknown)"
docker build \
  --file docker/Dockerfile.frontend-boyankb \
  --build-arg "VITE_FRONTEND_COMMIT=${VITE_FRONTEND_COMMIT:-$commit_id}" \
  --build-arg "NPM_REGISTRY=${NPM_REGISTRY:-}" \
  --build-arg "NODE_MAX_OLD_SPACE_SIZE=${NODE_MAX_OLD_SPACE_SIZE:-4096}" \
  --tag "${BOYANKB_FRONTEND_IMAGE:-boyankb/frontend:v0.8.2}" .
if "$with_app"; then
  docker build \
    --file docker/Dockerfile.app-boyankb \
    --build-arg "BRANDING_COMMIT=$commit_id" \
    --tag "${BOYANKB_APP_IMAGE:-boyankb/app:v0.8.2}" .
fi
