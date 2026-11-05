#!/usr/bin/env bash
# Resume the verified, paused SQLite import; never re-run the first-upgrade script.
set -Eeuo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
BACKUP_DIR="${1:-/data/cutting-before-upgrade-SR9p3Ufy}"
compose=(docker compose -f "$PROJECT_ROOT/compose.yaml" -f "$PROJECT_ROOT/compose.offline.yaml")
started=false
failed() {
  local code="${1:-$?}"
  trap - ERR INT TERM
  if $started; then "${compose[@]}" stop app scheduler worker automation >&2 || true; fi
  echo "[失败] 已保留现有卷和备份：${BACKUP_DIR}。不自动回退，不清空数据库。" >&2
  exit "$code"
}
trap failed ERR
trap 'failed 130' INT
trap 'failed 143' TERM
[[ -s "$BACKUP_DIR/app-files.tgz" ]] || { echo '[ERROR] 缺少已完成的数据备份，停止。' >&2; exit 1; }
cd "$PROJECT_ROOT"
exec 9>"$(dirname -- "$BACKUP_DIR")/.cutting-report-upgrade.lock"
flock -n 9 || { echo '[ERROR] 已有升级操作。' >&2; exit 1; }
[[ -z "$("${compose[@]}" ps --status running -q app scheduler worker automation)" ]] || {
  echo '[ERROR] 应用角色仍在运行，不重复执行切换；请先核对现场状态。' >&2; exit 1;
}
image="$("${compose[@]}" config --images app scheduler worker automation | sed '/^postgres:/d' | sort -u)"
[[ -n "$image" && "$image" != *$'\n'* ]] || { echo '[ERROR] 应用镜像不一致。' >&2; exit 1; }
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 120 postgres
pg_backup="$(mktemp "$BACKUP_DIR/pg-before-legacy-fix-XXXXXXXX")"
"${compose[@]}" exec -T postgres sh -c 'pg_dump -U postgres -d "$POSTGRES_DB" -Fc' > "$pg_backup"
[[ -s "$pg_backup" ]]
migrate=("${compose[@]}" run --rm --no-deps -T migrate python scripts/migrate_sqlite_to_postgres.py)
"${migrate[@]}" schema
for action in check migrate verify; do
  "${migrate[@]}" "$action" --sqlite /app/data/metadata.db > "$BACKUP_DIR/${action}-legacy-fix.json"
  docker run --rm -i --network none "$image" python -c '
import json,sys
r=json.load(sys.stdin)
expected="ready" if sys.argv[1]=="check" else "verified"
assert r["result"]==expected, "迁移或校验未通过"
print(sys.argv[1],r["result"],"表数",len(r["tables"]))
' "$action" < "$BACKUP_DIR/${action}-legacy-fix.json"
done
"${migrate[@]}" prepare-consumers
started=true
"$SCRIPT_DIR/start_offline_docker.sh"
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 180 app scheduler worker automation
"${compose[@]}" exec -T app python -c '
import json,urllib.request
r=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health",timeout=10))
assert r["success"] and r["data"]["background_ready"], "后台未就绪"
'
echo "[成功] 历史数据已导入校验，四角色已启动。维护记录：${BACKUP_DIR}"
