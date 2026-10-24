#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

cd "${PROJECT_ROOT}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[ERROR] 应用尚未安装，请先运行 ./scripts/linux/install_kylin.sh。" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "[ERROR] 未找到 .env，请先完成部署配置。" >&2
  exit 1
fi

if [[ "${1:-web}" == "worker" ]]; then
  exec env PYTHONPATH="${PROJECT_ROOT}/backend" "${PYTHON_BIN}" -m app.worker
fi

mapfile -t APP_BIND < <(
  PYTHONPATH="${PROJECT_ROOT}/backend" "${PYTHON_BIN}" - <<'PY'
from app.config import settings

print(settings.app_host)
print(settings.app_port)
PY
)

APP_HOST_VALUE="${APP_BIND[0]:-0.0.0.0}"
APP_PORT_VALUE="${APP_BIND[1]:-8000}"

echo "启动切割生产智能产报系统：http://${APP_HOST_VALUE}:${APP_PORT_VALUE}"
echo "按 Ctrl+C 停止。"
echo "后台任务需独立启动：./scripts/linux/start_kylin.sh worker"

exec "${PYTHON_BIN}" -m uvicorn app.main:app \
  --app-dir backend \
  --host "${APP_HOST_VALUE}" \
  --port "${APP_PORT_VALUE}" \
  --workers 1
