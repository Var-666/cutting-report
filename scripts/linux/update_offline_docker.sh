#!/usr/bin/env bash
# Existing PostgreSQL deployment only; keep the database and file volumes.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
OLD_DIR="${1:-/opt/cutting-report-kylin/cutting-report-kylin-offline-docker-20261104-v1}"
BACKUP_ROOT="${2:-/data}"
stage='预检查'
backup_dir=""
started=false
compose=(docker compose -f "$PROJECT_ROOT/compose.yaml" -f "$PROJECT_ROOT/compose.offline.yaml")
failed() {
  local code="${1:-$?}"
  trap - ERR INT TERM
  if $started; then "${compose[@]}" stop app scheduler worker automation >&2 || true; fi
  echo "[失败] 阶段：${stage}。保留现有数据，不自动回退。" >&2
  [[ -z "$backup_dir" ]] || echo "备份：$backup_dir" >&2
  exit "$code"
}
trap failed ERR
trap 'failed 130' INT
trap 'failed 143' TERM
fail() { echo "[ERROR] $*" >&2; failed 1; }

[[ $# -le 2 && "$(id -u)" == 0 && "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
  echo '[ERROR] 以root执行：update_offline_docker.sh [旧部署目录] [备份目录，默认/data]' >&2; exit 1;
}
OLD_DIR="$(cd -- "$OLD_DIR" && pwd -P)"
[[ "$OLD_DIR" != "$PROJECT_ROOT" && -d "$BACKUP_ROOT" ]] || fail '旧目录不能为新目录，备份目录须已存在。'
for file in .env compose.yaml compose.offline.yaml; do
  [[ -f "$OLD_DIR/$file" && -f "$PROJECT_ROOT/$file" ]] || fail "新旧目录缺少$file"
done
[[ -s "$PROJECT_ROOT/PACKAGE-MANIFEST.json" ]] || fail '缺少部署包清单。'
cd "$PROJECT_ROOT"
exec 9>"$BACKUP_ROOT/.cutting-report-upgrade.lock"
flock -n 9
old=(docker compose -f "$OLD_DIR/compose.yaml" -f "$OLD_DIR/compose.offline.yaml")
pg="$("${old[@]}" ps --status running -q postgres)"
app="$("${old[@]}" ps -a -q app)"
[[ -n "$pg" && -n "$app" && "$pg" != *$'\n'* && "$app" != *$'\n'* ]] || fail '未找到唯一Web和运行中的PostgreSQL。'
[[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$app")" == cutting-report ]] || fail '旧容器不属于cutting-report。'
for pair in data reports logs; do
  [[ "$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"/app/$pair\"}}{{.Name}}{{end}}{{end}}" "$app")" == "cutting-report_cutting_$pair" ]] || fail "旧文件卷不匹配：$pair"
done
[[ "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql"}}{{.Name}}{{end}}{{end}}' "$pg")" == cutting-report_postgres_data ]] || fail '旧PostgreSQL卷不匹配。'
image="$("${compose[@]}" config --images app scheduler worker automation | sed '/^postgres:/d' | sort -u)"
[[ -n "$image" && "$image" != *$'\n'* ]] || fail '四个应用角色的镜像不一致。'

# Verify the extracted package and existing PG credentials before stopping services.
docker run --rm --user 0:0 --network none \
  -v "$OLD_DIR/.env:/old.env:ro,z" -v "$PROJECT_ROOT:/release:ro,z" \
  -i "$image" python - <<'PY'
import hashlib, json
from pathlib import Path
from dotenv import dotenv_values
root = Path('/release').resolve()
files = json.loads((root / 'PACKAGE-MANIFEST.json').read_text())['files']
assert files, '包清单为空'
for name, expected in files.items():
    if name == '.env':
        continue  # Deployment configuration can legitimately differ from the packaged defaults.
    path = root / name
    assert path.resolve().is_relative_to(root), '包路径越界'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, '包文件校验失败：' + name
old, new = dotenv_values('/old.env'), dotenv_values(root / '.env')
keys = ('POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DATABASE', 'POSTGRES_USER',
        'POSTGRES_PASSWORD', 'POSTGRES_BOOTSTRAP_PASSWORD', 'POSTGRES_MIGRATOR_PASSWORD')
changed = [key for key in keys if not new.get(key) or old.get(key) != new[key]]
assert not changed, '请保留现场PG配置：' + ','.join(changed)
PY

backup_dir="$(mktemp -d "$BACKUP_ROOT/cutting-before-update-XXXXXXXX")"
echo "备份：$backup_dir"
cp -p "$OLD_DIR/.env" "$backup_dir/env.before"
cp -p "$OLD_DIR/compose.yaml" "$OLD_DIR/compose.offline.yaml" "$backup_dir/"
stage='停止应用并备份'
"${old[@]}" stop app scheduler worker automation
"${old[@]}" exec -T postgres sh -ec 'pg_dump -U postgres -d "$POSTGRES_DB" -Fc' > "$backup_dir/postgres.dump"
[[ -s "$backup_dir/postgres.dump" ]] || fail 'PG备份为空。'
docker run --rm --user 0:0 --network none \
  -v cutting-report_cutting_data:/app/data:ro \
  -v cutting-report_cutting_reports:/app/reports:ro \
  -v cutting-report_cutting_logs:/app/logs:ro \
  -v "$backup_dir:/backup:z" "$image" sh -ec \
  'tar -czf /backup/app-files.tgz -C /app data reports logs; tar -tzf /backup/app-files.tgz >/dev/null'
[[ -s "$backup_dir/app-files.tgz" ]] || fail '文件备份为空。'

stage='追加数据库迁移'
"${compose[@]}" run --rm --no-deps -T migrate python scripts/manage_postgres.py schema
stage='启动及健康检查'
started=true
"$SCRIPT_DIR/start_offline_docker.sh"
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 180 app scheduler worker automation
"${compose[@]}" exec -T app python - <<'PY'
import json, urllib.request
r = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=10))
assert r['success'] and r['data']['background_ready'], '应用或后台未就绪'
PY
"${compose[@]}" ps
echo "[成功] 程序更新完成，备份保留在：$backup_dir"
