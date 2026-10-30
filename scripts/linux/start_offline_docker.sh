#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

if [[ ! -f .env ]]; then
  echo "[ERROR] 未找到 .env。" >&2
  exit 1
fi

# Check only public lock inputs; never mount data or credentials here.
image="$(docker compose -f compose.yaml -f compose.offline.yaml config --images | sort -u)"
if [[ -z "${image}" || "${image}" == *$'\n'* ]]; then
  echo '[ERROR] Web 和 Worker 必须使用同一运行镜像。' >&2
  exit 1
fi
if ! docker run --rm --network none \
  --mount "type=bind,src=${PROJECT_ROOT}/backend/pyproject.toml,dst=/app/backend/pyproject.toml,readonly" \
  --mount "type=bind,src=${PROJECT_ROOT}/backend/uv.lock,dst=/app/backend/uv.lock,readonly" \
  --mount "type=bind,src=${PROJECT_ROOT}/backend/.python-version,dst=/app/backend/.python-version,readonly" \
  --entrypoint /usr/local/bin/cutting-entrypoint "${image}" test -r /opt/python-project.sha256; then
  echo '[ERROR] 请导入使用当前 uv.lock 构建的配套镜像，不能复用旧版依赖镜像。' >&2
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
