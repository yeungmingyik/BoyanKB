# 版本管理

## 版本来源

BoyanKB 产品版本以根目录 `VERSION` 为准，采用 SemVer。当前为 `0.1.0-alpha.2`。上游包版本保留在各 `package.json`，不与产品版本混写；固定源提交记录于 `upstream.lock.json`。

| 版本类型 | 使用范围 |
|---|---|
| `0.1.0-alpha.N` | 本地集成与功能开发 |
| `0.1.0-beta.N` | 完成主要流程后的伙伴试点 |
| `0.1.0` | 本地正式版，通过全部 P0 门槛 |
| `0.1.x` | 兼容修复 |
| `0.2.0` | 云端部署与迁移能力 |
| `1.0.0` | 核心数据契约和交付标准稳定后发布 |

## 仓库与分支

| 项目 | 规则 |
|---|---|
| origin | `https://github.com/yeungmingyik/BoyanKB.git` |
| upstream | `https://github.com/danny-avila/LibreChat.git` |
| 稳定分支 | `main` |
| 当前开发分支 | `local-deployment` |
| 后续功能分支 | `local-deployment`、`account-access`、`feishu-sync`、`knowledge-reader`、`knowledge-search` |
| 版本标签 | `v` + VERSION，例如 `v0.1.0-alpha.1` |
| 提交规范 | Conventional Commits；无表情或非标准前缀 |
| 提交身份 | 仓库所有者的已验证 Git 身份，无机器人共同作者 |

初始导入采用锁定上游提交的源代码快照，在 BoyanKB 建立独立提交历史，保留 MIT 文本与来源记录。不得声称上游代码由 BoyanKB 维护者原创。上游提交对象保留于本地 upstream 引用，不推送为产品分支或产品标签。

原始上游工作流保存在 `.github/upstream-workflows/`，不作为 BoyanKB 的活动工作流。产品工作流仅使用 `.github/workflows/`；不继承上游发布、自动改分支、翻译机器人或外部审核配置。先运行项目文件检查，功能阶段按改动风险补充构建与集成验证。

## 提交

允许 `feat`、`fix`、`docs`、`refactor`、`perf`、`test`、`build`、`ci`、`chore`、`revert`。可带功能 scope；破坏性变更使用 `!` 和必要的 `BREAKING CHANGE` 字段。

```text
feat(sync): import Feishu document revisions
fix(auth): reject revoked knowledge access
docs: define knowledge base requirements
chore(release): prepare 0.1.0-beta.1
```

提交与标签只描述实际变更。发布标签在对应版本验证完成后创建，不用未来能力作为当前版本说明。推送、GitHub Release 和部署是各自独立动作，不能因本地存在版本号而标为已发布。

## 发布记录

每个版本同步更新 `VERSION`、README 能力状态和 `CHANGELOG.md`。日志采用“新增、变更、修复、移除”中的适用类别；计划留在路线图，验收结果留在验收记录。

应用开发后，构建读取 `VERSION` 显示产品版本，构建产物标记源码提交；不要求手动修改所有上游 npm 包版本。交付记录包含镜像 digest、数据迁移、配置兼容性、验证结果和回滚版本。

## 上游更新

1. 获取 `upstream/main`，比较 `upstream.lock.json` 中的旧 SHA 与候选 SHA，检查 release、依赖和数据迁移变化。
2. 在 `upstream-update` 功能分支应用旧快照到候选快照的差异，解决 BoyanKB 定制冲突。独立历史不执行无审查的 unrelated-history 合并。
3. 保留 BoyanKB 文档、版本、工作流、品牌及配置边界；更新受影响适配层，检查新增上游能力是否产生访问旁路。
4. 运行受影响的类型检查、构建、原生登录邀请、权限撤销、飞书发布、检索引用和数据恢复验证。
5. 通过后更新锁文件及 CHANGELOG，记录新的依赖镜像，不覆盖已有产品标签。

上游采用 `main` 的最新已核实提交作为候选；实际部署始终使用固定 SHA。不得由定时任务直接升级生产环境。

## 规划完成标准

固定基线可追溯，远程连接正确，PRD 与技术规格一致，所有规划能力明确未实现，文档链接有效，版本文件和日志一致，真实飞书资料与凭据未进入提交。业务运行测试属于后续版本，不在规划版本中虚报。
