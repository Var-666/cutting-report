#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${PROJECT_ROOT}/.venv"

cd "${PROJECT_ROOT}"

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

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "创建 Python 虚拟环境……"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

echo "安装应用依赖……"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install \
  -r backend/requirements.txt \
  -r backend/requirements-sqlserver.txt

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
