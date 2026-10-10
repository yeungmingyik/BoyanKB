# 本机试用备份与隔离恢复

本流程仅适用于 `weknora-local` 的七个服务：app、frontend、docreader、postgres、redis、ollama、document-renderer。生产备份脚本及生产 Compose 不变。停写备份和创建恢复资源必须由操作者明确执行；脚本的离线测试不能替代真实恢复演练，公网开放前应核对本次备份、恢复及应用验收记录。

`backup-trial.py` 要求 `--quiesce`，拒绝其他源项目和远程 Docker endpoint。脚本先验证 age 收件人与私钥配对、运行服务及卷归属，再停止 frontend、app、docreader、renderer 和 Ollama。数据库与 Redis 留在运行状态供导出。无论导出成功与否，脚本都会尝试启动原容器并等待健康；恢复启动失败时不产生成功 receipt。中断或失败的 `.age` 文件不能当作可用备份，脚本拒绝覆盖已有输出。

备份使用 age 对每个组件及外层归档分别加密，完整重解密校验后才输出 receipt。包含 PostgreSQL custom dump、本地对象文件、Redis RDB、原 `.env`、实际 Compose 和绑定配置、运行镜像内 `skills/boyan`、`presets/boyan/v1`、renderer 的 `branding/templates`、Ollama 模型缓存。临时处理只落盘加密组件。镜像本体、Ollama 身份密钥、docreader 临时目录和外部存储不在备份范围；本流程拒绝外部对象或向量存储及 Docker 沙箱配置。

恢复要求全新 `boyankb-restore-trial-*` 项目和全新目录，拒绝复用同名容器、卷、网络。七个原镜像的 image ID 必须仍存在于本机，禁止隐式拉取镜像。镜像内指南和模板须与归档逐文件一致。脚本生成七服务的隔离配置，但只启动新的 PostgreSQL 和 Redis；其余服务设为 `application-not-started` profile。所有恢复服务仅接入新建的 internal 网络，无宿主端口、源卷、Docker socket 或可写宿主绑定。

`restore-report.json` 的 `verified` 表示：数据库导入成功且迁移及全表行数一致，对象文件和模型缓存逐文件摘要一致，Redis RDB 校验与启动通过，原环境字节及镜像内业务资源一致。它不表示应用启动、记忆或数据源密文解密、文件下载、检索、LLM 或 Office 已通过。恢复 Redis 使用 RDB 模式，避免空 AOF 遮蔽恢复数据；后续正式切换需要另行安排应用验收和持久化策略。

恢复目录包含解密后的 `.env` 和完整配置，其中可能有凭据。脚本对目录设置当前用户访问权限；不要上传该目录、打印配置或运行 `docker compose config` 把内容写入日志。恢复配置将 `$` 转义为 Compose 字面量，以保持原环境值和容器命令。对外只使用 `.age`、脱敏 receipt 和 report；age 私钥应另行保管。

## 执行顺序

先完成镜像部署及本机策略配置，再生成清理计划和停写备份。receipt 绑定七个源容器 ID；后续重新创建任一容器都会使管理员交接门禁失效，必须重新备份。保持应用无其他操作者写入，停止外部同步/上传操作后再执行停写命令。

以下为 PowerShell 7 命令，路径及输出名称需选择新的值。`$trialIdentity` 指向已妥善保存的 age 私钥；不要把私钥内容放在命令参数里。

```powershell
$trialAge = (Resolve-Path 'temp/production-tools/age-v1.3.2/age/age.exe').Path
$trialKeygen = (Resolve-Path 'temp/production-tools/age-v1.3.2/age/age-keygen.exe').Path
$trialIdentity = (Resolve-Path 'temp/enterprise-trial/keys/backup-identity.txt').Path
$trialRecipient = & $trialKeygen -y $trialIdentity
$trialCompose = (& docker inspect WeKnora-app --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}').Split(',')
$trialBackupArgs = @('scripts/backup-trial.py', '--project', 'weknora-local', '--env-file', '.env', '--quiesce', '--age-binary', $trialAge, '--identity', $trialIdentity, '--recipient', $trialRecipient, '--output', 'temp/enterprise-trial/trial-snapshot.age', '--receipt', 'temp/enterprise-trial/backup-receipt.json')
foreach ($trialFile in $trialCompose) { $trialBackupArgs += @('--compose', $trialFile) }
python @trialBackupArgs
```

