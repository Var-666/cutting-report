#!/usr/bin/env bash
# First SQLite -> PostgreSQL cutover only; normal updates use start_offline_docker.sh.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
OLD_DIR="${1:-/opt/cutting-report-kylin/cutting-report-kylin-offline-docker-20261103-v2}"
BACKUP_ROOT="${2:-/data}"
stage=预检查
backup_dir=""
new_started=false
compose=(docker compose -f "${PROJECT_ROOT}/compose.yaml" -f "${PROJECT_ROOT}/compose.offline.yaml")

fail() {
  echo "[ERROR] $*" >&2
  [[ -z "$backup_dir" ]] || echo "备份与维护记录：${backup_dir}" >&2
  exit 1
}
failed() {
  local code="${1:-$?}"
  trap - ERR INT TERM
  if $new_started; then
    "${compose[@]}" stop app scheduler worker automation >&2 || true
  fi
  echo "[失败] 阶段：${stage}。已停止后续步骤，不会自动清库或恢复旧版。" >&2
  [[ -z "$backup_dir" ]] || echo "备份与维护记录：${backup_dir}" >&2
  exit "$code"
}
trap failed ERR
trap 'failed 130' INT
trap 'failed 143' TERM

[[ $# -le 2 ]] || fail '用法：upgrade_offline_docker.sh [旧部署目录] [备份所在目录，默认/data]'
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || fail '本脚本用于麒麟/Linux x86_64服务器。'
[[ "$(id -u)" == 0 ]] || fail '请使用root或sudo运行，以读取旧.env和备份数据卷。'
for tool in docker sha256sum flock df du awk mktemp; do
  command -v "$tool" >/dev/null || fail "缺少命令：${tool}"
done
[[ -d "$OLD_DIR" && -d "$BACKUP_ROOT" ]] || fail '旧部署目录或备份所在目录不存在。'
OLD_DIR="$(cd -- "$OLD_DIR" && pwd -P)"
BACKUP_ROOT="$(cd -- "$BACKUP_ROOT" && pwd -P)"
[[ "$OLD_DIR" != "$PROJECT_ROOT" ]] || fail '旧目录不能是新包目录。'
for file in .env compose.yaml; do
  [[ -f "$OLD_DIR/$file" && -f "$PROJECT_ROOT/$file" ]] || fail "新旧目录必须都有${file}。"
done
[[ -f "$PROJECT_ROOT/SHA256SUMS" && -x "$SCRIPT_DIR/start_offline_docker.sh" ]] || fail '请将脚本放入完整离线包的scripts/linux目录。'
cd "$PROJECT_ROOT"
exec 9>"$BACKUP_ROOT/.cutting-report-upgrade.lock"
flock -n 9 || fail '已有本项目的升级脚本正在运行。'
docker info >/dev/null
docker compose version >/dev/null

old_compose=(docker compose --project-directory "$OLD_DIR" -f "$OLD_DIR/compose.yaml")
[[ ! -f "$OLD_DIR/compose.offline.yaml" ]] || old_compose+=(-f "$OLD_DIR/compose.offline.yaml")
old_ids=()
ids="$("${old_compose[@]}" ps -a -q)"
while IFS= read -r container; do
  [[ -z "$container" ]] || old_ids+=("$container")
done <<< "$ids"
[[ ${#old_ids[@]} -gt 0 && -n "${old_ids[0]}" ]] || fail '未找到旧项目容器，不能猜测实际数据卷。'
old_app=""
for container in "${old_ids[@]}"; do
  role="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container")"
  [[ "$role" == app || "$role" == worker ]] || fail '旧项目已有其他角色，请按现有PostgreSQL升级流程处理。'
  if [[ "$role" == app ]]; then
    [[ -z "$old_app" ]] || fail '旧项目存在多个Web容器，无法自动确定数据归属。'
    old_app="$container"
  fi
done
[[ -n "$old_app" ]] || fail '没有旧Web容器可用于核对数据卷。'
old_project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$old_app")"
volumes=()
for target in data reports logs; do
  volume="$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"/app/${target}\"}}{{if eq .Type \"volume\"}}{{.Name}}{{end}}{{end}}{{end}}" "$old_app")"
  [[ -n "$volume" ]] || fail "旧/app/${target}不是命名卷，请使用人工升级步骤。"
  docker volume inspect "$volume" >/dev/null
  volumes+=("$volume")
done
for container in "${old_ids[@]}"; do
  actual="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}' "$container")"
  [[ "$actual" == "${volumes[0]}" ]] || fail '旧容器没有共享同一个应用数据卷。'
done

stage=校验并导入离线镜像
sha256sum -c SHA256SUMS
shopt -s nullglob
images=("$PROJECT_ROOT"/images/*.tar.gz)
[[ ${#images[@]} == 1 ]] || fail '完整包中应有一个配套离线镜像归档。'
docker_root="$(docker info --format '{{.DockerRootDir}}')"
free_kb="$(df -Pk "$docker_root" | awk 'NR==2 {print $4}')"
image_kb="$(du -k "${images[0]}" | awk '{print $1}')"
[[ "$free_kb" -ge $((image_kb * 4 + 1048576)) ]] || fail 'Docker存储所在磁盘空间不足，请先处理空间，不会自动删除镜像。'
old_image="$(docker inspect --format '{{.Image}}' "$old_app")"
backup_dir="$(mktemp -d "$BACKUP_ROOT/cutting-before-upgrade-XXXXXXXX")"
chmod 700 "$backup_dir"
old_tag="cutting-report:before-${backup_dir##*-}"
docker tag "$old_image" "$old_tag"
printf '%s\n' "$OLD_DIR" > "$backup_dir/old-directory.txt"
printf '%s\n' "$old_tag" > "$backup_dir/old-image-tag.txt"
docker inspect "${old_ids[@]}" > "$backup_dir/old-containers.json"
cp -p "$OLD_DIR/.env" "$backup_dir/env.before"
cp -p "$OLD_DIR/compose.yaml" "$backup_dir/compose.before.yaml"
[[ ! -f "$OLD_DIR/compose.offline.yaml" ]] || cp -p "$OLD_DIR/compose.offline.yaml" "$backup_dir/compose.before.offline.yaml"
docker load -i "${images[0]}"
image="$("${compose[@]}" config --images app scheduler worker automation | sed '/^postgres:/d' | sort -u)"
[[ -n "$image" && "$image" != *$'\n'* ]] || fail '应用角色没有使用同一镜像。'
names="$("${compose[@]}" config --format json | docker run --rm -i --network none "$image" python -c '
import json,sys
c=json.load(sys.stdin)
print(c["name"])
for name in ("cutting_data","cutting_reports","cutting_logs","postgres_data"): print(c["volumes"][name]["name"])
')"
names_array=()
while IFS= read -r name; do names_array+=("$name"); done <<< "$names"
[[ ${#names_array[@]} == 5 && "${names_array[0]}" == "$old_project" ]] || fail '新旧Compose项目身份不一致。'
for index in 0 1 2; do
  [[ "${names_array[index+1]}" == "${volumes[index]}" ]] || fail '新旧数据卷名称不一致，拒绝连接空卷。'
done
# Only the first cutover is automated; an existing PG volume requires inspection.
if docker volume inspect "${names_array[4]}" >/dev/null 2>&1; then
  fail '目标PostgreSQL卷已经存在。请先核对迁移状态，本脚本不会覆盖或删除它。'
fi
docker run --rm --user 0:0 --network none -v "${volumes[0]}:/app/data:ro" "$image" \
  python -c 'from pathlib import Path; assert Path("/app/data/metadata.db").is_file(), "旧SQLite不存在，停止升级"'
used_kb="$(docker run --rm --user 0:0 --network none \
  -v "${volumes[0]}:/app/data:ro" -v "${volumes[1]}:/app/reports:ro" -v "${volumes[2]}:/app/logs:ro" \
  "$image" sh -c 'du -sk /app/data /app/reports /app/logs | awk "{sum+=\$1} END {print sum}"')"
free_kb="$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {print $4}')"
[[ "$free_kb" -ge $((used_kb + 1048576)) ]] || fail '备份盘空间不足。'

stage=停止旧服务并备份
echo "将升级项目${old_project}；旧目录${OLD_DIR}；备份${backup_dir}"
"${old_compose[@]}" stop
running="$(docker ps -q --filter "label=com.docker.compose.project=$old_project")"
[[ -z "$running" ]] || fail '旧项目仍有容器运行，拒绝备份和迁移。'
# WAL reads may create auxiliary files: keep SQL read-only, with app-owned writable directory.
docker run --rm --network none -v "${volumes[0]}:/app/data" -i "$image" python - <<'PY'
import sqlite3,time
deadline=time.monotonic()+40
while True:
    with sqlite3.connect('file:/app/data/metadata.db?mode=ro',uri=True) as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='process_leases'").fetchone()
        live=exists and c.execute('SELECT 1 FROM process_leases WHERE expires_at>?',(time.time(),)).fetchone()
    if not live: break
    if time.monotonic()>=deadline: raise SystemExit('仍有有效旧租约，停止迁移')
    time.sleep(1)
PY
docker run --rm --user 0:0 --network none \
  -v "${volumes[0]}:/app/data:ro" -v "${volumes[1]}:/app/reports:ro" -v "${volumes[2]}:/app/logs:ro" \
  -v "$backup_dir:/backup:z" "$image" sh -ec 'tar -czf /backup/app-files.tgz -C /app data reports logs; tar -tzf /backup/app-files.tgz >/dev/null'

stage=迁移现场配置
mkdir -p "$OLD_DIR/data"
docker run --rm --user 0:0 --network none -e APP_DATA_SOURCE_MODE=sqlserver \
  -v "$OLD_DIR:/legacy:ro,z" -v "${volumes[0]}:/legacy/data" -v "${volumes[0]}:/app/data" \
  "$image" sh -ec '
    python scripts/migrate_production_lines.py --root /legacy
    for f in /app/data/settings.env /app/data/settings.env.lock /app/data/.web-process.lock; do
      if [ -f "$f" ]; then chown app:app "$f"; fi
    done
  '
stage=升级并导入状态数据库
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 120 postgres
migrate=("${compose[@]}" run --rm --no-deps -T migrate python scripts/migrate_sqlite_to_postgres.py)
"${migrate[@]}" schema
"${compose[@]}" run --rm --no-deps -T migrate python scripts/migrate_database.py upgrade --database /app/data/metadata.db
"${migrate[@]}" check --sqlite /app/data/metadata.db
for action in migrate verify; do
  "${migrate[@]}" "$action" --sqlite /app/data/metadata.db | tee "$backup_dir/${action}.json"
  docker run --rm -i --network none "$image" python -c \
    'import json,sys; assert json.load(sys.stdin)["result"]=="verified", "导入或校验未通过"' < "$backup_dir/${action}.json"
done
stage=准备Consumer并启动
"${migrate[@]}" prepare-consumers
new_started=true
"$SCRIPT_DIR/start_offline_docker.sh"
stage=检查新版健康
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 180 app scheduler worker automation
"${compose[@]}" exec -T app python -c '
import json,urllib.request
r=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health",timeout=10))
assert r["success"] and r["data"]["background_ready"], "后台角色未就绪"
'
"${compose[@]}" ps
echo "[成功] 已升级。备份：${backup_dir}；旧镜像保留为${old_tag}。"
echo '请检查历史报表、确认、告警和现场连接。新增PG记录后不可直接回退旧SQLite。'
