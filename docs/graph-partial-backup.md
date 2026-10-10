# 本机图谱变更前快照与冷备

`scripts/graph-trial-backup.py` 只支持本机 Docker 中的 `weknora-local`。它不会停止或恢复现有服务，不读取生产环境，不覆盖已有输出，也不会删除恢复卷。加密使用既有 age 工具；身份文件只交给 age 进程，数据库连接凭据只在容器中使用。

当前带网关和 Neo4j 的本机栈有九项服务。`scripts/trial_backup.py` 仍只接受原七服务及不启用 Neo4j 的运行状态；本次保留其拒绝规则。以下两种归档都不能作为账号清理、整站恢复或全系统一致性备份的证明。

| 操作 | 包含内容 | 明确不包含或不证明 |
| --- | --- | --- |
| `snapshot` | PostgreSQL 当前应用数据库的 custom dump、根 `.env`、指定知识库配置与文档/分块数量 | Neo4j、对象文件、Redis、模型、镜像、数据库集群角色；配置与数据库不是同一个原子快照 |
| `cold-backup` | 干净停止后的 Neo4j `/data` 全部普通文件，包括 `neo4j`、`system` 与事务目录 | PG、原文对象、环境文件、日志、插件缓存、运行配置、镜像；不证明跨存储一致性 |
| `restore` | 仅接受上述图谱冷备，恢复到新卷，核对每个文件摘要/权限/属主，并离线检查 `neo4j` 与 `system` | 不启动 DBMS 或应用，不执行真实检索，不提供生产回切 |

## 在线数据库与配置快照

从仓库根目录运行。示例输出名每次必须更换；已存在即失败。`keys/.env` 是历史保存的原始 age identity 文件，不是应用环境变量文件，不要输出或复制其内容到命令行。

```powershell
New-Item -ItemType Directory -Force temp/graph-backup | Out-Null
python scripts/graph-trial-backup.py snapshot --project weknora-local --age-binary temp/production-tools/age-v1.3.2/age/age.exe --identity temp/enterprise-trial/keys/.env --env-file .env --knowledge-base-id d9285ed1-0760-474b-ba82-a13347598176 --output temp/graph-backup/before-graph-UNIQUE.age --receipt temp/graph-backup/before-graph-UNIQUE.json
```

脚本核对 Docker 本机上下文、源容器项目标签、实际数据库连接与 `.env` 一致后执行 `pg_dump --format=custom --no-owner --no-acl`。知识库配置在 dump 前后比较，变化时不出具成功回执。数据库本身使用 PG 的一致性读取；其与前后读取的配置、对象存储及图谱仍不构成跨服务原子快照。

完成后重新解密整个归档，检查组件 SHA-256 和 custom 格式，再在原 PG 容器中仅执行 `pg_restore --list`，不连接目标数据库、不恢复数据。回执的 `database_restore_verified` 固定为 `false`。失败时可能留下加密归档；没有成功回执的文件不可视作已经验证。

2026-10-10 实际完成 `temp/graph-backup/before-graph-20261010T064824Z-a472ac27.age` 及同名 JSON 回执，包含总部知识库 `d9285ed1-0760-474b-ba82-a13347598176` 的配置。已通过解密认证、三个组件摘要及真实 `pg_restore --list`；没有停服务或恢复原库。

## Neo4j 冷备维护窗口

先安排维护窗口，停止应用写入，再干净停止 Neo4j。服务暂停与恢复由操作者使用当前完整 Compose 覆盖链执行；本脚本不替换或猜测覆盖链。确认应用与 Neo4j 都已停止后运行：

```powershell
python scripts/graph-trial-backup.py cold-backup --project weknora-local --maintenance-stopped --age-binary temp/production-tools/age-v1.3.2/age/age.exe --identity temp/enterprise-trial/keys/.env --output temp/graph-backup/neo4j-cold-UNIQUE.age --receipt temp/graph-backup/neo4j-cold-UNIQUE.json
```

脚本拒绝仍运行、重启中、Neo4j 非零退出或 OOM 状态；检查 `/data` 的卷项目标签、无运行中的消费者，并在归档后重新核对源容器 ID 与停止时间。它不会自行恢复服务，回执 `writers_resumed` 为 `false`。完成后由操作者先恢复 Neo4j，等待健康，再恢复应用。

