# BoyanKB

基于 Tencent/WeKnora 的企业知识库与业务文档助手。上游基线为 `v0.8.2`，保留 MIT LICENSE 和上游版权声明。代码备份仓库为私有 [yeungmingyik/BoyanKB](https://github.com/yeungmingyik/BoyanKB)。

当前节点：`boyankb-checkpoint-2026-10-12`。这是已上线内部测试版本的代码与运维资料快照，不代表生产验收全部通过。

| 内容 | 入口 |
| --- | --- |
| 节点范围、固定版本、恢复和验证 | [节点说明](docs/17-checkpoint-20261012.md) |
| 原仓库架构与功能分析 | [仓库分析](docs/00-repo-analysis.md) |
| 业务需求与九项技能 | [业务规格](docs/business/agents-spec.md)、[预设说明](docs/04-presets.md) |
| 腾讯云九服务部署 | [部署记录](docs/13-tencent-deployment.md)、[维护命令](deployment/tencent/README.md) |
| 原生技能入口、角色权限及云端飞书同步 | [当前实施记录](docs/16-skills-and-cloud-sync.md) |
| RustDesk 配套远程支持 | [部署记录](docs/15-rustdesk-native.md) |
| 定制文件清单及升级重用方法 | [定制登记](docs/CUSTOMIZATIONS.md)、[升级手册](docs/05-upgrade-runbook.md) |
| 适合贡献上游的问题 | [Issue 与 PR 指引](docs/18-upstream-contributions.md) |

真实 `.env`、密钥、账号密码、数据库与对象文件、上传资料、模型缓存、镜像归档及本机 `temp/` 不进入 GitHub。恢复完整服务还需要独立保存的私有运行备份；单独克隆仓库不能恢复知识库内容或账号数据。

日常定制工作使用 `feat/<任务名>` 分支，提交遵循 Conventional Commits。官方贡献从最新 `upstream/main` 建独立分支，只提交通用修复；不要以整个企业定制分支向官方提交 PR。
