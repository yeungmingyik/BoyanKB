# 部署与运维

## 环境

| 项目       | 要求                                                     |
| ---------- | -------------------------------------------------------- |
| 操作系统   | Windows PC，Docker Desktop 使用 WSL2 Linux 容器          |
| 命令环境   | PowerShell 7+、Git、Docker Compose V2                    |
| 构建运行时 | 镜像内 Node.js `24.16.0`、npm `11.13.0`                  |
| 数据库     | MongoDB `8.0.20`，单节点副本集 `boyankb`，仅容器网络可达 |
| 知识索引   | 固定 digest 的 RAG API 与 pgvector，启用同步时启动       |
| 向量模型   | 本地 `BAAI/bge-small-zh-v1.5`，512 维，首次下载需网络    |
| 访问地址   | `http://localhost:3080`                                  |
| 模型连接   | 用户 API Key；启动与账号管理无需模型服务                 |

所有命令在仓库根目录执行。默认 Compose 项目为 `boyankb-librechat`，命名卷独立于其他项目。应用仅监听宿主 `127.0.0.1`。

## 初始化与启动

```powershell
pwsh -NoProfile -File scripts/boyankb/init-local.ps1
pwsh -NoProfile -File scripts/boyankb/start-local.ps1
```

`start-local.ps1` 自动初始化配置、构建当前源码镜像、启动服务并等待健康检查。镜像标签为 `boyankb:<产品版本>-<提交号前12位>`。Node.js 和 MongoDB 基础镜像固定版本及 digest。MongoDB 初始化服务建立 `boyankb` 副本集并等待可写主节点后才启动应用；同步发布事务不支持独立 MongoDB 模式。

仅启动已构建镜像：

```powershell
pwsh -NoProfile -File scripts/boyankb/start-local.ps1 -NoBuild
```

独立实例：

```powershell
pwsh -NoProfile -File scripts/boyankb/start-local.ps1 -Name boyankb-librechat-test -Port 3081
```

实例名称以 `boyankb-librechat` 开头。`-Port` 仅首次初始化生效；后续初始化保留既有配置与凭据。

## 本机配置

| 文件                                      | 内容                                     |
| ----------------------------------------- | ---------------------------------------- |
| `.local/boyankb-librechat/.env`           | 本机状态路径与端口                       |
| `.local/boyankb-librechat/app.env`        | 应用凭据、账号策略、供应商配置           |
| `.local/boyankb-librechat/mongo.env`      | MongoDB root 与应用用户凭据              |
| `.local/boyankb-librechat/librechat.yaml` | 知识入口、界面与模型端点                 |
| `.local/boyankb-librechat/feishu.env`     | 飞书应用凭据与源空间配置                 |
| `.local/boyankb-librechat/rag.env`        | 启用同步时生成的向量库凭据与本地模型配置 |
| `.local/boyankb-librechat/image-tag`      | 实例使用的应用镜像标签                   |

初始化分别生成 MongoDB root 密码、应用密码、副本集内部密钥、JWT 密钥、刷新密钥、加密密钥与监控密钥。应用数据库用户仅具有 `BoyanKB` 数据库的 `readWrite` 权限。私有配置限制为当前操作系统用户与 SYSTEM 访问，并由 Git 忽略。

配置样例位于 `deploy/boyankb/app.env.example` 和 `deploy/boyankb/librechat.yaml`。修改本机配置后执行 `start-local.ps1 -NoBuild` 应用环境变量；修改 YAML 后需重启应用容器。

修改端口时同步更新 `.env` 的 `BOYANKB_HTTP_PORT` 和 `app.env` 的 `DOMAIN_CLIENT`、`DOMAIN_SERVER`。MongoDB 已初始化后，修改环境文件中的密码不会修改数据库用户密码。

## 管理员与伙伴账号

创建首个本地账号：

```powershell
pwsh -NoProfile -File scripts/boyankb/manage-users.ps1 -Action create-user
```

首个账号自动获得 ADMIN，后续账号为 USER。命令交互输入邮箱、名称、用户名和密码；邮箱验证选择 `Y`。密码不放入命令行参数或 Git。

登录管理员账号，在原生 Agent 界面创建企业知识 Agent，然后设置其 Agent ID：

```powershell
pwsh -NoProfile -File scripts/boyankb/configure-agent.ps1 -AgentId agent_example
```

