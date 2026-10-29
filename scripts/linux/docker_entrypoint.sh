#!/bin/sh
set -eu

if ! sha256sum --check --status /opt/python-dependencies.sha256; then
  echo '[ERROR] 源码包的 Python 依赖与运行镜像不一致，请使用配套重建的镜像。' >&2
  exit 1
fi
exec "$@"
