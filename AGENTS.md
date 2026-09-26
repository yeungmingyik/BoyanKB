# BoyanKB

- 新增和修改的代码、脚本、文档字符串不写解释性、元信息、历史或状态注释。产品界面只保留完成操作所需的文案；产品文档直述规格和使用方法。
- 所有 PowerShell 使用 `pwsh` 7+，不得回退至 Windows PowerShell。
- 提交遵循 Conventional Commits；功能分支按实际功能命名，例如 `feishu-sync`、`knowledge-search`，不使用 `codex/` 前缀。
- BoyanKB 提交作者和提交者使用仓库所有者已配置的 Git 身份；不添加机器人署名或共同作者。保留上游许可证。
- 飞书是唯一知识内容源。同步整个配置空间，伙伴通过系统内副本阅读；原始资料、凭据、备份和个人数据不得提交 Git。
- 优先复用 LibreChat 的本地账号、邀请、角色和会话能力。关闭公开注册；共享知识与个人会话使用各自的授权边界。
- 新后端逻辑放在 `packages/api`，`api` 只负责接线；数据库契约放在 `packages/data-schemas`，共享接口放在 `packages/data-provider`。前端复用现有组件和主题，新状态使用 Jotai。
- 新配置纳入现有配置 schema；新增界面文案维护简体中文和英文。对用户状态的修改同步使鉴权缓存失效。
- 按任务读取下表中的相关文件，不要求每次读取全部文档。技能只在任务需要时加载，不新增重复的流程约束。
- 在已授权范围内完成实现、相关验证与失败修复。只运行与改动风险相关的检查；使用隔离测试数据，不访问生产资料。
- 版本只记录实际交付能力；完成标准取自对应需求和验收条目，不能用规划代替运行结果。

| 工作 | 文件 |
|---|---|
| 需求与范围 | [PRD.md](PRD.md) |
| 模块、接口、权限 | [architecture.md](docs/boyankb/architecture.md) |
| 飞书接入与同步 | [sync.md](docs/boyankb/sync.md) |
| 环境与运维 | [deployment.md](docs/boyankb/deployment.md) |
| 版本与上游更新 | [versions.md](docs/boyankb/versions.md) |
| 排期与验收 | [roadmap.md](docs/boyankb/roadmap.md)、[acceptance.md](docs/boyankb/acceptance.md) |

工作约定遵循 [OpenAI 开发建议](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)：精简常驻指令、按需读取、明确完成边界。