该命令更新本机 `BOYANKB_KNOWLEDGE_AGENT_ID` 并重新创建应用容器。使用 `-NoRestart` 只保存配置。Agent ID 为 `agent_...` 标识，不是 MongoDB ObjectId。

通过原生分享界面逐账号授予知识 Agent 的 VIEW 权限。USER 新账号默认没有知识访问权限。移除 VIEW 后，知识请求拒绝访问并清除该用户刷新会话；重新授予 VIEW 恢复访问。

原生账号命令：

```powershell
pwsh -NoProfile -File scripts/boyankb/manage-users.ps1 -Action list-users
pwsh -NoProfile -File scripts/boyankb/manage-users.ps1 -Action reset-password
pwsh -NoProfile -File scripts/boyankb/manage-users.ps1 -Action ban-user
```

封禁交互输入正数分钟。部署固定 `BAN_VIOLATIONS=true`、`BAN_INTERVAL=20`，默认违规封禁期限为两小时。永久撤销知识权限使用移除 VIEW。

配置 SMTP 后使用原生邀请：

```powershell
pwsh -NoProfile -File scripts/boyankb/manage-users.ps1 -Action invite-user
```

公开注册与社交注册关闭，有效且邮箱匹配的邀请可完成注册。邀请注册不自动获得知识权限。`invite-user` 会发送邮件。

## 模型与密钥

| 端点                      | 密钥与地址                               | 模型列表                            |
| ------------------------- | ---------------------------------------- | ----------------------------------- |
| OpenAI、Anthropic、Google | 用户通过原生设置保存 API Key             | 原生模型列表                        |
| DeepSeek                  | 用户 API Key，`https://api.deepseek.com` | `deepseek-flash`、`deepseek-v4-pro` |
| OpenAI Compatible         | 用户 API Key 与 API 地址                 | 使用该用户凭据获取 `/models`        |
| Anthropic Compatible      | 用户 API Key 与 API 地址                 | 用户在原生密钥对话框填写模型 ID     |

兼容端点的默认模型在本机 YAML 的 `models.default` 设置。用户可在原生密钥对话框填写个人模型 ID，每行一个；个人模型目录不对其他用户共享。默认选项不保证服务商支持。用户提供的地址经过上游 URL 检查。用户密钥和个人模型配置加密存储于 MongoDB，凭据 API 不返回明文。

公司提供模型服务时，为伙伴分别发放专属网关凭据，通过相应兼容端点保存。不把一个公司公共密钥配置为全体账号默认可用的服务端密钥。

## 飞书同步与本地索引

先创建并绑定由 ADMIN 持有的知识 Agent，再按照 [飞书应用开通](feishu-setup.md) 填写私有 `feishu.env`。`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_WIKI_URL`、`FEISHU_SPACE_ID` 均须填写。

```powershell
pwsh -NoProfile -File scripts/boyankb/start-sync.ps1 -NoBuild
```

该命令启用本机 YAML 的 `knowledge.sync`，生成独立向量库密码与 `rag.env`，保留首次修改前的 `librechat.yaml.before-sync`，再启动 Worker、RAG 和 pgvector。之后使用 `start-local.ps1` 或 `-NoBuild` 会同时启动已启用的同步服务。新版本源码尚未构建时省略 `-NoBuild`。

默认从 Hugging Face 下载锁定模型；需要优先使用 ModelScope 时执行：

```powershell
pwsh -NoProfile -File scripts/boyankb/start-sync.ps1 -ModelDownloadSource modelscope -NoBuild
```

