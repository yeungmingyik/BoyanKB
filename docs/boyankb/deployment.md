# 部署与运维

## 环境

| 项目       | 要求                                            |
| ---------- | ----------------------------------------------- |
| 操作系统   | Windows PC，Docker Desktop 使用 WSL2 Linux 容器 |
| 命令环境   | PowerShell 7+、Git、Docker Compose V2           |
| 构建运行时 | 镜像内 Node.js `24.16.0`、npm `11.13.0`         |
| 数据库     | MongoDB `8.0.20`，仅容器网络可达                |
| 访问地址   | `http://localhost:3080`                         |
| 模型连接   | 用户 API Key；启动与账号管理无需模型服务        |

所有命令在仓库根目录执行。默认 Compose 项目为 `boyankb-librechat`，命名卷独立于其他项目。应用仅监听宿主 `127.0.0.1`。

## 初始化与启动

```powershell
pwsh -NoProfile -File scripts/boyankb/init-local.ps1
pwsh -NoProfile -File scripts/boyankb/start-local.ps1
```

`start-local.ps1` 自动初始化配置、构建当前源码镜像、启动服务并等待健康检查。镜像标签为 `boyankb:<产品版本>-<提交号前12位>`。Node.js 和 MongoDB 基础镜像固定版本及 digest。

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

| 文件                                      | 内容                           |
| ----------------------------------------- | ------------------------------ |
| `.local/boyankb-librechat/.env`           | 本机状态路径与端口             |
| `.local/boyankb-librechat/app.env`        | 应用凭据、账号策略、供应商配置 |
| `.local/boyankb-librechat/mongo.env`      | MongoDB root 与应用用户凭据    |
| `.local/boyankb-librechat/librechat.yaml` | 知识入口、界面与模型端点       |
| `.local/boyankb-librechat/image-tag`      | 实例使用的应用镜像标签         |

初始化分别生成 MongoDB root 密码、应用密码、JWT 密钥、刷新密钥、加密密钥与监控密钥。应用数据库用户仅具有 `BoyanKB` 数据库的 `readWrite` 权限。私有配置限制为当前操作系统用户与 SYSTEM 访问，并由 Git 忽略。

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

## 状态与停止

```powershell
docker compose --project-name boyankb-librechat --env-file .local/boyankb-librechat/.env --file deploy/boyankb/compose.yaml ps
docker compose --project-name boyankb-librechat --env-file .local/boyankb-librechat/.env --file deploy/boyankb/compose.yaml logs --tail 100 app
pwsh -NoProfile -File scripts/boyankb/stop-local.ps1
```

应用健康检查包含 `/readyz`、MongoDB ping 和数据、日志、上传卷的写入检查。数据库健康检查使用认证后的 ping。启动失败返回非零退出码；既有容器与数据卷保留。

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
| `app-data`                       | 应用持久数据                               |
| `app-uploads`、`app-images`      | 上传与图片                                 |
| `app-logs`                       | 应用日志                                   |

停止脚本保留全部命名卷。备份需同时保存数据库、应用数据卷和独立加密的本机配置；丢失 `CREDS_KEY`、`CREDS_IV` 将无法解密已有用户密钥。

更新前停止写入并完成备份，再执行 `start-local.ps1` 构建和启动。回滚必须使用与数据库结构及加密密钥匹配的镜像、数据和配置。

## 部署边界

当前 Compose 包含应用与 MongoDB。飞书 Worker、资料同步、RAG、向量库、自动备份恢复和云端迁移按后续版本交付。

伙伴远程访问需配置 VPN 或 HTTPS 入口，并重新设置域名、代理与安全 Cookie。localhost 部署仅提供本机访问。云端部署沿用相同源码与配置契约，数据库与秘密材料独立迁移。
