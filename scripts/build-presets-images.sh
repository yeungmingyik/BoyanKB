#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export BOYANKB_APP_IMAGE="${BOYANKB_APP_IMAGE:-boyankb/app:v0.8.2-presets-v2}"
export BOYANKB_FRONTEND_IMAGE="${BOYANKB_FRONTEND_IMAGE:-boyankb/frontend:v0.8.2-presets-v2}"
bash scripts/build-image.sh --with-app
docker build --file deployment/document-renderer/Dockerfile --tag boyankb/document-renderer:boyan-v2 .
