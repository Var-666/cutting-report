# Web、Scheduler、Consumer、Automation 部署、扩缩容与回退

更新：2026-11-04，P7。仅支持同一主机、本地共享文件卷；默认1个Consumer，可按容量扩容。PostgreSQL队列本身可跨网络访问，不代表整个应用支持跨主机。

## 角色与实例

| Compose服务 | 入口 | 数量 / 职责 |
| --- | --- | --- |
| app | app.main:app，Uvicorn workers=1 | 单Web，HTTP、鉴权、查询及任务提交；原下载/派发恢复保留 |
| scheduler | python -m app.scheduler | 单Scheduler，生产周期任务入队；scheduler租约 |
| worker | python -m app.worker | 1～N个Consumer，后台与报表消费；worker:实例ID租约 |
| automation | python -m app.automation_worker | 单Automation，计划/补偿与SMTP；automation租约 |
| postgres | PostgreSQL 18.6 | 唯一状态库、任务队列、所有权、租约与心跳 |

复用同一个应用镜像，后台角色不开端口。Scheduler不执行SQL报表/AI/PDF/SMTP；Consumer不调度周期任务、不启动邮件线程。自动化计划创建并关联原report_jobs，Consumer完成回调与Automation补偿通过原outbox唯一约束衔接，邮件发送未知时仍保持unknown且不自动重发。

Docker Consumer用自身容器hostname作为实例ID，Compose显式清空共享CONSUMER_INSTANCE_ID并移除固定container_name。容器内重启保持该身份；重建容器可能产生新身份。原生部署必须显式设置不同CONSUMER_INSTANCE_ID，不能靠共享主机名区分进程。实例ID与每次获取租约生成的owner token不同，同ID第二个进程拒绝启动；重启得到新token。

## 升级门槛与顺序

