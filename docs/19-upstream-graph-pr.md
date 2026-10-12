# 知识图谱状态误报修复与 PR 准备

日期：2026-10-12。官方 PR 尚未创建或提交。

## 补丁范围

官方基线：`e5f9f9deab36898676fe85a9fda6b3e6f00288fa`。独立工作树：`D:/BoyanKB/WeKnora-worktrees/upstream-graph-availability`。贡献分支：`feat/graph-availability-state`，提交：`0400fe0c230dc8fadb2dacd5a1fb9fe547f35690`。

修复系统信息仍在加载或获取失败时，被误报为图数据库未启用的问题。区分加载、启用、关闭、失败四种状态；失败可强制重试，晚到请求不能覆盖新结果。缺失或不合法的引擎字段显示检查失败；保留上游对非空引擎名称的兼容性。普通和嵌入设置页均覆盖，读取状态不修改已有抽取配置。

公开补丁只有八个文件：`GraphSettings.vue`、新增 `GraphSettings.test.ts`，以及简中、繁中、英语、日语、韩语、俄语六个语言文件。无新依赖，无后端、权限、配置或部署改动。官方帮助链接、品牌、LICENSE 和版权声明保留。没有合并或 cherry-pick 企业定制提交，代码提交的直接父提交就是上述官方基线。

## 验证

- 38 项组件测试通过。编译、挂载真实 Vue 模板，模拟 store/API 与 TDesign 控件；覆盖两种布局、合法引擎、关闭、HTTP 500/403/超时、异常响应、强制重试、旧响应保护；检查控件展示的文本、指令、标签、实体和关系仍与保存配置一致，且没有配置更新事件和模型调用。
- 同一测试副本对官方原组件运行：8 项通过、30 项失败。两份测试 SHA256 一致，证明回归测试能捕获本问题。
- 12 组真实浏览器检查通过。使用独立 Headless Edge，真实 Vue SFC、TDesign/CSS、中英文词条；HTTP/store/auth 使用模拟边界。保存 20 张截图，没有外部请求、页面运行错误或配置更新。
- Windows Node 24.14.1：类型检查与生产构建通过。首次完整测试为 1,529 通过、6 失败、2 取消、1 跳过；失败包括 CRLF 敏感断言、POSIX shell 不兼容及开发代理超时。
- Linux Node 24.20.0 / npm 11.19.0，使用与 Git blob 一致的 LF 源码：`npm ci`、完整 `npm test`（1,537 通过、1 跳过、0 失败）、`npm run type-check`、`npm run build` 全部通过。构建只有已有的大 chunk 提示。

Windows 默认 `git archive` 也可能应用 `core.autocrlf=true`。首次 Linux 导出的 CRLF 内容导致四项源文本正则测试失败。用 `git -c core.autocrlf=false archive` 重新导出，已确认源码内容与 Git blob 逐字节一致；没有为此修改全局 Git 设置、沙箱代码或技能页面。

复核命令，在贡献工作树执行：

```bash
git diff --check upstream/main...HEAD
git diff --name-only upstream/main...HEAD
git log --format='%H %P %s' upstream/main..HEAD
git diff upstream/main HEAD -- LICENSE
cd frontend
npm ci --no-audit --no-fund
node --import tsx --test src/views/knowledge/settings/GraphSettings.test.ts
npm test
npm run type-check
npm run build
```

局部 UI 修复没有运行完整服务端部署、Neo4j 集成、模型抽取或 Docker 镜像集成测试；浏览器证据不能称为完整端到端验收。官方 CI 需在提交 PR 后运行。

## 提交前交付物

PR 标题：`fix(frontend): distinguish graph availability errors from disabled state`。

PR 正文按官方模板准备，包含复现、修复行为、测试范围与截图。没有捏造 Issue 编号，也没有创建 Draft PR；Draft PR 本身仍属于已提交 PR。

正式比较必须选择 base `Tencent/WeKnora:main`、head `yeungmingyik/WeKnora:feat/graph-availability-state`。截图单独保存在 Fork 的 `feat/graph-availability-evidence` 分支，避免二进制证据进入代码 PR 的差异。截图提交 `6177f5ca1248fbf86dc9c50cebf3218b7b901c9f` 只在代码修复之上新增四张无业务数据的图片。

本机交付目录：`D:/BoyanKB/WeKnora/temp/upstream-graph-pr/`。包含 `pr-title.txt`、`pr-body.md`、`browser-results.json`、`screenshots/`、浏览器复现脚本与检查日志。基线反证目录：`D:/BoyanKB/WeKnora/temp/upstream-graph-pr-baseline/`。

内置浏览器连接不可用，采用预填 URL 携带标题和正文。只有点击 GitHub 的 Create pull request 才会提交；本步骤不执行该动作。

自动审批拦截了生成并打开 PR 快捷方式的组合命令，仅返回 `blocked by policy`，没有提供具体原因。改为保存 Markdown 草稿和预填链接，不启动快捷方式；没有在已登录的 GitHub 页面确认表单显示。需要用户打开下面的预填入口审阅后，再决定是否提交。

## 后续合入

官方若要求修改，仅在贡献分支调整相关代码并运行针对性检查；不得将私有仓库或截图分支合入贡献分支。若上游基线更新，先评估冲突及 API 变化，再补对应验证。此任务没有更新云端或本机运行中的定制服务。

## 审阅入口

