#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

if [[ ! -f .env ]]; then
  echo "[ERROR] 未找到 .env。" >&2
  exit 1
fi

docker compose \
  -f compose.yaml \
  -f compose.offline.yaml \
  up -d --no-build --pull never --force-recreate

docker compose \
  -f compose.yaml \
  -f compose.offline.yaml \
  ps
