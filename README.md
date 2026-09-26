# BoyanKB · 博言知识库

面向科创教育企业员工和授权合作伙伴的知识服务平台。资料统一在飞书维护，BoyanKB 提供浏览器访问、资料阅读、检索和带来源的知识问答。

## 业务领域

| 领域 | 知识内容 |
|---|---|
| 中小学科创教育 | 机器人、无人机、编程、AI 课程与实施方案 |
| 成人 AI 培训 | 培训课程、应用案例、教学与交付材料 |
| 中小学 AI 营地 | 企业参访、AI 学习、作品制作与活动执行 |
| 中小学 AI 黑客松 | 活动方案、项目指导、作品与版权申请资料 |
| 运营与合作 | 合作方案、师资培训、交付标准、供应链与常见问题 |

## 产品版本

`0.1.0-alpha.2`：本地网页部署、原生账号授权、个人会话与多供应商入口。

| 能力 | 状态 |
|---|---|
| LibreChat 源码与固定基线 | 已纳入仓库 |
| PRD、技术架构、验收与版本管理 | 已建立 |
| 原生账号、逐用户授权、封禁与撤权 | 已实现，验收记录见下文 |
| 本地 Docker 部署与持久化 | 已实现 |
| 多供应商选择与个人 API Key | 已集成；真实调用待配置密钥 |
| 飞书同步、系统内原文阅读 | 待开发 |
| 企业知识检索与问答 | 待开发 |
| 伙伴访问与备份恢复 | 待验收 |

本版本用于本地开发与授权验证。飞书同步、原文阅读及知识问答尚未交付。开发计划见 [路线图](docs/boyankb/roadmap.md)，验证范围见 [验收清单](docs/boyankb/acceptance.md)，变更见 [更新日志](CHANGELOG.md)。

## 使用方式

管理员通过 LibreChat 原生命令创建账号，或配置邮件服务后发送邀请，再通过原生分享界面授予企业知识 Agent 的查看权限。关闭公开注册，授权用户通过浏览器登录；员工和伙伴可阅读同一共享知识库，各自的会话保持私有。首期不建设独立的伙伴管理系统。

后续同步版本从飞书更新系统内副本；目录、搜索结果和回答引用均在 BoyanKB 内打开，伙伴无需加入飞书知识空间。

伙伴可选择原生模型供应商、DeepSeek、OpenAI 兼容或 Anthropic 兼容服务，使用自己的 API Key。公司提供的模型服务采用伙伴专属网关密钥，网关计量与收费在试点阶段验收，详见 [模型服务](docs/boyankb/models.md)。

首期部署在本地 PC。远程访问入口在 VPN 与 HTTPS 域名之间选定后验收。云服务器迁移纳入后续版本。

## 开发环境

| 组件 | 基线 |
|---|---|
| LibreChat | `main` · `7b2362d7a7c6148b84850924dc7fa5fc43307923` |
| Node.js / npm | `24.16.0` / `11.13.0` |
| 前端 | React、TypeScript、Vite |
| 后端 | Node.js、TypeScript、Express |
| 业务数据 | MongoDB |
| 知识索引 | PostgreSQL、pgvector |
| 本地运行 | Windows、WSL2、Docker Desktop、Compose V2 |
| Windows 命令环境 | PowerShell 7+ |

安装 Docker Desktop、Compose V2 和 PowerShell 7+ 后运行：

```powershell
pwsh -NoProfile -File scripts/boyankb/start-local.ps1
pwsh -NoProfile -File scripts/boyankb/manage-users.ps1 -Action create-user
```

访问 `http://localhost:3080`。首个原生账号为管理员；在原生界面创建知识 Agent 后绑定其标识：

```powershell
pwsh -NoProfile -File scripts/boyankb/configure-agent.ps1 -AgentId agent_xxx
```

通过原生分享界面逐用户授予查看权限。应用仅监听本机，独立 MongoDB 不发布宿主端口，凭据保存在 Git 忽略的 `.local/boyankb-librechat`。完整配置与命令见 [部署文档](docs/boyankb/deployment.md)。

检查项目文件：

```powershell
node scripts/boyankb/check-project.mjs
```

## 项目文档

- [产品需求](PRD.md)
- [架构与授权](docs/boyankb/architecture.md)
- [飞书同步](docs/boyankb/sync.md)
- [飞书应用开通](docs/boyankb/feishu-setup.md)
- [资料覆盖](docs/boyankb/source-coverage.md)
- [部署与恢复](docs/boyankb/deployment.md)
- [模型服务](docs/boyankb/models.md)
- [版本规范](docs/boyankb/versions.md)
- [开发路线图](docs/boyankb/roadmap.md)
- [验收清单](docs/boyankb/acceptance.md)
- [开发约束](AGENTS.md)

## 维护与许可

维护者：[@yeungmingyik](https://github.com/yeungmingyik)。

项目基于 [LibreChat](https://github.com/danny-avila/LibreChat)，保留其 [MIT 许可证](LICENSE)。上游来源固定于 [upstream.lock.json](upstream.lock.json)。