**以下仅用于旧PG全局Worker协议切换。已完成切换的PG现场走[普通更新](OFFLINE_DOCKER部署说明.md#已有postgresql更新)，不重复prepare-consumers。当前版本不支持SQLite升级/导入，禁止混跑两种租约协议。**

1. 停止全部旧Consumer并禁用其自动重启；维护升级时同时停止其余应用角色，保留项目名、原卷和同一时点PG/配置/报表文件备份，不执行down -v。
2. 等待旧租约释放或30秒到期，确认旧进程已停止执行。用cutting_migrator运行下面的schema命令，追加003_consumer_scaling，001_initial/002_runtime校验和保持不变。003发现有效的旧/新Consumer租约时拒绝升级。
3. 显式运行prepare-consumers。该操作只允许cutting_migrator，拒绝任何有效Consumer租约，在事务内恢复中断后台/报表/能耗状态、保留业务ID和重试计数，并移除旧worker租约。旧无来源指纹的产线任务标为blocked/SOURCE_UNVERIFIED，不能猜测当前来源执行。此操作是停机维护，不由正常启动调用。
4. 启动同版本Web/Scheduler/Automation和1个Consumer，验证历史记录、探针及配置，再扩至2/3个。新增实例启动只恢复失效owner，不重置其他正常实例。

```bash
# Compose维护服务使用cutting_migrator；不把维护凭据注入正式角色
docker compose run --rm migrate python scripts/manage_postgres.py schema
# 仅旧PG全局Worker协议切换时执行，普通更新不重复准备
docker compose run --rm migrate python scripts/manage_postgres.py prepare-consumers
docker compose up -d --build app scheduler worker automation
```

若存在旧worker租约记录，新Consumer拒绝启动并提示prepare-consumers。正常应用只用无DDL权限cutting_app，不自动迁移、不双写、不回退。准备步骤会有意调整执行态，操作前保留同批PG及文件备份。

## Compose与离线扩缩容

.env中的CONSUMER_REPLICAS同时用于Compose部署数量和Web期望的最小健康Consumer数，默认1、至少1；不是每实例线程数。原生部署也使用同一参数声明期望容量。建议先验证1、2、3，更多副本依据CPU、内存、PG连接及外部系统容量决定，不能预设线性加速。

```bash
# 明确启动单Consumer；三个后台只依赖PG健康，不依赖Web或彼此先启动
docker compose up -d --scale worker=1 app scheduler worker automation
# 动态扩至两个、三个，仅变更Consumer
docker compose up -d --no-deps --scale worker=2 worker
docker compose up -d --no-deps --scale worker=3 worker
# 缩回一个，不清空队列、不重启Scheduler/Automation
docker compose up -d --no-deps --scale worker=1 worker
docker compose ps
docker compose logs --tail 100 worker
```

持久化副本数量可写CONSUMER_REPLICAS=2/3，再运行up；显式--scale仅覆盖该次实际副本数量，不改变Web加载的健康期望。若期望为3而只运行2个健康实例，后台状态为degraded；历史lost/stopped实例仍保留，但不扣减当前健康容量。永久调整期望时须重建Web及相应应用容器；默认期望1下临时扩缩容不需重启其他角色。宿主机.env冷配置变化须重建相应应用容器，资源容量修改须所有角色使用一致值；普通restart不更新环境。管理保存的产线、数据源及自动化/SMTP仍按各进程下一次操作热加载，不依赖共享Python内存。

离线仍使用同一镜像和compose.yaml+compose.offline.yaml。start_offline_docker.sh先验证依赖镜像，再检查PG既有Schema，然后按CONSUMER_REPLICAS启动四类角色；不自动执行维护迁移。各副本均挂载当前backend/vendor和同机data/reports/logs，共享只读绑定使用SELinux z标签（[Docker官方语义](https://docs.docker.com/reference/compose-file/services/#volumes)），不使用会隔离副本访问的私有Z标签；P3锁目录必须是同一data/.coordination。

```bash
./scripts/linux/start_offline_docker.sh
# 离线动态扩容；镜像已导入且与当前uv.lock匹配
docker compose -f compose.yaml -f compose.offline.yaml up -d --no-build --pull never --no-deps --scale worker=3 worker
```

## 原生麒麟服务

分别注册单Web、单Scheduler、单Automation及Consumer@实例ID系统服务，工作目录、data/reports/logs保持一致。配置只读生产数据源，状态库使用cutting_app。

```bash
./scripts/linux/start_kylin.sh web
./scripts/linux/start_kylin.sh scheduler
./scripts/linux/start_kylin.sh automation
CONSUMER_INSTANCE_ID=consumer-1 ./scripts/linux/start_kylin.sh worker
CONSUMER_INSTANCE_ID=consumer-2 ./scripts/linux/start_kylin.sh worker
CONSUMER_INSTANCE_ID=consumer-3 ./scripts/linux/start_kylin.sh worker
```

每个Consumer服务独立设置Environment=CONSUMER_INSTANCE_ID=consumer-1等。不要把实例ID保存在共享data/settings.env，否则它会覆盖进程环境并让多个实例竞争同一身份。原生探针使用与该服务相同的环境，例如CONSUMER_INSTANCE_ID=consumer-2 PYTHONPATH=backend .venv/bin/python -m app.worker --health。

## 所有权、恢复与缩容

三个后台角色均沿用30秒租约/5秒心跳，Consumer按实际worker:实例ID进入execution_scope。事务进入和提交用PG数据库时钟及租约共享锁校验；过期owner不能续约或提交。后台任务原子SKIP LOCKED领取，完成同时核对running、owner、claim_token及attempts代际；同产线同类普通任务部分唯一索引保留，不同产线可并行。领取量受本实例真实执行容量限制，没有预取大批任务。

Consumer启动及每5秒恢复只选择没有有效owner租约的运行任务。先锁候选行，对owner租约取不等待共享锁；续约/接管未提交的owner跳过，其余失效owner继续恢复，再以新语句快照复核并条件更新；并发领取/续约/恢复不会把正常实例任务重置。报表状态与文件元数据验证execution_owner；失败状态写入短暂失败时只重试该写入，不重跑报表或提前发失败回调，能耗保留claim_token与owner，初始化依赖既有唯一约束幂等。来源切换不改历史任务ID；告警/能耗等新任务都带来源指纹，缺失/不匹配被blocked，当前有效来源由正常调度/刷新提交新轮次；blocked不占活动容量，已确认来源的同键计划可重新入队，缺指纹旧键继续隔离。快照复用任务ID时，每次领取生成新token，旧轮次完成不能覆盖新轮次。

SIGTERM后停止领取，报表执行收到取消检查，现有5秒有界等待尽量完成操作；仅在全部执行线程停止并提交完成记录后释放租约。仍有在途线程时不提前释放，进程退出/TTL与事务围栏负责接管，不能把未完成任务标成功。被强杀的实例必须等真实TTL失效，其余实例随后精确恢复；生产禁止手工篡改租约以抢跑。

报表使用唯一report_id和owner隔离的内部HTML文件名及原子替换；旧实例不能覆盖新owner文件或发布其元数据。数据库回滚不撤销文件、SMTP或外部副作用。正常写入/替换失败会清理本owner临时文件；进程被强杀遗留的未引用文件/临时文件不视为已完成报告；停机维护时结合PG reports引用核对处理，不能按文件名猜测完成状态。

## 健康、诊断与记录保留

```bash
docker compose exec scheduler python -m app.scheduler --health
docker compose exec --index 1 worker python -m app.worker --health
docker compose exec --index 2 worker python -m app.worker --health
docker compose exec automation python -m app.automation_worker --health
```

Consumer探针只验证当前容器/原生实例的租约、报表线程、队列观测和停滞状态。Web健康保留原字段，增加workers.consumer_instances、consumer_running_count、consumer_lost_count和roles.worker汇总；正常/退化/不可用按期望容量及当前有效实例判断：健康数不足或有效实例停滞为degraded，无正常实例为unavailable。历史lost/stopped记录不扣减健康容量。PG故障返回503，不回退或把未持久化结果标成功。

公开健康递归移除配置比较指纹；配置诊断保留旧worker字段并按实例展示consumers快照、版本与差异，实例ID不是共享配置一致性项。资源API保留worker字段，在roles中增加worker:实例ID的本进程SQL观测，不能声称全局实时计数。

同实例重启复用process_leases并更新owner；Docker重建留下的历史失联记录保留，但不累积配置诊断卡片或降低健康容量。配置诊断展示当前运行/停滞实例，容量不足及当前异常仍提示。原retention只清理超过30天且无后台/报表/能耗运行任务引用的失效Consumer租约，不删除有效或仍用于恢复的owner。

## 容量与连接预算

CONSUMER_OPERATION_CONCURRENCY默认4、CONSUMER_ANALYSIS_CONCURRENCY默认10，各允许1–32，都是每实例本地容量；每实例另有1报表线程。增加副本不改变共享SQL全局12、每源/产线配额，LLM默认全局10、未提升优先级的后台上限5、前台优先，PDF仍是共享Chromium执行锁。不得为每个实例使用不同协调目录或不同共享容量配置。

每角色独立复用有界PG池，默认0–4。N个Consumer时应用池总上限4×(N+3)：1/2/3实例分别16/20/24；另给短健康探针及维护连接留余量。慢生产SQL、模型、SMTP/PDF不持有PG事务或行锁。外部MySQL池也按实例独立，现场需同时评估空闲池连接和源库连接预算。没有新增PgBouncer、队列、容量框架或跨主机锁。

池上限4/8/12、满执行槽与三个真实Consumer、长事务锁竞争、池耗尽和自然TTL崩溃恢复的实测见[并发与队列容量验证](PostgreSQL并发与队列容量验证.md)。快照活动上限64与后台pending阈值256保持不变；调整前先检查持续拒绝/积压、任务年龄、消费能力和外部资源等待，不能随产线数盲目扩容。

## 验证与回退

隔离验证范围、连接与队列观测见[容量验证](PostgreSQL并发与队列容量验证.md)。测试证明领取、租约、恢复和配额边界，不承诺生产线性加速或现场迁移成功。

回退必须先停止所有新Consumer及其余角色，确认退出并保留同批PG/配置/文件备份，处理未完成任务和未知副作用，再恢复匹配P6的PG备份及源码/镜像。003新增Schema版本使旧二进制拒绝启动；不能只换镜像或删除版本行来宣称已回退。需要保留升级后业务数据时须单独制定回迁流程，不擅自覆盖。更早SQLite版本也不能只换连接字符串恢复。
