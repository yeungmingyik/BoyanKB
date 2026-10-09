# 生产备份与隔离恢复

适用范围为本仓库 `docker-compose.prod.yml`：PostgreSQL 同时承担主库和检索库，业务对象保存在 `data-files`，Redis 使用 `redis-data`。备份工具为 Python 3 标准库、Docker Compose 和 [age](https://github.com/FiloSottile/age)。本次工具版本为 age v1.3.2。

`scripts/backup-prod.py` 输出一个 `.age` 文件。数据库、对象文件、Redis RDB、完整环境文件、Compose 文件、各服务挂载的单文件配置，以及存在时的 `caddy-data`、`caddy-config` 分别流式加密，再与校验清单整体加密。ACME 账户及证书私钥随 Caddy 卷进入加密包。临时目录只保存密文；不会生成明文数据库包、对象包或中间 `.env`。

清单记录各组件密文与明文的长度、SHA-256、来源镜像 ID、数据库迁移版本和 `public` schema 各表行数、Ollama 模型名称及完整 digest。数据库导出包含完整数据库，行数核验覆盖应用使用的 `public` schema。输出仅包含状态、项目名、数量、备份文件路径和备份文件校验值，不输出凭据、数据库内容或文档内容。

## 支持边界

| 内容 | 处理方式 |
| --- | --- |
| PostgreSQL 数据、向量及关键词检索数据 | `pg_dump` 自定义格式，恢复使用事务与遇错退出 |
| 本地业务文件 | 完整 `data-files` 卷；恢复校验每个文件内容、权限及 UID/GID |
| Redis | RDB 快照；恢复到空卷，校验 RDB 后生成 AOF，再启动 Redis |
| `.env` | 原始字节完整加密保存，包含 `SYSTEM_AES_KEY`；恢复为最终目标目录的 `.env` |
| Caddy 两个状态卷 | 源存在时必须同时备份并恢复，包含证书和 ACME 状态 |
| 挂载配置 | 各服务单文件 bind mount 与指定 Compose 文件进入加密包 |
| Ollama 模型缓存 | 不包含；记录 digest，另行下载或复制到恢复项目自己的模型卷 |
| DocReader 临时文件、容器镜像 | 不包含，分别为临时结果和部署产物 |
| MinIO、COS、S3 等对象后端 | 当前脚本拒绝；不能把只备份数据库称为完整备份 |
| 外部向量库、Neo4j、沙箱工作目录 | 当前脚本拒绝 |

MinIO 的后续支持必须把对象数据与数据库放在同一停写窗口：单节点专用 MinIO 可在停止应用和 MinIO 后备份完整数据卷，包括 `.minio.sys`；远程或多节点 MinIO 应使用其一致快照/复制能力，保留版本、删除标记及必要桶配置，再纳入同一加密清单。当前版本没有执行这些操作，因此不能用于 MinIO 恢复验收。

## 密钥与运行方式

age 备份只需要公钥。恢复私钥应放在仓库外的受控位置并另行保管，不能只存于同一备份磁盘；丢失私钥无法解密。不要把私钥或业务凭据写进命令参数、文档或日志。官方用法支持标准输入输出流和 identity 文件：[age 使用说明](https://github.com/FiloSottile/age#usage)。

以下是 PowerShell 7 命令，路径按实际机器调整；生产 Linux 同样使用 Python 脚本和 age，无需 Bash wrapper。

```powershell
& 'D:/tools/age/age-keygen.exe' -o 'D:/secure/boyankb-backup-key.txt'
$backupRecipient = & 'D:/tools/age/age-keygen.exe' -y 'D:/secure/boyankb-backup-key.txt'
python scripts/backup-prod.py --project boyankb-prod --compose docker-compose.prod.yml --env-file 'D:/secure/boyankb-prod.env' --recipient $backupRecipient --age-binary 'D:/tools/age/age.exe' --output 'D:/backups/boyankb-20261009.age' --quiesce
```

输出文件必须不存在，脚本不会覆盖已有备份。退出码 `0` 表示备份完成且原运行服务已恢复；`1` 表示失败；中断为 `130`。命令失败后的不完整密文文件不能作为成功备份使用，重试应选择新文件名。

`--quiesce` 明确授权一个维护窗口：关闭 Caddy、frontend、app，停止 Web 请求及应用内后台任务，数据库和 Redis 保持运行。完成或捕获到错误后会在 `finally` 中重新启动这些源容器，并等待原健康检查通过。维护期间也必须暂停外部直连数据库写入和直接改动对象卷的操作。脚本拒绝对 `weknora-local` 主开发项目执行停写备份。

机器断电、Docker daemon 失效或强制杀进程无法由 `finally` 恢复。维护窗口结束时仍需确认源容器运行和健康状态；恢复失败会返回非零，不会报告成功。

`--online` 不停止服务，但数据库快照与文件、Redis、证书状态之间不具备跨存储原子性。清单会固定标为 `online-cross-store-not-atomic`，恢复必须另外指定 `--accept-online-snapshot`。它适合明确接受该边界的临时副本，不能代替停写备份的验收结果。

环境文件支持普通赋值及单引号值，保留原始文件字节。为避免实际运行值与存档值不一致，重复键、多行值、需要插值的未加引号值及转义双引号值会被拒绝；重要运行凭据还会与源容器内有效环境比较。归档不能通过 `DOCKER_HOST`、`DOCKER_CONTEXT`、`PATH` 等客户端控制变量改变检查和恢复使用的 Docker daemon 或可执行程序。脚本不会打印不匹配的值。

## 隔离恢复

恢复目标项目只能使用 `boyankb-restore-...` 前缀，不能包含独立的 `prod`、`production`、`local`、`staging` 名称段，且不能等于源项目。目标目录、容器、网络、卷必须全新；脚本拒绝现有目标，不清空、不删除卷，不执行 `DROP DATABASE`。

先创建恢复目录的父目录；不要预先创建最终目标目录，也不要提前运行目标 Compose。

```powershell
New-Item -ItemType Directory -Force -Path temp/recovery
python scripts/restore-prod.py --archive 'D:/backups/boyankb-20261009.age' --identity 'D:/secure/boyankb-backup-key.txt' --age-binary 'D:/tools/age/age.exe' --project boyankb-restore-20261009a --compose docker-compose.prod.yml --compose docker-compose.restore.yml --target-dir temp/recovery/20261009a --allow-config-override caddy:/etc/caddy/Caddyfile
```

恢复分为完整校验、配置隔离检查、新卷导入三个阶段。先完成外层 age 认证、清单比对、全部组件认证和明文 SHA 校验，检查对象归档的相对路径、文件类型和权限。路径穿越、符号链接、设备文件、目录 setuid 及普通文件特殊权限会被拒绝；目录 sticky/setgid 位按原值保留，兼容 Caddy 状态目录。只有之后才创建最终环境文件和目标资源。

目标 Compose 必须满足以下约束：所有网络均为新的 `internal` 网络；发布端口只绑定 `127.0.0.1`；无固定 `container_name`、host network、特权模式或外部卷；bind mount 只允许已有的只读单文件配置，拒绝 `configs`、`secrets` 和 `use_api_socket` 隐式挂载；数据库、文件和 Redis 使用约定命名卷。数据库、Redis、应用、前端及存在时 Caddy 的镜像 ID 必须与备份一致，禁止自动迁移。恢复不能隐式升级数据库或应用。

最终 `.env` 是恢复运行所需的真实配置，位于指定恢复目录，内容与备份完全相同。POSIX 使用目录 `0700`、文件 `0600`；Windows 在写入之前使用 `icacls` 移除新目录的继承权限，仅授予当前用户可继承的访问权限，失败则停止。仓库的 `temp/` 已忽略，仍不得提交其中内容。

脚本只启动新 PostgreSQL 和 Redis，并生成 `restore-report.json`，记录数据库行数/迁移版本、文件数量及校验结果。app、frontend、Caddy、DocReader、Ollama 不会自动启动；这样恢复出的定时任务、飞书数据源和其他集成不会立即连接外部系统。

PostgreSQL 的等待、查询与导入固定连接 `127.0.0.1` TCP，避免镜像初始化期间临时 Unix socket 服务提前被认为就绪。新库仅允许镜像预建的 PostGIS `spatial_ref_sys` 表，并核验它确属 `postgis` 扩展；其他业务表立即拒绝。对同镜像预建的 `paradedb`、`tiger`、`topology`，先检查初始 schema、扩展及表/函数归属，再从恢复目录清单中精确跳过这 3 条已存在的 schema 创建声明。COMMENT、扩展、表、数据和索引保持原样，导入仍使用 `--single-transaction --exit-on-error`，不执行清库或忽略错误。清单只含数据库对象元信息，临时保存在目标容器内，完成或失败后删除该临时文件。

加密包中的配置会导出到最终恢复目录的 `config/`，`config/index.json` 对应原文件及服务挂载目标，不覆盖仓库。当前恢复使用命令中明确指定的 Compose 文件；其中只读绑定文件必须与备份的 SHA-256 和长度一致。仅对逐项指定的 `--allow-config-override 服务:/容器路径` 允许内容差异，并把实际差异记入恢复报告。上例仅允许将生产 Caddy 配置替换为演练的内部 TLS 配置；staging 来源本来使用同一文件时可省略该参数。

## 恢复验收

先在保持 `internal` 网络的项目中准备独立 Ollama 模型缓存，核对模型 digest。随后通过指定的恢复 Compose 启动其余服务；每次都显式传入恢复项目名、恢复 `.env`、`docker-compose.prod.yml` 和 `docker-compose.restore.yml`，不要调用仓库默认的本地 Compose。恢复报告的 `compose_invocation` 提供对应参数及仅含路径/项目名的进程环境覆盖，不包含凭据。

```powershell
$env:PRODUCTION_ENV_FILE = (Resolve-Path temp/recovery/20261009a/.env).Path
docker compose --project-name boyankb-restore-20261009a --env-file $env:PRODUCTION_ENV_FILE --file docker-compose.prod.yml --file docker-compose.restore.yml up -d --no-build
```

必须验证：七个服务健康、管理员可登录且角色正确、原工作空间/知识库/文档 ID 存在、源文件可读取、文档片段和索引可检索、模型配置凭据可由恢复的 `SYSTEM_AES_KEY` 解密，以及 Caddy/前端只在恢复端口可达。检索验收可使用本地 embedding 模型，无需付费问答；未实际执行的问答不能标记为通过。

本机实测只接 internal 网络的 Caddy 虽声明发布端口，`NetworkSettings.Ports` 仍为空，宿主直连被拒绝。Docker 官方项目也记录了这一行为：[Moby internal 网络端口发布讨论](https://github.com/moby/moby/discussions/53256)。不为此给恢复应用开放出站；使用 [restore-tunnel.py](../scripts/restore-tunnel.py) 在宿主回环提供临时入口。它检查恢复项目的唯一运行 Caddy、锁定镜像及 internal 网络，通过 `docker exec -i` 和容器内 `nc` 原样转发字节到 `127.0.0.1:80/443`。TLS 始终由恢复 Caddy 终止，客户端仍验证恢复 CA。该脚本不解释 HTTP/TLS 内容、不保存流量，仅绑定宿主 `127.0.0.1:9089/9443`，最大 16 个连接，单连接空闲超时 180 秒；默认运行 600 秒，到期或 Ctrl+C 关闭入口和活动连接。

当前已完成的目标为 `e`。以下模型复制步骤仅对新建且尚未启动的空 Ollama 卷执行；已有模型的目标会拒绝覆盖，已完成的 `e` 不要重复复制：

```powershell
docker compose --env-file temp/recovery/20261009e/.env -p boyankb-restore-20261009e -f docker-compose.prod.yml -f docker-compose.restore.yml create --no-build ollama
$restorePgImage = (Get-Content deployment/production/images.lock.json -Raw | ConvertFrom-Json).images.postgres
docker run --rm --network none --read-only --mount type=volume,src=boyankb-staging-20261009_ollama-data,dst=/source,readonly --mount type=volume,src=boyankb-restore-20261009e_ollama-data,dst=/target --entrypoint /bin/sh $restorePgImage -ec 'test -z "$(ls -A /target)"; test -f /source/models/manifests/registry.ollama.ai/library/bge-m3/latest; cp -a /source/models /target/models; sha256sum /target/models/manifests/registry.ollama.ai/library/bge-m3/latest'
```

SHA-256 必须等于 `images.lock.json` 的 embedding 值。后续启动和验收：

```powershell
docker compose --env-file temp/recovery/20261009e/.env -p boyankb-restore-20261009e -f docker-compose.prod.yml -f docker-compose.restore.yml up -d --no-build --wait --wait-timeout 180
docker cp boyankb-restore-20261009e-caddy-1:/data/caddy/pki/authorities/local/root.crt temp/recovery/20261009e/ca.crt
python scripts/restore-tunnel.py --project boyankb-restore-20261009e --seconds 600
```

隧道命令保持前台运行；在另一 PowerShell 7 窗口执行以下命令，报告文件必须是新路径：

```powershell
python scripts/check-production.py --project boyankb-restore-20261009e --env-file temp/recovery/20261009e/.env --compose docker-compose.prod.yml --compose docker-compose.restore.yml --base-url https://localhost:9443 --ca-file temp/recovery/20261009e/ca.crt --http-url http://localhost:9089 --output temp/production-acceptance/restored-infra-new.json
python scripts/check-restored.py --project boyankb-restore-20261009e --env-file temp/recovery/20261009e/.env --fixtures temp/production-acceptance/run7.fixtures.json --restore-report temp/recovery/20261009e/restore-report.json --ca-file temp/recovery/20261009e/ca.crt --output temp/production-acceptance/restored-app-new.json
```

`check-restored.py` 只适用于本套合成 fixture，不能拿不存在的 run7 文件验证任意生产备份；真正生产恢复需用其备份时间点的受控样本 ID 和文件哈希做同等检查。新恢复轮次替换项目/目录，并先确认其他恢复项目没有占用静态网段与回环端口。

失败后的隔离项目会原样保留以供检查，不自动销毁资源，也不允许向同一项目重复导入。修复后选择新的项目名和目标目录重做；清理既有容器/卷另行确认。

## 验证记录

2026-10-09，20 项安全回归已通过：双层加密往返、错误私钥、密文损坏、清单校验错误、缺少组件、对象路径穿越/符号链接拒绝、文件权限/UID/GID 保留、现有目标与生产项目拒绝、Compose 隔离约束、单引号环境值、Docker 客户端环境覆盖拒绝、配置差异的精确授权、停写后备份失败仍重新启动源服务、TCP 就绪判断、扩展空库边界，以及 schema 清单过滤不遗漏业务数据和索引。

```powershell
python scripts/test_prod_backup.py --age-binary temp/production-tools/age-v1.3.2/age/age.exe -v
```

2026-10-09 已完成隔离 staging 的真实停写备份与数据库/文件恢复，两个命令均退出 `0`，没有访问或停止 `weknora-local`。

| 项目 | 实测结果 |
| --- | --- |
| 源项目 | `boyankb-staging-20261009`，使用 run7 的已解析合成上传文档 |
| 一致性 | `application-quiesced`；备份后原 app、frontend、Caddy 自动恢复并通过健康检查 |
| 加密归档 | `temp/production-backups/staging-20261009.age`，450856 字节，14 个加密组件 |
| 归档 SHA-256 | `578afc06071afcd37551960b397c80a9634dcdaac818baff5d15a87a0d986359` |
| 成功目标 | `boyankb-restore-20261009e`，`temp/recovery/20261009e` |
| 数据库 | 74 张 `public` 表、8919 行，与快照逐表一致；迁移 `110`，`dirty=false` |
| 业务对象 | 2 个文件，内容 SHA-256、路径、权限及 UID/GID 全部一致 |
| Redis | RDB SHA-256、`redis-check-rdb`、AOF 重建及认证 PING 通过 |
| Caddy | `caddy-data` 与 `caddy-config` 的文件清单和内容校验通过 |
| 环境文件 | 恢复文件与备份原始字节一致；写入前已收紧目录 ACL，未输出凭据 |
| 基础服务 | 恢复后 7 服务运行，6 个 Docker healthcheck 通过，Caddy 由真实 TLS 请求验证 |
| 应用与检索 | `temp/production-acceptance/restored-application.json` 32 项 PASS：管理员登录、原 KB/文档/片段、真实文件下载、A/B 关键词与向量检索 |
| 恢复基础设施 | `temp/production-acceptance/restored-infrastructure.json` 119 项 PASS，包含 TLS、品牌、入口和资源配置 |
| 模型缓存与 TLS | 模型从 staging 只读复制至恢复独立卷，manifest SHA 与锁定值一致；恢复 CA 与备份来源公开 CA 相同，全程校验证书 |
| 临时隧道 | `temp/recovery/20261009e/tunnel-verification.json` 14 项 PASS：端口冲突及时失败、真实 TLS、到期关闭连接/端口并回收全部派生 Docker CLI；测试后入口已关闭 |

此次兼容性排查保留了失败目标 `boyankb-restore-20261009a` 至 `d`，仅停止其 PostgreSQL 容器，未删除卷。早期 Unix socket 就绪检查会命中镜像初始化临时服务；`c` 已修复该竞态，但被镜像原生 `spatial_ref_sys` 表触发空库拒绝；`d` 通过空库检查后遇到原生 schema 重复创建，单事务导入失败。修复均通过新目标 `e` 验证，没有向失败目标重复导入。

备份完成后源上继续运行的 run8 验收资料不属于此快照；本次恢复应用验收使用 run7 记录。模型缓存已按上述步骤单独准备，恢复项目没有调用外部 LLM 或飞书；不能把本地检索成功称为真实飞书集成密文解密或外部问答通过。此类真实凭据验证、异地取回与生产规模 RTO/RPO 均需要人工验证。底层 `restore-report.json` 在启动 app 前写入，故其 `application_started=false` 与后续应用验证报告并不矛盾。