备份输出目录必须已存在，`.age` 与 receipt 必须不存在。私钥缺失、源服务不完整、挂载布局不符、加密或恢复原服务失败都会非零退出。

```powershell
python scripts/restore-trial.py --archive temp/enterprise-trial/trial-snapshot.age --identity $trialIdentity --age-binary $trialAge --project boyankb-restore-trial-rehearsal01 --target-dir temp/enterprise-trial/rehearsal01
python scripts/verify-trial.py --archive temp/enterprise-trial/trial-snapshot.age --identity $trialIdentity --age-binary $trialAge --receipt temp/enterprise-trial/backup-receipt.json --restore-report temp/enterprise-trial/rehearsal01/restore-report.json
```

最后一条只读校验命令重新解密归档，核对 receipt/report 的归档摘要、大小、源容器 ID、数据库清单摘要、七服务集合及 24 小时时效，不启动容器或访问业务 API。成功输出 `event=trial_evidence_verified` 及脱敏摘要。report 本身不是签名凭证，清理工具还必须核对操作者明确提供的两份 JSON 文件 SHA，并重新核对当前源容器 ID；不得仅根据手写的 `verified` 字段执行清理。

恢复失败时，新项目和恢复目录会保留用于检查，不自动删除或覆盖。确认处理方案后使用新的恢复项目和目录重试。停写失败时应先核对原七服务状态，不能直接根据归档文件是否存在判断成功。

## 日志与验证

`frontend/nginx-http.conf` 的访问日志不记录 query、Referer 或 User-Agent，能力短链 `/r/*` 固定记录为 `/r/[redacted]`；`frontend/nginx.conf` 将普通页面及 API 路由统一使用该格式。app 请求日志也对 `/r/*` 的路径做同样处理，保留状态码及其他诊断字段。真实路由、代理转发、SSE 和 WebSocket 行为不变。

Nginx 无法为原生 error log 自定义字段脱敏，因此当前 server 使用 `error_log /dev/null crit`，不保留带完整请求上下文的运行期错误日志。容器启动错误和健康检查仍可用于发现故障，访问日志保留 method、脱敏路径、status、bytes、remote address；上游故障的细节排查能力相应减少。本修复不表示浏览器历史、其他网关或所有业务错误日志均已完成全局脱敏。

离线测试不读取真实 `.env`，使用合成数据和临时 age 私钥：

```powershell
python scripts/test_trial_backup.py --age-binary temp/production-tools/age-v1.3.2/age/age.exe
python scripts/test_frontend_access_logs.py
```

代码测试结果：15 项试用备份测试、8 项 Nginx 配置和探针测试通过。包括真实 age 往返、密文篡改拒绝、危险 tar 路径拒绝、来源/时效门禁、停写后导出失败仍恢复服务的模拟测试。真实恢复结果以对应 receipt/report 和部署后验收记录为准。

新前端镜像上线后运行以下只读探针。它向注册页、公开认证配置、品牌 SVG 以及无效能力短链发送随机非敏感标记，并只在进程内读取相关容器日志；成功要求前三个路由为 200、无效短链为 404，日志短链路径为固定脱敏值，且 query、Referer、User-Agent 和路径里的标记均未出现在日志中。输出仅状态与布尔结果，不打印原日志。

```powershell
python scripts/check-frontend-access-logs.py --base-url http://127.0.0.1:8088 --container WeKnora-frontend
```

追加 `--check-upstream-failure` 会使用正在运行的同一前端 image ID 启动一个临时测试容器，设置 `network=none`、无宿主端口及挂载、上游指向自身关闭的 9 端口。脚本检查两次真实 502 的 query、短链、Referer、User-Agent 标记均不出现在日志，最后停止自己创建的测试容器，不停止原 app。Nginx 内部错误页跳转可能将日志路径替换为固定 `/50x.html`，探针接受该安全路径，同时仍要求真实 502 和所有标记均未泄漏。该选项会创建临时容器，区别于默认的只读路由探针。2026-10-10 本机部署后，四路由探针和两次上游故障模拟均通过。

