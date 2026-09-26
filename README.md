# BoyanKB · 博言知识库

面向科创教育企业员工和授权合作伙伴的知识服务平台。资料统一在飞书维护，BoyanKB 管理系统内副本、授权阅读与用户个人会话。

## 业务领域

| 领域             | 知识内容                                       |
| ---------------- | ---------------------------------------------- |
| 中小学科创教育   | 机器人、无人机、编程、AI 课程与实施方案        |
| 成人 AI 培训     | 培训课程、应用案例、教学与交付材料             |
| 中小学 AI 营地   | 企业参访、AI 学习、作品制作与活动执行          |
| 中小学 AI 黑客松 | 活动方案、项目指导、作品与版权申请资料         |
| 运营与合作       | 合作方案、师资培训、交付标准、供应链与常见问题 |

## 产品版本

`0.1.0-alpha.4` 预发布候选：关键词与语义检索、带资料引用的知识问答、生成期间撤权与封禁控制。飞书整空间同步、系统内阅读和原生个人模型密钥沿用现有部署。

实际知识空间共发现 18 份资料，12 份已发布，6 份保持未发布并列明原因，见[资料覆盖清单](docs/boyankb/source-coverage.md)。

| 能力                               | 状态                                                    |
| ---------------------------------- | ------------------------------------------------------- |
| LibreChat 源码与固定基线           | 已纳入仓库                                              |
| PRD、技术架构、验收与版本管理      | 已建立                                                  |
| 原生账号、逐用户授权、封禁与撤权   | 已实现，验收记录见下文                                  |
| 本地 Docker 部署与持久化           | 已实现                                                  |
| 多供应商选择与个人 API Key         | 已集成；DeepSeek 的 OpenAI 与 Anthropic 兼容协议真实调用通过 |
| 飞书同步、系统内原文阅读与同步管理 | 合成场景验收通过；实际 18 份中 12 份已发布且可读        |
| 本地中文向量模型与原生索引发布     | 真实 RAG 验收通过；实际 12 个原生文件及向量关联核对通过 |
| 企业知识检索与问答                 | 已实现；合成资料问答、系统内引用与会话隔离验收通过      |
| 伙伴访问与备份恢复                 | 待验收                                                  |

本版本用于本地集成验证。完整资料格式覆盖、伙伴远程网络试点、公司模型网关计费和备份恢复演练仍在后续范围。开发计划见 [路线图](docs/boyankb/roadmap.md)，验证范围见 [验收清单](docs/boyankb/acceptance.md)，变更见 [更新日志](CHANGELOG.md)。

## 使用方式

管理员通过 LibreChat 原生命令创建账号，或配置邮件服务后发送邀请，再通过原生分享界面授予企业知识 Agent 的查看权限。关闭公开注册，授权用户通过浏览器登录；员工和伙伴可阅读同一共享知识库，各自的会话保持私有。首期不建设独立的伙伴管理系统。

管理员启用同步后，资料从飞书更新至系统内副本。授权用户通过“资料库”浏览目录和已发布原文，管理员在同一页面查看同步状态、触发扫描和重试。伙伴无需加入飞书知识空间。

在“资料库 → 搜索资料”输入标题、关键词或问题，可选择综合、关键词或语义搜索，并限定目录及子目录。点击结果定位到对应资料版本和正文片段。聊天中选择模型后直接提问；答案引用同样在系统内打开。缺少依据时显示资料不足，资料冲突时保留不同来源，不自动认定现行规则。

当前发布支持可完整解析的 Docx；未支持的格式和嵌入对象显示明确状态，不发布空白或部分新版本。图片与正文通过系统内授权读取，真实资料与凭据不进入仓库。

伙伴可选择原生模型供应商、DeepSeek、OpenAI 兼容或 Anthropic 兼容服务，使用自己的 API Key。公司提供的模型服务采用伙伴专属网关密钥，网关计量与收费在试点阶段验收，详见 [模型服务](docs/boyankb/models.md)。

首期部署在本地 PC。远程访问入口在 VPN 与 HTTPS 域名之间选定后验收。云服务器迁移纳入后续版本。

## 开发环境

| 组件             | 基线                                                |
| ---------------- | --------------------------------------------------- |
| LibreChat        | `main` · `7b2362d7a7c6148b84850924dc7fa5fc43307923` |
| Node.js / npm    | `24.16.0` / `11.13.0`                               |
| 前端             | React、TypeScript、Vite                             |
| 后端             | Node.js、TypeScript、Express                        |
| 业务数据         | MongoDB 单节点副本集                                |
| 知识索引         | PostgreSQL、pgvector                                |
| 向量模型         | 本地 `BAAI/bge-small-zh-v1.5`，固定版本，512 维     |
| 本地运行         | Windows、WSL2、Docker Desktop、Compose V2           |
| Windows 命令环境 | PowerShell 7+                                       |

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

完成 [飞书应用开通](docs/boyankb/feishu-setup.md) 并填写私有 `feishu.env` 后启用同步：

```powershell
pwsh -NoProfile -File scripts/boyankb/start-sync.ps1 -NoBuild
```

首次启动下载并校验向量模型；随后向量计算在本机离线运行。飞书同步仍需网络，问答模型按用户选择的服务连接。同步数据使用独立持久卷；自动物理清理和完整备份恢复尚未交付。

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
