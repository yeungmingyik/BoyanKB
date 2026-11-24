# 上游 Issue 与 PR 候选

知识图谱状态候选已完成独立官方分支修复、测试，并按用户授权提交 [官方 PR #4172](https://github.com/Tencent/WeKnora/pull/4172)，见 [实施记录与审阅入口](19-upstream-graph-pr.md)。下文保留最初核查依据，后续验证结果以上述记录为准。

核查日期：2026-10-12。核查官方 `Tencent/WeKnora` 的 `main` 提交为 `21ef506fc995259e48680477ce8eb06556bbd957`。本轮没有向官方发送 Issue、PR 或评论；下面提供可复核的问题和提交草稿。

## 1. 当前建议

| 问题 | 本地情况 | 最新官方状态 | 建议 |
| --- | --- | --- | --- |
| 图谱状态查询失败误报数据库未启用 | 定制版已有状态区分与重试 | 最新源码仍将未知、加载中、失败与明确禁用混同；未找到直接对应 Issue/PR | 先在最新官方版本复现，再做一个小型前端 PR |
| 清空同步 cron 后旧周期仍保留 | GORM 结构体更新忽略空字符串，本项目暂停脚本保留周期并暂停数据源 | [Issue #3652](https://github.com/Tencent/WeKnora/issues/3652)、[PR #3653](https://github.com/Tencent/WeKnora/pull/3653) 已解决，PR 于 2026-09-25 合并 | 不重复提交，后续升级评估官方修复及 PUT 的完整更新语义 |
| 共享知识库图谱检索按当前租户查分块，漏掉源库结果 | 本地 `fb37003d` 同时包含补图、授权与检索调整 | [PR #3687](https://github.com/Tencent/WeKnora/pull/3687) 已于 2026-09-25 合并，使用跨租户 ID 查询并筛选授权知识库 | 不重复提交，不把本地大提交整体贡献给官方 |

检索未找到重复项不代表绝对不存在；正式发帖前再次搜索官方 [Issues](https://github.com/Tencent/WeKnora/issues) 和 [Pull requests](https://github.com/Tencent/WeKnora/pulls)。图谱证据有效性另有 [PR #3955](https://github.com/Tencent/WeKnora/pull/3955) 处理中，不混入本次 UI 问题。

## 2. 推荐 Issue 草稿

标题：`[Bug]: 知识图谱设置把系统信息加载失败误报为图数据库未启用`

在知识图谱设置页，`GET /api/v1/system/info` 仍在加载或请求失败时，界面会显示图数据库未启用。这会让管理员把网络或接口故障误认为部署缺少数据库，而且页面没有直接重试入口。

复现环境应填写实际运行的官方提交、浏览器和部署方式。当前源码确认基准是 `21ef506fc995259e48680477ce8eb06556bbd957`；**最新官方版本浏览器故障注入复现未验证**，完成下面步骤后再将结果和截图填入官方表单，不把定制环境当作原版复现。

复现步骤：

1. 在图数据库确已启用的测试部署中登录，确认正常系统信息接口返回 `graph_database_engine: "Neo4j"`。
2. 在浏览器开发者工具中阻断 `*/api/v1/system/info`，或使用测试路由让该请求返回 500。
3. 刷新并打开知识库的知识图谱设置，观察提示。
4. 解除请求阻断，检查页面是否提供重新检查入口。

预期：加载时显示正在检查，失败时提示状态获取失败并提供重试；只有服务端明确返回禁用状态时才提示数据库未启用。状态未知时仍禁止提交依赖图数据库的操作，不改变已经保存的抽取设置。

当前源码表现：初始 `systemInfo=null`，计算属性回落为 `false`；请求异常只写控制台；页面根据假值直接展示禁用提示。

源码依据：

- [GraphSettings.vue 第 383 行](https://github.com/Tencent/WeKnora/blob/21ef506fc995259e48680477ce8eb06556bbd957/frontend/src/views/knowledge/settings/GraphSettings.vue#L383)：初始状态为空。
- [第 386 行](https://github.com/Tencent/WeKnora/blob/21ef506fc995259e48680477ce8eb06556bbd957/frontend/src/views/knowledge/settings/GraphSettings.vue#L386)：未取得信息时计算为未启用。
- [第 8 行](https://github.com/Tencent/WeKnora/blob/21ef506fc995259e48680477ce8eb06556bbd957/frontend/src/views/knowledge/settings/GraphSettings.vue#L8)：禁用提示条件；嵌入页面也有同类条件。
- [第 581 行](https://github.com/Tencent/WeKnora/blob/21ef506fc995259e48680477ce8eb06556bbd957/frontend/src/views/knowledge/settings/GraphSettings.vue#L581)：异常处理缺少可见错误状态。
- [第 599 行](https://github.com/Tencent/WeKnora/blob/21ef506fc995259e48680477ce8eb06556bbd957/frontend/src/views/knowledge/settings/GraphSettings.vue#L599)：挂载时读取，无用户重试入口。

## 3. 最小 PR 方案

建议标题：`fix(frontend): distinguish graph availability errors from disabled state`

只提交 `GraphSettings.vue` 的 loading、enabled、disabled、error 状态区分、重试入口，必要的小型状态 helper、测试和上游现有语言文件。保留官方文档链接、品牌及数据库类型兼容行为。

本地参考为 `frontend/src/views/knowledge/settings/graphAvailability.ts` 和 `graphAvailability.test.ts`；本轮使用 Node 24.14.1 实际运行 3 项测试通过：

```bash
node --experimental-strip-types --test frontend/src/views/knowledge/settings/graphAvailability.test.ts
```

这些测试覆盖失败后重试、明确关闭与未知状态区分、旧响应不覆盖新结果；仅证明本地 helper，不证明最新官方页面已运行。官方已有版本化请求缓存，因此旧响应保护是新增重试需要的回归测试，不单独宣称当前官方已复现竞态。

本地提交 `af79474c` 同时包含技能和品牌调整，不能整个 cherry-pick。本地 helper 对 Neo4j 的限制也不能直接移植，应保持上游已有兼容语义，并关注 [Memgraph PR #3800](https://github.com/Tencent/WeKnora/pull/3800) 的合并状态。

验证至少覆盖：初次加载、正常启用、明确禁用、500/超时/请求被拒、缺字段响应、重试成功、晚到的失败响应、普通与嵌入页面，以及读取失败不会清空已保存的提取配置。UI 改动附错误和恢复后的截图，不携带企业名称、用户邮箱、服务器地址、知识库正文或访问令牌。

## 4. 提交路径

使用公开 Fork 的独立检出目录，避免误推私有定制历史。以下命令只准备官方贡献分支，尚未在本轮执行：

```bash
git clone https://github.com/yeungmingyik/WeKnora.git WeKnora-upstream-pr
cd WeKnora-upstream-pr
git remote add upstream https://github.com/Tencent/WeKnora.git
git fetch upstream main
git switch -c feat/graph-availability-state upstream/main
```

先完成最新上游复现，确认没有重复项，再在 [官方 Bug 表单](https://github.com/Tencent/WeKnora/issues/new?template=bug_report.yml) 填写第 2 节内容，取得 Issue 编号。基于最小方案实施、测试并提交，推送贡献分支到公开 Fork，在 GitHub 选择 base `Tencent/WeKnora:main`、head `yeungmingyik/WeKnora:feat/graph-availability-state`。

PR 描述写具体触发、修复后行为、Issue 编号、针对性测试及截图；按 [官方 PR 模板](https://github.com/Tencent/WeKnora/blob/main/.github/pull_request_template.md) 填写。提交前执行 `git diff --check upstream/main...HEAD` 并人工确认差异只有通用修复，不包含 BoyanKB 代码、部署材料或凭据。提交标题遵循 Conventional Commits。

没有完成的检查必须如实写未验证，不因完整仓库测试受无关环境问题阻塞而宣称全通过。若后续发现涉及跨空间越权或敏感信息泄漏的漏洞，按 [官方安全政策](https://github.com/Tencent/WeKnora/blob/main/SECURITY.md) 私密报告，不在普通公开 Issue 发布业务数据或漏洞利用细节。
