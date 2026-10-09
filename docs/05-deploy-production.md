# BoyanKB 生产部署入口

目标地址为 `https://kb.boyantech.com`，初期采用邀请制和本地密码登录，不接入 OIDC。部署基于独立的 `docker-compose.prod.yml`，不叠加开发用 `docker-compose.yml`、local 或 branding overlay。生产文件已经使用定制 app/frontend 镜像，并挂载生产专用内置 Agent 配置。

本文是待在阿里云 ECS 执行的运行手册。ECS 地域、操作系统、CPU 架构、内存、磁盘、IP 和 DNS 尚未确认；没有连接云服务器、修改 DNS 或完成公网部署。当前镜像锁定的平台是 `linux/amd64`。不能把本机 Windows Docker Desktop 的通过结果当成目标 Linux 主机的验收。

## 已验证范围与上线门槛

2026-10-09，本机独立 staging 的 119 项基础设施、340 项权限/问答自动检查通过；7 个服务运行，其中 6 个设置了 Docker healthcheck 并通过，Caddy 通过真实 HTTPS 请求验证。`app-baseline` 的 6 项容器表面检查通过。加密备份已恢复到全新项目，74 张表、8919 行、2 个业务文件及 Redis/Caddy/环境文件一致，恢复后登录、文件下载和关键词/向量检索也通过。详细证据见 [production-auth.md](production-auth.md)、[production-backup.md](production-backup.md) 和 [05-acceptance.md](05-acceptance.md)。Caddy 使用本地 CA，这些结果不证明 `kb.boyantech.com` 的公网证书可签发或续期。

对外邀请用户前必须完成：

1. ECS 平台、资源、磁盘和安全组核实；目标机能解析所有锁定镜像及模型 manifest。
2. 新环境独立密钥、空数据库初始化、指定系统管理员登录及有效邀请制配置回读。
3. 每个工作空间脚本禁用、无命名沙箱后端；应用无 Docker socket、远端运行时证书或可达运行时。
4. 真实域名 HTTPS、注册阻断、权限/检索与合成问答验收。
5. 加密备份及新项目隔离恢复，包括登录、文件、检索与必要凭据解密。

沙箱没有上线。基线不支持 Agent 执行脚本、安装 Skill 或生成 Word/PPT；已有文档解析能力不等于生成能力。后续受控远端运行时和真实 Office 验收见 [production-sandbox.md](production-sandbox.md)。

## 文件与运行拓扑

| 文件 | 用途 |
| --- | --- |
| `docker-compose.prod.yml` | 独立生产服务、网络、卷、限制及固定镜像 |
| `docker-compose.staging.yml` | 本机演练，HTTP/HTTPS 仅绑定 `127.0.0.1:8089/8443`，使用 Caddy 本地 CA |
| `docker-compose.restore.yml` | 全新恢复项目，声明回环测试端口，所有网络 internal；Docker 未实际发布端口时使用下述临时隧道 |
| `deployment/production/images.lock.json` | 7 个镜像引用、平台、上游基线及 BGE-M3 manifest SHA-256 |
| `deployment/production/Caddyfile*` | TLS、注册与内部接口限制、反向代理 |
| `deployment/production/builtin_agents.yaml` | 仅保留基于当前空间知识的快速回答 Agent，关闭网页搜索并使用固定无依据回复 |
| `config/builtin_models.local.yaml` | `local-deepseek-chat` 远程模型和 `local-embedding` 本地 1024 维模型 |
| `scripts/init-production.py` | 创建不覆盖旧文件的独立环境文件并限制文件权限 |
| `scripts/bootstrap-production.py` | 仅空库的一次性迁移和管理员注册，随后恢复邀请制服务 |
| `scripts/check-production.py` | 实际容器、镜像、资源、环境、模型 manifest、TLS 和入口规则检查 |
| `scripts/acceptance-prod.py` | 默认只读配置检查；显式隔离模式才创建合成权限/问答样本 |

公网流量依次经过 Caddy → frontend Nginx → app。app 使用 PostgreSQL/ParadeDB 保存业务数据、向量及关键词索引，Redis 提供队列/流状态，DocReader 解析文档，Ollama 提供本地 embedding。只有 Caddy 发布宿主端口。