[打开已预填标题和正文的官方 PR 页面](https://github.com/Tencent/WeKnora/compare/main...yeungmingyik:feat/graph-availability-state?quick_pull=1&title=fix%28frontend%29%3A+distinguish+graph+availability+errors+from+disabled+state&body=%23%23+Description%0A%0AWhen+%60GET+%2Fapi%2Fv1%2Fsystem%2Finfo%60+is+pending+or+fails%2C+graph+settings+currently+report+that+the+graph+database+is+not+enabled.+Users+see+deployment+advice+even+for+a+transient+network+error%2C+with+no+way+to+retry+in+the+component.%0A%0ADistinguish+loading%2C+enabled%2C+disabled+and+error+states+in+both+standalone+and+embedded+settings.+Only+an+explicit+%60Not+Enabled%60+response+shows+the+setup+warning.+Failed+or+malformed+responses+offer+a+forced+retry%3B+older+requests+cannot+overwrite+a+newer+result.+Preserve+saved+extraction+settings%2C+existing+graph-engine+compatibility+and+the+documentation+link.+Add+messages+in+all+six+locales.%0A%0A%23%23+Type+of+Change%0A%0A-+%5Bx%5D+%F0%9F%90%9B+Bug+fix%0A-+%5B+%5D+%E2%9C%A8+New+feature%0A-+%5B+%5D+%F0%9F%92%A5+Breaking+change%0A-+%5B+%5D+%F0%9F%93%9A+Documentation+update%0A-+%5B+%5D+%F0%9F%8E%A8+Refactor%0A-+%5B+%5D+%E2%9A%A1+Performance+improvement%0A-+%5Bx%5D+%F0%9F%A7%AA+Test%0A-+%5B+%5D+%F0%9F%94%A7+Configuration+%2F+Build+%2F+CI%0A%0A%23%23+Related+Issue%0A%0ANo+separate+issue+filed.+Reproduced+against+%60e5f9f9deab36898676fe85a9fda6b3e6f00288fa%60.%0A%0A%23%23+Testing%0A%0A-+38+component+regression+tests+pass.+The+same+tests+against+the+unmodified+component+fail+in+30+cases%2C+including+loading%2C+failed+requests%2C+malformed+responses+and+retry+behavior.%0A-+Tests+compile+and+mount+the+real+Vue+template%2C+checking+both+layouts%2C+HTTP+500%2F403%2Ftimeouts%2C+non-Neo4j+engines%2C+forced+retry%2C+stale+responses+and+preservation+of+rendered+configuration+without+update+events+or+extraction+calls.%0A-+12+browser+scenarios+pass+across+English%2FChinese+and+standalone%2Fembedded+layouts%2C+using+the+real+SFC+and+TDesign+with+mocked+HTTP%2Fstore%2Fauth+boundaries.+This+is+not+a+full+backend+end-to-end+test.%0A-+Linux+%2F+Node+24.20.0%3A+%60npm+ci+--no-audit+--no-fund%60%2C+%60npm+test%60+%281%2C537+passed%2C+1+skipped%2C+0+failed%29%2C+%60npm+run+type-check%60+and+%60npm+run+build%60+all+pass.%0A-+Initial+Windows+checks+encountered+CRLF-sensitive+source+assertions%2C+POSIX-shell+incompatibilities+and+proxy+timeouts.+Linux+verification+uses+an+LF+archive+matching+Git+blobs%3B+no+unrelated+source+changes+are+included.%0A%0AReproduce+manually+by+delaying+or+failing+the+system-info+request%2C+opening+graph+settings%2C+then+restoring+the+endpoint+and+selecting+Retry.+Only+an+explicit+%60Not+Enabled%60+should+display+the+disabled+warning.+Saved+extraction+settings+must+remain+unchanged+throughout.%0A%0A%23%23+Checklist%0A%0A-+%5Bx%5D+%60git+diff+--check+upstream%2Fmain...HEAD%60+passes%0A-+%5Bx%5D+Changed+source+files+follow+existing+formatting%0A-+%5Bx%5D+Targeted+tests+for+the+changed+component+pass%0A-+%5Bx%5D+Diff-scoped+lint%3A+no+frontend+lint+command+configured%3B+type-check+used%0A-+%5Bx%5D+Full+frontend+checks+completed%3B+backend+and+Docker-image+integration+were+not+run+for+this+frontend-only+change%0A-+%5Bx%5D+Self-reviewed+the+code%0A-+%5Bx%5D+Added+tests+covering+the+change%0A-+%5Bx%5D+No+user-facing+documentation+or+API+changes+required%3B+reproduction+steps+above%0A-+%5Bx%5D+No+breaking+changes%0A%0A%23%23+Screenshots+%2F+Recordings%0A%0AIsolated+component+harness%2C+with+simulated+system-info+responses+and+no+business+data.%0A%0A%7C+Before%3A+HTTP+500+%7C+After%3A+HTTP+500+%7C%0A%7C+---+%7C+---+%7C%0A%7C+%21%5BBefore%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fbefore-error-standard-en.png%29+%7C+%21%5BAfter%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fafter-error-standard-en.png%29+%7C%0A%0A%7C+Retry+succeeds+%7C+Embedded+error+%7C%0A%7C+---+%7C+---+%7C%0A%7C+%21%5BRecovered%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fafter-retry-standard-en.png%29+%7C+%21%5BEmbedded%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fafter-error-embedded-en.png%29+%7C%0A)

[审阅代码差异](https://github.com/Tencent/WeKnora/compare/main...yeungmingyik:feat/graph-availability-state)。GitHub 比较验证为 1 个提交、8 个文件；检查时官方对应 PR 数量为 0。预填使用 GitHub 支持的 [query parameters](https://docs.github.com/en/pull-requests/reference/using-query-parameters-to-create-a-pull-request)，不会自动点击提交按钮。
