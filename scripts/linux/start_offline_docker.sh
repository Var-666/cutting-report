#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

if [[ ! -f .env ]]; then
  echo "[ERROR] 未找到 .env。" >&2
  exit 1
fi

# Check only the public dependency manifest; never mount data or credentials here.
image="$(docker compose -f compose.yaml -f compose.offline.yaml config --images | sort -u)"
if [[ -z "${image}" || "${image}" == *$'\n'* ]]; then
  echo '[ERROR] Web 和 Worker 必须使用同一运行镜像。' >&2
  exit 1
fi
docker run --rm --network none \
  --mount "type=bind,src=${PROJECT_ROOT}/backend/requirements-sqlserver.txt,dst=/app/backend/requirements-sqlserver.txt,readonly" \
  --entrypoint /usr/local/bin/cutting-entrypoint "${image}" true

docker compose \
  -f compose.yaml \
  -f compose.offline.yaml \
  up -d --no-build --pull never --force-recreate

docker compose \
  -f compose.yaml \
  -f compose.offline.yaml \
  ps