脚本和文档复用的生产原语位于 `scripts/backup-prod.py`、`scripts/restore-prod.py`；新增流程在 `scripts/trial_backup.py`、`scripts/backup-trial.py`、`scripts/restore-trial.py`、`scripts/verify-trial.py`，生产文件未修改。

## 九服务栈与图谱兼容边界

2026-10-10 的本机覆盖新增试用网关与 Neo4j，当前栈为九服务。本文上方的七服务备份/恢复命令仅适用于其指定历史 profile，**不能用于当前九服务栈**。`trial_backup.py` 会继续拒绝额外服务及 `NEO4J_ENABLE=true`，不应移除检查来获取成功回执，也不要套用历史回执为当前部署背书。

新增 `scripts/graph-trial-backup.py` 只提供局部操作，与原七服务回执采用不同格式：

| 子命令 | 校验范围 | 未覆盖范围 |
| --- | --- | --- |
| `snapshot` | 在线 PG custom dump、根 `.env`、指定 KB 配置；age 完整解密、摘要、`pg_restore --list` | 非全栈原子快照，不含 Neo4j、原文对象、Redis、模型、镜像或数据库集群角色；没有实际恢复数据库 |
| `cold-backup` | 外部维护流程已停止 app 和干净停止 Neo4j后，完整加密 `/data`，包含系统库与事务目录 | 不包含其他服务、环境文件、插件缓存和镜像；脚本不自动停机或恢复服务 |
| `restore` | 新隔离卷、相同本地 image ID、完整文件清单摘要及 `neo4j-admin database check` | 不启动 DBMS/应用，不证明登录、文件、检索或跨存储一致性 |

真实在线快照、冷备及新卷离线恢复结果记录在 [图谱局部备份说明](graph-partial-backup.md)。2026-10-10 已完成 Neo4j 冷备和全新 c 项目恢复，文件清单及 `neo4j`、`system` 两库离线一致性检查全部通过；未启动恢复 DBMS 或应用。原管理员清理门禁所需的七服务整栈证据，不能被这些局部回执替代。当前九服务整栈备份及应用恢复兼容方案仍需单独实现和验收。

维护窗口应先确认图谱任务结束并暂停新的写入，再由操作者按当前完整 Compose 覆盖链停止 app、干净停止 Neo4j。冷备成功后，可以先恢复原 Neo4j 并等待健康，再恢复 app；随后在原服务继续运行时验证独立恢复卷。每个输出与目标项目必须唯一，失败保留证据，不覆盖或删除原卷。

当前 age identity 的实际路径为 `temp/enterprise-trial/keys/.env`；它是原始 age 私钥文件，与根目录应用 `.env` 用途不同，只将路径交给 age，不输出内容。以下为本轮成功执行的命令记录；再次操作时须换用未占用的输出和目标名称：

```powershell
python scripts/graph-trial-backup.py cold-backup --project weknora-local --maintenance-stopped --age-binary temp/production-tools/age-v1.3.2/age/age.exe --identity temp/enterprise-trial/keys/.env --output temp/graph-backup/neo4j-cold-20261010a.age --receipt temp/graph-backup/neo4j-cold-20261010a.json
python scripts/graph-trial-backup.py restore --archive temp/graph-backup/neo4j-cold-20261010a.age --identity temp/enterprise-trial/keys/.env --age-binary temp/production-tools/age-v1.3.2/age/age.exe --target-project boyankb-restore-graph-20261010c --report temp/graph-backup/neo4j-restore-20261010c.json
```

恢复辅助容器为 `network none`，不发布端口、不启动应用；仅写新项目的专用数据卷，保留该卷供后续审核。挂载禁用镜像目录预填，只有可信离线检查容器的临时目录允许原生库加载，根目录及资源限制保持不变。a、b 失败目标与 c 成功目标均保留，原卷未被修改。14 项图谱工具及 15 项旧备份测试通过。详细前提、失败处理和报告边界见 [图谱局部备份说明](graph-partial-backup.md)。