从干净停止的数据目录制作的文件级副本限定使用相同镜像 ID 恢复，不是跨版本迁移格式。Neo4j 官方另提供离线 `dump/load`；运行中的数据库不能用本流程拷贝替代在线备份。[Neo4j 离线备份说明](https://neo4j.com/docs/operations-manual/current/backup-restore/offline-backup/)、[Docker 离线 dump/load](https://neo4j.com/docs/operations-manual/current/docker/dump-load/)。

## 全新隔离卷验证

需要本机仍保留归档记录的确切 Neo4j 镜像 ID。脚本不会自动拉取镜像。目标项目必须为新的 `boyankb-restore-graph-*`，不能含 `prod`、`production`、`local` 或 `staging` 段。

```powershell
python scripts/graph-trial-backup.py restore --archive temp/graph-backup/neo4j-cold-UNIQUE.age --identity temp/enterprise-trial/keys/.env --age-binary temp/production-tools/age-v1.3.2/age/age.exe --target-project boyankb-restore-graph-UNIQUE --report temp/graph-backup/neo4j-restore-UNIQUE.json
```

目标后缀须使用小写字母、数字或连字符，替换示例中的 `UNIQUE`。恢复命令只新建目标数据卷，辅助容器全部 `network none`、无发布端口、只读根文件系统，并限制 CPU、内存和进程数。先还原文件，再验证完整文件清单，最后执行两个数据库的 `neo4j-admin database check`。新卷保留，供后续授权检查；失败时也不自动删除卷。若辅助 Docker CLI 超时或被中断，先检查目标项目标签下是否仍有隔离检查容器，不要立即重用该目标名。

新卷挂载使用 `volume-nocopy`，避免镜像目录预填使空卷检查失败。辅助容器固定主机名 `localhost`，使无网络状态下 Java 可以解析本机；只有可信 `neo4j-admin` 检查容器的 `/tmp` 允许加载原生库，提取及清单容器仍使用 `noexec`。两类容器均没有主机目录挂载，保持 `network none`、只读根目录、2 GiB 内存、1 CPU、128 个进程和禁止提权。

Neo4j 离线检查要求数据库没有被使用；退出码非零就不签发恢复成功报告。[Neo4j 一致性检查说明](https://neo4j.com/docs/operations-manual/current/backup-restore/consistency-checker/)。

2026-10-10 已在授权维护窗口完成 `temp/graph-backup/neo4j-cold-20261010a.age` 冷备及同名 JSON 回执；归档大小 541,582,616 字节，SHA-256 为 `d022920357f6603deaf4fa0e5460431085125acd96433a0f824ae281b2105def`。原服务恢复后，在全新项目 `boyankb-restore-graph-20261010c` 完成解密认证、文件摘要/权限/属主匹配及 `neo4j`、`system` 两库离线一致性检查，命令退出码为 0。正式报告为 `temp/graph-backup/neo4j-restore-20261010c.json`。

第一次目标 a 因镜像目录预填失败，第二次目标 b 在文件核验后因隔离容器的主机名及原生库加载条件失败；修复后以全新目标 c 完整重跑。a、b、c 卷全部保留，未覆盖或删除原数据。报告明确 `dbms_started=false`、`application_restore_verified=false`、`full_system_backup=false`：隔离实例启动、应用检索和整站恢复仍为**未验证**，不能由本次离线检查替代。

## 开发验证

```powershell
python scripts/test_graph_trial_backup.py --age-binary temp/production-tools/age-v1.3.2/age/age.exe
python scripts/test_trial_backup.py --age-binary temp/production-tools/age-v1.3.2/age/age.exe
```

14 项图谱备份测试与 15 项原七服务备份回归全部通过。覆盖真实 age 加解密与篡改拒绝、路径越界拒绝、禁止覆盖、维护前置状态、项目和目标卷边界、归档范围声明、无镜像目录预填及仅离线检查容器允许原生库加载。测试使用临时合成数据，不启动或停止业务容器。