| 服务 | 容器内端口 | CPU 上限 | 内存上限 | PID 上限 |
| --- | --- | --- | --- | --- |
| caddy | 80、443 | 0.5 | 256 MiB | 128 |
| frontend | 80 | 0.5 | 256 MiB | 128 |
| app | 8080 | 2 | 2 GiB | 256 |
| docreader | 50051 | 1 | 1536 MiB | 192 |
| postgres | 5432 | 2 | 2 GiB | 256 |
| redis | 6379 | 0.5 | 256 MiB | 64 |
| ollama | 11434 | 2 | 3 GiB | 128 |

内存上限合计约 9.25 GiB，CPU 上限合计 8.5 核；这些是容器约束，不是宿主预留量、实例选型结论或并发性能保证。需额外计入操作系统、Docker、文件缓存、镜像、解析临时文件、模型与备份空间。当前没有目标 ECS 容量或负载测试，不能据此承诺 4 核/8 GiB可运行或任意并发量。调整限制后必须重新测试大文档解析、embedding、检索与备份窗口。

`web`、`backend` 网络设为 internal；app、Ollama另接出站网络，Caddy另接公网入口网络。应用的 SSRF 域名校验不等于主机出站防火墙；当前配置不能被描述成所有容器流量都按域名白名单隔离。允许的模型/飞书域名和实际出站策略需分别核验。

## ECS 与域名前置准备

部署负责人先记录 ECS 的地域、发行版与版本、`uname -m`、CPU/内存、磁盘可用量、Docker 数据目录、公网/私网 IP 和已有网段。当前镜像只验证 `linux/amd64`；若目标为 ARM64，需要重新构建/获取相应镜像并更新镜像锁，不能直接使用模拟运行作为生产验证。

使用发行版支持的 Docker Engine、Compose 插件、Python 3.11+、Git；备份还需要 age。本机实际使用 Python 3.13，目标机版本须运行附带测试验证。选择 Docker 官方对应发行版的安装方式，不把未知发行版套用到单一安装命令。Compose 至少需要支持 `!override` 的 2.24.4 版本；其合并行为见 [Docker Compose 合并规则](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)。Windows 运维命令必须运行于 PowerShell 7；Linux 的 `python` 命令可按实际安装替换为 `python3`。

```text
docker version
docker compose version
python --version
git --version
```

