#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
UV_BIN="${UV_BIN:-uv}"
VENV_DIR="${PROJECT_ROOT}/.venv"

cd "${PROJECT_ROOT}"

if ! command -v "${UV_BIN}" >/dev/null 2>&1; then
  echo '[ERROR] 请先安装 uv 0.13.6；也可通过 UV_BIN 指定离线 uv 二进制。' >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "[ERROR] 未找到 ${PYTHON_BIN}，请先安装 64 位 Python 3.11 或 3.12。" >&2
  exit 1
fi

"${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info[:2] not in {(3, 11), (3, 12)}:
    raise SystemExit(
        f"[ERROR] 当前 Python 为 {sys.version.split()[0]}，请使用 Python 3.11 或 3.12。\n"
        "如果命令不是 python3，请使用：PYTHON_BIN=python3.11 ./scripts/linux/install_kylin.sh"
    )
print(f"使用 Python {sys.version.split()[0]}")
PY

if [[ -L "${VENV_DIR}" || ( -e "${VENV_DIR}" && ! -x "${VENV_DIR}/bin/python" ) ]]; then
  echo '[ERROR] .venv 必须是本项目的独立有效环境，请先保留原目录并重新安装。' >&2
  exit 1
fi

SYNC_PYTHON="${PYTHON_BIN}"
if [[ -x "${VENV_DIR}/bin/python" ]]; then
  "${VENV_DIR}/bin/python" - <<'PYTHON'
import sys
if sys.version_info[:2] not in {(3, 11), (3, 12)}:
    raise SystemExit("[ERROR] 现有 .venv 需要 Python 3.11 或 3.12；请先保留旧环境并重建，不会自动覆盖。")
PYTHON
  SYNC_PYTHON="${VENV_DIR}/bin/python"
fi

echo "安装应用依赖……"
UV_PROJECT_ENVIRONMENT="${VENV_DIR}" UV_LINK_MODE=copy \
  "${UV_BIN}" sync --project backend --locked --no-dev --extra sqlserver --no-build \
  --no-python-downloads --python "${SYNC_PYTHON}"

echo "安装 PDF 导出所需的 Chromium……"
"${VENV_DIR}/bin/python" -m playwright install chromium

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "已从 .env.example 创建 .env，请先完成配置。"
else
  chmod 600 .env
  echo "已保留部署包中的 .env。"
fi

mkdir -p data reports logs

echo
echo "安装完成。启动命令："
echo "  ./scripts/linux/start_kylin.sh"