模型固定为 `BAAI/bge-small-zh-v1.5`，Hugging Face 修订 `7999e1d3359715c523056ef9478215996d62a620`，输出 512 维。下载源均须通过 `embedding.lock.json` 的逐文件 SHA-256 校验。初始化完成后 RAG 以只读方式挂载模型卷，并设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`；正文向量计算在本机完成。模型文件完整时再次启动不重复下载。

RAG 与 pgvector 镜像 digest 固定于 `deploy/boyankb/compose.sync.yaml`。默认分块长度为 400、重叠为 60；索引版本为 `bge-small-zh-v1.5-7999e1d-v1`。更换模型或维度须使用新索引版本并重新发布资料。RAG 与向量库不开放宿主端口，用户选择问答供应商不会改变共享知识索引。

默认每 10 分钟轮询、每 24 小时完整对账。管理员在“资料库”的同步区域执行完整或增量扫描、查看失败项并重试；只有通过内容和原生索引核验的版本才提供阅读。源授权失效会暂停访问；恢复连接后仍需逐篇重新验证。

## 状态与停止

```powershell
docker compose --project-name boyankb-librechat --env-file .local/boyankb-librechat/.env --file deploy/boyankb/compose.yaml ps
docker compose --project-name boyankb-librechat --env-file .local/boyankb-librechat/.env --file deploy/boyankb/compose.yaml logs --tail 100 app
pwsh -NoProfile -File scripts/boyankb/stop-local.ps1
```

已启用同步时，查看完整服务状态与 Worker 日志：

```powershell
docker compose --project-name boyankb-librechat --env-file .local/boyankb-librechat/.env --file deploy/boyankb/compose.yaml --file deploy/boyankb/compose.sync.yaml ps
docker compose --project-name boyankb-librechat --env-file .local/boyankb-librechat/.env --file deploy/boyankb/compose.yaml --file deploy/boyankb/compose.sync.yaml logs --tail 100 worker
```

应用健康检查包含 `/readyz`、MongoDB ping 和数据、日志、上传卷的写入检查。数据库健康检查使用认证后的 ping。启动失败返回非零退出码；既有容器与数据卷保留。

Worker 健康检查使用进程心跳，RAG 与向量库分别检查服务和连接；健康状态不代表所有资料已同步。资料覆盖与发布结果以同步任务为准。

容器配置 `restart: unless-stopped`。手动停止后通过启动脚本恢复。Docker Desktop 未运行时服务不可用。

## 隔离验收

隔离集成验证：

```powershell
pwsh -NoProfile -File scripts/boyankb/test-local-access.ps1
```

验证使用 `boyankb-librechat-test` 实例、端口 `3081` 和 `@boyankb-acceptance.invalid` 合成账号，拒绝含其他邮箱账号的数据库。私有测试账号和结果保存至 `.local/boyankb-librechat-test`。聊天链路通过容器内 OpenAI 协议测试服务验证，不调用真实模型或发送邮件。

## 数据与备份

| 命名卷后缀                       | 内容                                       |
| -------------------------------- | ------------------------------------------ |
| `mongodb-data`、`mongodb-config` | 用户、会话、授权、加密密钥数据与数据库配置 |
| `app-data`                       | 应用持久数据、知识正文与素材快照           |
| `app-uploads`、`app-images`      | 上传与图片                                 |
| `app-logs`                       | 应用日志                                   |
| `vector-data`                    | 启用同步后的 PostgreSQL 与向量索引         |
| `embedding-models`               | 已校验的本地向量模型                       |

停止脚本保留全部命名卷。备份需同时保存数据库、应用数据、向量索引和独立加密的本机配置；保留模型卷可避免恢复时重新下载。丢失 `CREDS_KEY`、`CREDS_IV` 将无法解密已有用户密钥。副本集内部密钥和 RAG JWT 配置随同对应实例配置保留。

从 alpha.2 更新前停止写入并完成备份，再执行 `start-local.ps1` 构建和启动。初始化会为既有配置补充副本集内部密钥，保留原有数据库卷及账号；副本集转换须在隔离恢复环境验证。回滚必须使用与数据库结构及加密密钥匹配的镜像、数据和配置，不能仅切回旧应用镜像。

快照默认保留策略为 30 天，任务与审计为 90 天；自动物理清理未提供。已下线内容保留在卷中时仍受授权与版本限制，不对伙伴提供读取。

## 部署边界

基础部署包含应用与 MongoDB 副本集；启用同步后增加 Worker、模型初始化、RAG 与向量库。alpha.3 预发布功能已交付，合成场景集成通过，首次实际同步发现 18 份资料、发布 12 份。最终镜像的 HTTP、Worker、正式部署阅读与索引核对通过，运行范围见[验收清单](acceptance.md#8-alpha3-验证记录)。其余 6 份的未发布原因见[资料覆盖清单](source-coverage.md)。关键词与语义搜索、企业知识问答、自动备份恢复、自动物理清理和云端迁移尚未交付。

伙伴远程访问需配置 VPN 或 HTTPS 入口，并重新设置域名、代理与安全 Cookie。localhost 部署仅提供本机访问。云端部署沿用相同源码与配置契约，数据库与秘密材料独立迁移。