安全组及主机防火墙的公网入站仅开放 Caddy 的 TCP 80/443；SSH 22 只允许运维固定来源 IP 或堡垒机来源，禁止 `0.0.0.0/0`、`::/0` 的 SSH 规则。不开放 8080、8088、5432、6379、50051、11434、2375、2376。需要 IPv6 时同样核对 IPv6 规则；不提供 IPv6 服务时不要发布指向不可用地址的 AAAA 记录。阿里云建议管理端口限制可信来源，见[安全组使用说明](https://www.alibabacloud.com/help/zh/ecs/user-guide/start-using-security-groups)。Docker 实际发布端口仍需检查，不能只依赖主机防火墙界面。

确认 80/443 未被其他服务占用。DNS 变更为待执行动作：将 `kb.boyantech.com` 的 A 记录指向目标入口；只有确实完成 IPv6 连通测试才添加 AAAA。Caddy 公网证书需要正确 DNS、可达的挑战端口及持久可写的数据目录，见[自动 HTTPS 官方说明](https://caddyserver.com/docs/automatic-https)。本项目生产配置未接入 DNS challenge、SLB 或 CDN；如果加前置代理，需另行调整证书与可信代理链并验证。

`PROD_WEB_SUBNET` 默认 `10.239.40.0/24`，staging 为 `10.239.50.0/24`，restore 为 `10.239.60.0/24`。先排除它们与 ECS VPC、VPN、已有 Docker 网络冲突。生产初始化可传 `--web-subnet`；恢复覆盖文件的固定网段若冲突，需调整该独立覆盖文件及对应静态地址后一并验收。

## 准备固定版本与镜像

在目标机使用已审查的仓库版本，保留 `LICENSE`、品牌素材及 `deployment/production`。部署包必须包含本次新增脚本、Compose、锁文件和挂载配置；不能从尚未包含这些文件的旧提交导出。不要把工作目录连同真实 `.env`、`temp/`、业务文件或本机 Docker 凭据一起打包。

7 个镜像和 BGE-M3 manifest 以 `deployment/production/images.lock.json` 为准。生产不现场构建 app/frontend，不拉取 `latest` 来替代已锁定内容。BGE-M3 的名字虽然是 `bge-m3:latest`，仍须验证完整 manifest SHA-256；仅名称相同不够。

定制 `boyankb/app`、`boyankb/frontend` 目前只存在于本机构建环境，`pull_policy: never` 不代表 ECS 已有镜像。两种交付方式择一：

- 离线转运：源机器 `docker image save` 导出指定两个镜像，通过受控传输通道运至目标机并对照归档 SHA-256，然后 `docker image load`。这不包含模型卷或业务数据。Docker 官方分别描述 [save](https://docs.docker.com/reference/cli/docker/image/save/) 和 [load](https://docs.docker.com/reference/cli/docker/image/load/)；当前没有在干净 ECS 上验证 load 后同一 `repository@sha256` 引用仍可解析。必须在目标机实际验证锁定引用和平台，失败时停止，不能退回只按 tag 启动。
- 经批准推送私有 ACR：使用批准的地域、命名空间和仓库，发布已验收镜像，目标机使用只读拉取权限。推送后取得注册表 digest，将实际 ACR 完整引用同时写入生产 Compose 与锁文件，再走 staging 验收。不要直接复制本地 image ID 当作注册表 manifest digest。登录通过交互或安全凭据输入，不将密码放在命令参数。操作依据见 [ACR 企业版推拉镜像](https://www.alibabacloud.com/help/en/acr/getting-started/use-a-container-registry-enterprise-edition-instance-to-push-and-pull-images)。此次未推送 ACR。

离线命令使用新的归档路径，避免覆盖已有产物：

```text
docker image save --output /secure-transfer/boyankb-custom-images.tar boyankb/app:v0.8.2 boyankb/frontend:v0.8.2
docker image load --input /secure-transfer/boyankb-custom-images.tar
```

前一条仅在源构建机执行，后一条仅在目标机执行；路径按各自系统调整。归档 SHA-256 应通过独立可信渠道核对。目标机逐项验证锁文件中的镜像引用可解析、平台为 `linux/amd64`，并记录实际 image ID。源镜像存在或 load 输出成功都不能替代这一步。

## 创建独立生产环境文件

以下命令在仓库根目录执行。每个项目使用独立环境文件、项目名和卷，不复用本机开发 `.env`。

```text
python scripts/init-production.py --output temp/production/prod.env --domain kb.boyantech.com
```

脚本要求输出位于仓库内、已被 Git 忽略且不存在；拒绝覆盖现有 `.env`。它生成数据库/Redis/JWT/应用加密/签名/gRPC 秘钥和管理员密码；POSIX 设为 `0600`，Windows 限制文件 ACL。使用受控编辑器填写以下内容，不 `cat`、`source`、打印或提交该文件：

| 字段 | 要求 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 有效生产凭据，删除占位值；模型调用会产生费用 |
| `DEEPSEEK_MODEL_NAME`、`DEEPSEEK_BASE_URL` | 与实际可用账号及 `builtin_models.local.yaml` 兼容；不要把默认模型名当作已验证生产权限 |
| `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL` | 唯一初始系统管理员邮箱 |
| `ACCEPTANCE_ADMIN_EMAIL` | 必须与 bootstrap 邮箱完全相同 |
| `ACCEPTANCE_ADMIN_PASSWORD` | 脚本生成的强密码；初始化要求 12–32 字符 |
| `PUBLIC_HOST` | `kb.boyantech.com`，不带协议/路径 |
| `PROD_WEB_SUBNET` 和三个静态地址 | 与实际网络规划一致；优先在初始化时通过 `--web-subnet` 生成 |

保留生产 `ACCEPTANCE_ISOLATED_ORIGIN` 为空，不把公网 origin 声明为隔离测试环境。`--import-model-env` 只用于确实需要复用模型字段的独立演练；生产建议填独立凭据。环境文件使用脚本的单行单引号格式，避免重复键、换行或需要 shell 展开的值。

`SYSTEM_AES_KEY` 用于解密已保存的集成/模型凭据，不能在有数据后随意重生成；JWT 和签名秘钥也应随部署受控备份。环境文件本身不是备份方案，见[加密备份与恢复](production-backup.md)。

```text
docker compose --env-file temp/production/prod.env -p boyankb-prod -f docker-compose.prod.yml config --quiet
docker compose --env-file temp/production/prod.env -p boyankb-prod -f docker-compose.prod.yml pull caddy postgres redis docreader ollama
```

不要直接输出完整 `docker compose config`、容器 inspect 或 app 环境，它们包含展开后的凭据。`config --quiet` 只做配置有效性检查。公共镜像拉取与定制镜像转运均须完成后再初始化数据库。

## 空库初始化与 embedding 准备

只适用于全新数据库。先启动依赖并下载 embedding；下载阶段需要 Ollama 能访问镜像/模型分发服务，不能用应用的域名白名单推断下载已被允许。

```text
docker compose --env-file temp/production/prod.env -p boyankb-prod -f docker-compose.prod.yml up -d --wait --wait-timeout 180 postgres redis docreader ollama
docker compose --env-file temp/production/prod.env -p boyankb-prod -f docker-compose.prod.yml exec -T ollama ollama pull bge-m3:latest
docker compose --env-file temp/production/prod.env -p boyankb-prod -f docker-compose.prod.yml exec -T ollama sha256sum /root/.ollama/models/manifests/registry.ollama.ai/library/bge-m3/latest
```

将最后的 SHA-256 与锁文件 `embedding.sha256` 精确比较，且确认该模型为 1024 维。若远端 tag 已更新，停止部署；使用已验证模型缓存的受控导入或重新评审新的模型版本，不能静默更新锁。后续 `check-production.py` 会再次强制检查该 manifest。

```text
python scripts/bootstrap-production.py --project boyankb-prod --env-file temp/production/prod.env --compose docker-compose.prod.yml --initialize-empty-database
```

脚本会拒绝已有 `users` 表或仍在运行的 app/frontend/Caddy。它在无发布端口的一次性 app 容器内启用迁移和注册，仅创建指定管理员及其初始空间；验证迁移非 dirty 后停止临时容器，再启动常态邀请制 app/frontend/Caddy。管理员在常态 app 启动时按 bootstrap 邮箱提权。初始化不是幂等重跑命令：失败后保留现场，先核对数据库与临时容器状态，不删除卷重试，也不对既有库重新开启公开注册。

当前 PostgreSQL 使用同一个 `POSTGRES_USER` 作为初始化与 app 连接账号，初始化角色是 superuser；这是已知限制，不能称为最小数据库权限部署。后续应拆分迁移/扩展管理账号与运行时账号，明确表、序列和扩展权限后验证升级、检索及恢复；本次没有实施该拆分。

常态 `AUTO_MIGRATE=false`、`AUTO_RECOVER_DIRTY=false`。以后升级不能复用空库 bootstrap，需在停写备份后执行单独验证过的迁移流程。

## 首次管理配置与邀请发放

管理员先通过真实 HTTPS 登录，确认系统管理员身份，以及原始 `can_access_all_tenants=false`。不要为绕过建空间限制永久授予跨空间标志。

环境变量不是全部有效配置的最终来源。通过系统管理界面或受控认证 API 核对以下系统设置；需要修改时对 `/api/v1/system/admin/settings/{key}` 发送 `PUT {"value":...}`，随后 `GET` 回读顶层 `value`，不能只看 `.env`：

```json
{
  "auth.registration_mode": "invite_only",
  "auth.default_tenant_mode": "tenantless",
  "sandbox.docker_enabled": false,
  "tenant.self_service_creation_enabled": false,
  "tenant.auto_accept_invitation": false
}
```

对 bootstrap 初始空间及之后每个新空间，使用该空间 Owner 权限执行 `PUT /api/v1/sandbox-configs/workspace-policy`，请求 `{"scripts_disabled":true}`，再确认 `GET /api/v1/sandbox-configs` 的顶层 `workspace_scripts_disabled=true` 且 `data=[]`。此步骤必须先于邀请发放；它不是关闭 Docker env 后自动得到的结果。

新增伙伴空间时，当前需要受控维护窗口：临时开启自助创建、由管理员创建空间、无论创建成败立即关闭并回读，然后禁用新空间脚本，再邀请伙伴。该全局开关不只作用于管理员，所以应限制窗口内用户访问。具体接口、角色与退出失败的处理见[生产认证与空间隔离](production-auth.md)。目前没有独立生产 provision CLI；不能把隔离 fixture 创建命令直接用于公网生产。

优先按最小必要角色邀请，不发公开 Owner 链接。共享邀请 URL 是可重复使用的持有者凭据，并非绑定邮箱的一次性链接；发给指定伙伴并在完成后撤销。已有账号的邮箱邀请保留显式接受流程。当前不配置 OIDC；后续启用需重新验证准入与账号生命周期，邀请制不保证自动约束首次 OIDC 开户。

## 生产只读检查

选择不存在的报告路径。不要使用跳过证书校验参数；真实域名证书应由系统信任链验证。

```text
python scripts/check-production.py --project boyankb-prod --env-file temp/production/prod.env --compose docker-compose.prod.yml --base-url https://kb.boyantech.com --http-url http://kb.boyantech.com --output temp/production/check-001.json
python scripts/acceptance-prod.py --base-url https://kb.boyantech.com --env-file temp/production/prod.env --output temp/production/auth-readonly-001.json
```

第一条检查 7 个实际服务与镜像锁、资源限制、实际 app 环境、非 root app 主进程、模型 manifest、TLS/HTTP 跳转、favicon、许可证和入口拦截。Caddy 没有容器 healthcheck，所以必须同时满足 HTTPS 请求成功。它不检查阿里云安全组、公网扫描、实例容量或恢复能力。

第二条默认不创建业务对象，使用管理员凭据登录时仍会生成令牌并可能写审计记录；检查 API 有效认证配置与 bootstrap 空间策略。它不代替所有伙伴空间的逐一检查，也不等于跨空间样本已完成。完整隔离样本应在下节的独立演练项目执行。

另外用已建立的目标 Docker context 检查 app 的基础设施表面：

```text
python scripts/check-sandbox-isolation.py --context production-app app-baseline --container ACTUAL_APP_CONTAINER_ID
```

通过 Compose `ps -q app` 获取真实容器 ID；context 必须明确指向目标机器。这个命令只检查挂载、namespace 和运行时环境，不证明数据库策略或远端网络隔离。生产 Caddy 还阻断脚本/沙箱相关入口和命名后端写接口；应保留该规则，不对公网暴露内部 app 端口绕过它。

## 在独立 staging 重复演练

本节仅用于本机/隔离测试环境，不用于向公网生产灌入 fixture。每轮用新的项目名、环境文件和报告前缀；同一主机不要并发复用 8089/8443 或同一静态网段。

```text
python scripts/init-production.py --output temp/production-acceptance/staging-new.env --staging
```

先在受保护的环境文件填写 staging 独立模型凭据；镜像与模型仍按前文核对。完成后执行：

```text
docker compose --env-file temp/production-acceptance/staging-new.env -p boyankb-stage-new -f docker-compose.prod.yml -f docker-compose.staging.yml config --quiet
docker compose --env-file temp/production-acceptance/staging-new.env -p boyankb-stage-new -f docker-compose.prod.yml -f docker-compose.staging.yml up -d --wait --wait-timeout 180 postgres redis docreader ollama
docker compose --env-file temp/production-acceptance/staging-new.env -p boyankb-stage-new -f docker-compose.prod.yml -f docker-compose.staging.yml exec -T ollama ollama pull bge-m3:latest
python scripts/bootstrap-production.py --project boyankb-stage-new --env-file temp/production-acceptance/staging-new.env --compose docker-compose.prod.yml --compose docker-compose.staging.yml --initialize-empty-database
docker compose --env-file temp/production-acceptance/staging-new.env -p boyankb-stage-new -f docker-compose.prod.yml -f docker-compose.staging.yml cp caddy:/data/caddy/pki/authorities/local/root.crt temp/production-acceptance/staging-new-ca.pem
```

这里只复制本地 CA 的公开证书，不复制私钥，不把 staging CA 安装成生产公网证书。

```text
python scripts/check-production.py --project boyankb-stage-new --env-file temp/production-acceptance/staging-new.env --compose docker-compose.prod.yml --compose docker-compose.staging.yml --base-url https://localhost:8443 --http-url http://localhost:8089 --ca-file temp/production-acceptance/staging-new-ca.pem --output temp/production-acceptance/check-new.json
python scripts/acceptance-prod.py --base-url https://localhost:8443 --env-file temp/production-acceptance/staging-new.env --ca-file temp/production-acceptance/staging-new-ca.pem --isolated --create-fixtures --random-identities --allow-llm --fixture-prefix prodaccept-new --output temp/production-acceptance/accept-new.json
```

`--allow-llm` 会向配置的模型发送合成测试内容并产生调用费用；不使用真实飞书知识库。该验收包含 A/B 空间、角色、限定 KB 的检索 Key、邀请以及有限的问答样本，不是普遍抗提示注入或全部权限场景的证明。报告即使自动通过，`production_readiness` 仍列明需要人工验证的项目。部分失败后的测试实体保留，不自动删除；重试按[认证验收文档](production-auth.md)使用新的样本前缀，避免反复创建耗尽配额。

## 持久化、备份与恢复

持久卷包括 `postgres-data`、`redis-data`、`data-files`、`ollama-data`、`caddy-data` 和 `caddy-config`；`docreader-tmp` 是解析中间结果。当前对象存储是本地 `data-files`，没有外部 S3/OSS/COS/MinIO。生产文件、数据库和索引必须一起备份，Caddy ACME 状态/私钥及应用环境也必须受加密保护。

按[生产备份与隔离恢复手册](production-backup.md)执行真实演练。推荐停写窗口 `--quiesce`，它暂时停止入口、前端和 app，完成或可捕获失败后重新启动；备份不是无中断动作。`--online` 不保证跨数据库/对象/Redis的同一时点一致性，不能据此跳过停写恢复验收。

恢复只能到全新 `boyankb-restore-...` 项目及不存在的目标目录，显式叠加 `docker-compose.restore.yml`。恢复脚本只启动数据库和 Redis；Ollama 模型缓存不在备份包内，需单独准备已锁定模型后再启动其他服务。恢复网络全部 internal，不能为了下载模型临时把含恢复数据的 app 放回出站网络；应通过已验证模型缓存的受控导入准备独立模型卷。

恢复成功必须包含管理员登录、原对象 ID/源文件、分块及检索、模型/飞书凭据解密和仅本地端口可达；仅 `pg_restore` 成功不等于恢复验收完成。演练结果未完成前不得宣称已有可恢复备份。备份私钥必须在源 ECS 和备份文件之外另行安全保管。

本机 Docker 对只连接 internal 网络的容器未真正发布声明的端口。恢复验收使用 `scripts/restore-tunnel.py` 在 `127.0.0.1:9089/9443` 临时监听，通过 Docker exec 的字节流仅转发到指定恢复 Caddy 的 `80/443`。它不解密 TLS、不记录凭据、不改变任何容器网络，最长运行一小时，默认十分钟；退出后端口关闭。不要通过给恢复 app 增加普通出站网络来解决入口问题。准确启动命令、模型缓存导入及已完成结果见备份手册。

此次合成恢复没有真实飞书集成密文；真实集成密文解密仍需人工验证，不能由 `.env` 字节一致直接推导。恢复后真实外部服务调用、生产规模 RTO/RPO 和异地私钥取回同样尚未验证。

## 日常运行与变更

每次操作显式传入生产环境文件、`-p boyankb-prod` 和 `-f docker-compose.prod.yml`。不要使用开发 smoke 脚本刷新生产 app，不运行裸 `docker compose up`，避免选到默认开发 Compose。普通查看使用 Compose `ps` 和本手册检查脚本；日志按需限量查看并脱敏，不导出原始环境、访问令牌、邀请链接或文档正文。

修改环境文件后单纯 `restart` 不会让容器重读新环境；需要在确定变更范围及维护窗口后重新创建对应服务，再执行有效配置和健康检查。升级先备份、在 staging 验证目标镜像与迁移、更新 Compose 和锁文件，再执行已验证的生产变更。回滚 app 镜像不自动回滚数据库 schema，恢复路径必须使用配套数据库和配置。

持续监控磁盘/卷使用率、OOM 和重启、队列积压、模型超时、证书续期、备份结果及恢复耗时。Redis 当前为 192 MiB、`noeviction`，达到上限可能使写入失败；容器健康通过并不能证明队列可持续承载峰值。当前没有完成 ECS 性能容量、告警通道或故障恢复时间目标测试。

不执行 `down -v`、卷删除、删库或强制覆盖已有恢复目录作为常规重试。这些是数据破坏操作，需单独确认。DNS 切换、云安全组修改、镜像发布和真正云上线仍是待执行事项；本文没有替代这些动作的验收结果。
