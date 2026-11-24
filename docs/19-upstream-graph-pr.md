# 知识图谱状态误报修复与上游 PR

日期：2026-10-12。已按用户授权提交官方 [PR #4172](https://github.com/Tencent/WeKnora/pull/4172)，状态为 Open，尚未合并。

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

首次提交时没有运行完整服务端部署、Neo4j 集成、模型抽取或 Docker 镜像集成测试；后续 Docker 验证见本文 2026-10-16 完整 CI 记录。浏览器证据不能称为完整端到端验收。官方 CI 需在提交 PR 后运行。

## 提交前交付物

PR 标题：`fix(frontend): distinguish graph availability errors from disabled state`。

PR 正文按官方模板准备，包含复现、修复行为、测试范围与截图。没有捏造 Issue 编号。此前仅准备表单，用户确认后创建正式 PR，没有创建 Draft PR。

正式比较必须选择 base `Tencent/WeKnora:main`、head `yeungmingyik/WeKnora:feat/graph-availability-state`。截图单独保存在 Fork 的 `feat/graph-availability-evidence` 分支，避免二进制证据进入代码 PR 的差异。截图提交 `6177f5ca1248fbf86dc9c50cebf3218b7b901c9f` 只在代码修复之上新增四张无业务数据的图片。

本机交付目录：`D:/BoyanKB/WeKnora/temp/upstream-graph-pr/`。包含 `pr-title.txt`、`pr-body.md`、`browser-results.json`、`screenshots/`、浏览器复现脚本与检查日志。基线反证目录：`D:/BoyanKB/WeKnora/temp/upstream-graph-pr-baseline/`。

2026-10-12 12:49（北京时间），已使用系统 Chrome 打开官方 PR 创建表单。通过 Windows UI Automation 读取实际标题和正文，确认与本机草稿完全一致；正文为 3,694 字符。已核对 base 为 `Tencent/WeKnora:main`，head 为 `yeungmingyik/WeKnora:feat/graph-availability-state`。没有点击 Create pull request，随后再次查询该分支的官方 PR 数量仍为 0。

表单核对记录：`temp/upstream-graph-pr/browser-form-verification.json`；实际页面截图：`temp/upstream-graph-pr/pr-form-review.png`。两者仅保存在本机，不进入公开贡献分支。内置浏览器连接仍不可用；本次系统浏览器打开成功，先前快捷方式组合命令的审批拦截已不再阻碍审阅。

## 重复修复检查

2026-10-12 12:48（北京时间）重新查询官方当前代码及公开 PR。官方 `main` 仍为上述基线，组件仍把系统信息未加载或加载失败判断为图数据库未启用。

- 检查全部 257 个开放 PR 的文件清单，包括超过 100 个文件的五个 PR 的后续分页。
- 仅 [#2944](https://github.com/Tencent/WeKnora/pull/2944) 和 [#2978](https://github.com/Tencent/WeKnora/pull/2978) 修改同一组件；逐行检查确认分别处理字段校验、关系标签拆分，不包含本次状态修复。
- 检索所有状态的相关 Issue/PR，包括 143 条英文 graph PR、41 条中文知识图谱 PR，以及组件、函数、响应字段和接口名关键词；检查相近的 #2666、#3562、#3800，均不解决本问题。
- 该文件在官方的最新改动为 `9253b2c1`（2026-09-24，#3674），只修改文档链接。

结论：截至查询，未发现相同的公开修复；无法排除他人尚未公开的分支或后续新提交。证据为 `temp/upstream-graph-pr/duplicate-audit.json`。提交前可再次运行 `gh pr list --repo Tencent/WeKnora --state all --search 'graph' --limit 1000`，并检查最新 `main` 和相关 PR 的实际差异，不能仅凭标题判断重复。

## 官方提交与复核

用户确认后，再次核对官方 `.github/pull_request_template.md` 和 README 的 Contributing 要求，保留全部六个模板段落，标题与代码提交均符合 Conventional Commits。两项独立只读审查分别检查实现、真实资源缓存契约、回归测试、模板和测试证据，均未发现必须修改项。38 项组件测试再次运行全部通过；本次没有代码变更，既有 Linux 全套前端测试、类型检查及构建记录仍对应提交 `0400fe0c`。

提交前重新获取官方 `main`，基线未变化。公开 Fork 与本地 HEAD 一致，差异为一个提交、八个文件。提交后再次读取 GitHub 正文、base/head、代码 SHA、文件清单，确认与审阅材料一致；GitHub 报告 `MERGEABLE`，只表示没有合并冲突，不代表 CI 已通过或维护者已批准。

2026-10-12 12:56（北京时间）查看官方 [Frontend 工作流](https://github.com/Tencent/WeKnora/actions/runs/38205714191)，状态为 `action_required`，页面明确提示等待维护者批准运行，任务列表为空；不是测试断言失败。腾讯 `GITHUB_OPENSOURCE_SCAN_PIPELINE@PULL_REQUEST` 已启动，尚在运行。没有代替维护者批准、绕过检查或执行合并。

提交快照为 `temp/upstream-graph-pr/submitted-pr.json`，汇总为 `temp/upstream-graph-pr/result.json`。复核后续状态：

```bash
gh pr view 4172 --repo Tencent/WeKnora --json url,state,isDraft,mergeable,mergeStateStatus,statusCheckRollup
gh pr checks 4172 --repo Tencent/WeKnora
gh run view 38205714191 --repo Tencent/WeKnora
```

## 后续合入

官方若要求修改，仅在贡献分支调整相关代码并运行针对性检查；不得将私有仓库或截图分支合入贡献分支。若上游基线更新，先评估冲突及 API 变化，再补对应验证。此任务没有更新云端或本机运行中的定制服务。

## 2026-10-16 主干冲突处理

GitHub 报告 `CONFLICTING`，唯一冲突文件为 `frontend/src/views/knowledge/settings/GraphSettings.vue`。原 PR 的 Frontend 测试、构建、Docker 多阶段检查和腾讯扫描均已成功，冲突来自提交后主干的界面变更，并非原测试失败。

最新主干为 `2fbfb4471581cd5e7d731bc7a2d443fe7d8e95b5`；其中 `8fae51c64`（[#4193](https://github.com/Tencent/WeKnora/pull/4193)）将旧 `t-alert` 改为图谱状态卡片。处理方式是在原贡献分支合入主干，保留双方提交历史，不重写远端历史：

- 保留上游 `graph-disabled` 卡片布局、图标、outline 指南按钮和嵌入样式。
- 卡片显示条件使用 `graphAvailability !== 'enabled'`，文案使用 `graphAvailabilityMessage`；加载中采用信息色和 `status` 语义。
- 仅失败显示重试，只有显式未启用且非嵌入模式才显示启用指南。
- 保留原有强制刷新、过期请求保护和已有抽取配置；38 项行为测试改用卡片及按钮选择器，覆盖范围不缩减。

相对新主干仍为原来的八个预期文件。与新主干的 LICENSE 差异为空；主干自身的许可证与其他变更通过合并承接，没有手改许可证。企业品牌、部署配置、账号、资料、截图分支和本文不进入公开贡献分支。此次仅处理公开 PR，不更新云端服务。

验证命令仍使用前文贡献工作树命令；合并期间可用 `git diff --check upstream/main` 和 `git diff --name-only upstream/main` 检查暂存后的最终内容，避免将上游自身已有格式问题误算成本次补丁问题。新增证据单独保存于本机忽略目录 `temp/upstream-graph-conflict/`，保留原始提交时的验证记录。

合并提交为 `0987db68d8a8d6d7bb3a496e7e2d53c8cb9c9494`，两个父提交分别为原 PR 的 `0400fe0c` 和上游 `2fbfb447`。已普通 push 到 `yeungmingyik/WeKnora:feat/graph-availability-state`，无 force push、无新建 PR、无代替维护者合并。GitHub 复核 `headRefOid` 与本地一致、`mergeable=MERGEABLE`，冲突已消失；首次复核 `mergeStateStatus=UNSTABLE` 是新一轮检查尚未完成，不能解释为仍有代码冲突。

本轮检查：38 项组件测试通过；Windows Node 24 的 `npm run type-check`、`npm run build` 均 exit 0；12 个独立浏览器场景通过，覆盖中英文、标准和嵌入布局、加载→500→重试恢复以及明确关闭，0 运行时错误、0 外部请求。20 张新版卡片截图位于 `screenshots-final/`。该 harness 复用了应用已有的离线图标 guard 与本地 sprite，未修改应用代码或依赖。独立代码复审通过，原状态保护与上游样式均保留。

本轮未重跑整个 Linux 测试集或 Docker 镜像集成；前文 1,537 项 Linux 通过是原提交 `0400fe0c` 的历史结果。PR 正文已补充本轮合并验证，并明确旧截图和旧全套测试的版本，避免混淆证据。官方新一轮 CI 需另看 PR Checks，`MERGEABLE` 仅代表没有冲突。

提交后复核：[新一轮 Frontend 工作流](https://github.com/Tencent/WeKnora/actions/runs/38858816921) 为 `action_required`、任务列表为空，页面明确显示等待维护者批准运行；不是已执行的测试失败。腾讯扫描仍在运行。本机验证已完成，不代替维护者审批或绕过官方检查。

随后按用户明确授权发布了 [PR 说明留言](https://github.com/Tencent/WeKnora/pull/4172#issuecomment-6209268990)，并回读核对作者、正文和链接。留言前再次确认远端仍为 `0987db68d`、无合并冲突、腾讯扫描已成功；Frontend 仍待维护者批准。按用户要求，留言已缩短为普通叙述：冲突已解决并保留新版卡片，加载或失败不再误报未启用，失败可重试，本地组件测试、类型检查与构建通过。详细验证范围继续保留在 PR 正文和本文。

## 2026-10-16 完整 Frontend CI 本地复现

按用户要求，针对贡献提交 `0987db68d8a8d6d7bb3a496e7e2d53c8cb9c9494` 复现 `.github/workflows/frontend.yml` 的两个 job。使用 `git -c core.autocrlf=false archive` 导出提交中的文件到隔离 Linux 目录，排除 Windows 换行、未跟踪文件、真实环境文件及旧构建产物。

`verify` 使用 Linux Node 24，按顺序运行：

```bash
cd frontend
npm ci --no-audit --no-fund
npm test
npm run type-check
npm run build
```

`docker-image` 使用该提交原样的 `frontend/Dockerfile` 和固定摘要的基础镜像，在仓库快照根目录运行：

```bash
docker build -f frontend/Dockerfile \
  --build-arg VITE_FRONTEND_COMMIT=0987db6 \
  -t weknora-ui:pr4172-0987db6-ci frontend/
python3 scripts/test_embed_nginx.py weknora-ui:pr4172-0987db6-ci
```

代理路由检查包括嵌入页 GET/HEAD、CSP 和 no-store、拒绝访问与后端异常、API 公网端口保留、MCP 请求方法／会话头／认证失败以及 SSE。测试容器只发布回环端口；脚本结束时仅清理自己创建的临时容器。环境、完整日志和提交对应关系保存在本机忽略目录 `temp/upstream-graph-full-ci/`。本地执行不替代 GitHub 对外部贡献工作流的维护者审批。

本机环境为 WSL2 Ubuntu 26.04 / Linux x86_64、Node 24.20.0、npm 11.19.0、Docker 29.1.3、BuildKit 0.26.2。使用临时 Node 与 Buildx 路径，没有修改全局 Git／npm／Docker 配置。两个 job 的命令保持官方流程，受本机内存限制，独立前端构建与 Docker 内的 Vite 构建错开执行。这个环境不是 GitHub 托管 runner，不能把本地结果写成官方 Actions 已通过。

复现时发现两项本机环境问题，均保留原始日志：

- 第一轮 Docker 快照含 CRLF（910 个文件仅换行不同、28 个文件完全一致）。该轮虽然构建和代理检查通过，不作为最终验收记录；重新用 `core.autocrlf=false` 导出后，按 Git blob 核验再重跑。独立前端测试快照的 5,084 个文件从一开始就全部匹配 Git blob。
- 独立 `npm run build` 首轮在默认约 2 GB 的 V8 堆上限处内存耗尽（exit 134）。重跑命令为 `NODE_OPTIONS=--max-old-space-size=4096 npm run build`，只影响这次构建进程；不修改源码、锁文件或官方 workflow。采样的默认 heap limit 为 2,240 MB，调整后为 4,288 MB，本机总内存约 7,798 MB。官方 Dockerfile 本身已有相同的 4 GB 构建堆配置。此处不能描述为默认本机环境一次通过。
- 运行期间 `/tmp` 下的隔离目录消失，原因未确定；日志和源归档已保留在 D 盘。重新在 `/var/tmp` 恢复相同源码与固定运行时，重新安装依赖后核验源码仍匹配 Git blob，之后继续构建。已通过的测试和类型检查结果来自原快照，最终构建来自同提交的恢复快照。

最终结果如下，全部对应贡献提交 `0987db68d`：

| 检查 | 结果 | 成功执行耗时 |
| --- | --- | ---: |
| `npm ci --no-audit --no-fund` | exit 0 | 147.096 秒 |
| `npm test` | 1,697 通过、1 跳过、0 失败、0 取消 | 293.710 秒 |
| `npm run type-check` | exit 0 | 207.735 秒 |
| `npm run build`（进程级 4 GB 堆） | exit 0，6,780 modules | 213.581 秒 |
| 原 Dockerfile，最终 LF 快照构建 | exit 0 | 320 秒 |
| 原 embed/MCP 代理检查 | exit 0 | 6 秒 |

唯一跳过为上游 `BrowserTaskPreview.browser.test.mjs` 的 human-handoff 浏览器测试；它需要 `BROWSERSKILL_TEST_CHROMIUM` 和 `BROWSERSKILL_TEST_PLAYWRIGHT`，官方 workflow 未设置这两个可选变量。本任务没有增加 skip 或删除断言。成功命令耗时不包括首次失败、恢复依赖和错峰等待，不能据此声称在同一默认环境中一次完成了有时限的 GitHub job。

最终 Docker 镜像为 `weknora-ui:pr4172-0987db6-ci`（linux/amd64），digest 为 `sha256:c0cbad20276c7bfb924f96d4255be63c212832d97a8b8f8b776cccaddb8c7a27`。未发布镜像；前端 5,084 个源文件以及 Docker 938 个源文件均完成 Git blob 比对，构建后无源文件变化。现有 chunk-size 警告没有导致构建失败。

证据入口为 `temp/upstream-graph-full-ci/frontend/20261016-142754/summary.json` 和 `temp/upstream-graph-full-ci/docker/summary.json`；前者包含首轮 OOM、恢复安装及构建重试的分开记录，后者仅以最终 LF 轮作为成功依据，并保留早期环境问题日志。独立只读复核检查了命令、日志、归档和公开 diff，未发现阻塞项。

额外执行的 `npm audit --json` 不在官方 CI 流程中，退出 1，报告 14 个依赖包的告警（1 critical、8 high、3 moderate、2 low），不是 14 个独立可利用漏洞。其中 critical 是开发依赖链 `npm-run-all2 → shell-quote@1.10.0`；high 包括直接依赖 axios 和 vue 等。审计级别来自 npm，未做可利用性验证，不能据此声称这些告警全部影响线上或全部无害。`frontend/package.json` 和锁文件与上游 `2fbfb447` 完全一致，本 PR 未引入或修改这些依赖；没有执行 `npm audit fix` 或将无关依赖升级混入 UI 修复。需要另行评估依赖升级，完整记录为 `frontend/20261016-142754/audit.json` 和 `audit-summary.json`。

2026-10-16 15:01（北京时间），全部本地检查完成后更新了 [PR #4172](https://github.com/Tencent/WeKnora/pull/4172) 的 Testing／Checklist，并编辑原有 [留言](https://github.com/Tencent/WeKnora/pull/4172#issuecomment-6209268990)，没有新增重复留言。正文保留官方模板，说明构建内存调整、额外 audit 结果及验证边界。已回读核对正文、留言与本机草稿一致，记录为 `temp/upstream-graph-full-ci/pr-update-verification.json`。本轮无需修补 PR 源码，没有创建空提交或再次 push；公开差异仍为八个文件，不含本文、企业配置或品牌定制。

复核时远端 head/base 未变，`mergeable=MERGEABLE`，腾讯扫描成功；Frontend 工作流 `38858816921` 仍为 `action_required`、jobs 为空，需维护者批准。已按前文 `gh pr view`／`gh run view` 命令核查，不能将本地通过写成官方 Actions 已通过。

随后按用户要求简化公开措辞：从 PR 正文和原留言中移除本机 Node 堆配置说明，正文仅写 `npm run build` 在本地通过。已回读核对；没有改成官方 CI 已通过，也没有改动代码或测试结果。详细环境调整和首次失败日志继续保留于本文及本机证据目录。

## 审阅入口

[打开已预填标题和正文的官方 PR 页面](https://github.com/Tencent/WeKnora/compare/main...yeungmingyik:feat/graph-availability-state?quick_pull=1&title=fix%28frontend%29%3A+distinguish+graph+availability+errors+from+disabled+state&body=%23%23+Description%0A%0AWhen+%60GET+%2Fapi%2Fv1%2Fsystem%2Finfo%60+is+pending+or+fails%2C+graph+settings+currently+report+that+the+graph+database+is+not+enabled.+Users+see+deployment+advice+even+for+a+transient+network+error%2C+with+no+way+to+retry+in+the+component.%0A%0ADistinguish+loading%2C+enabled%2C+disabled+and+error+states+in+both+standalone+and+embedded+settings.+Only+an+explicit+%60Not+Enabled%60+response+shows+the+setup+warning.+Failed+or+malformed+responses+offer+a+forced+retry%3B+older+requests+cannot+overwrite+a+newer+result.+Preserve+saved+extraction+settings%2C+existing+graph-engine+compatibility+and+the+documentation+link.+Add+messages+in+all+six+locales.%0A%0A%23%23+Type+of+Change%0A%0A-+%5Bx%5D+%F0%9F%90%9B+Bug+fix%0A-+%5B+%5D+%E2%9C%A8+New+feature%0A-+%5B+%5D+%F0%9F%92%A5+Breaking+change%0A-+%5B+%5D+%F0%9F%93%9A+Documentation+update%0A-+%5B+%5D+%F0%9F%8E%A8+Refactor%0A-+%5B+%5D+%E2%9A%A1+Performance+improvement%0A-+%5Bx%5D+%F0%9F%A7%AA+Test%0A-+%5B+%5D+%F0%9F%94%A7+Configuration+%2F+Build+%2F+CI%0A%0A%23%23+Related+Issue%0A%0ANo+separate+issue+filed.+Reproduced+against+%60e5f9f9deab36898676fe85a9fda6b3e6f00288fa%60.%0A%0A%23%23+Testing%0A%0A-+38+component+regression+tests+pass.+The+same+tests+against+the+unmodified+component+fail+in+30+cases%2C+including+loading%2C+failed+requests%2C+malformed+responses+and+retry+behavior.%0A-+Tests+compile+and+mount+the+real+Vue+template%2C+checking+both+layouts%2C+HTTP+500%2F403%2Ftimeouts%2C+non-Neo4j+engines%2C+forced+retry%2C+stale+responses+and+preservation+of+rendered+configuration+without+update+events+or+extraction+calls.%0A-+12+browser+scenarios+pass+across+English%2FChinese+and+standalone%2Fembedded+layouts%2C+using+the+real+SFC+and+TDesign+with+mocked+HTTP%2Fstore%2Fauth+boundaries.+This+is+not+a+full+backend+end-to-end+test.%0A-+Linux+%2F+Node+24.20.0%3A+%60npm+ci+--no-audit+--no-fund%60%2C+%60npm+test%60+%281%2C537+passed%2C+1+skipped%2C+0+failed%29%2C+%60npm+run+type-check%60+and+%60npm+run+build%60+all+pass.%0A-+Initial+Windows+checks+encountered+CRLF-sensitive+source+assertions%2C+POSIX-shell+incompatibilities+and+proxy+timeouts.+Linux+verification+uses+an+LF+archive+matching+Git+blobs%3B+no+unrelated+source+changes+are+included.%0A%0AReproduce+manually+by+delaying+or+failing+the+system-info+request%2C+opening+graph+settings%2C+then+restoring+the+endpoint+and+selecting+Retry.+Only+an+explicit+%60Not+Enabled%60+should+display+the+disabled+warning.+Saved+extraction+settings+must+remain+unchanged+throughout.%0A%0A%23%23+Checklist%0A%0A-+%5Bx%5D+%60git+diff+--check+upstream%2Fmain...HEAD%60+passes%0A-+%5Bx%5D+Changed+source+files+follow+existing+formatting%0A-+%5Bx%5D+Targeted+tests+for+the+changed+component+pass%0A-+%5Bx%5D+Diff-scoped+lint%3A+no+frontend+lint+command+configured%3B+type-check+used%0A-+%5Bx%5D+Full+frontend+checks+completed%3B+backend+and+Docker-image+integration+were+not+run+for+this+frontend-only+change%0A-+%5Bx%5D+Self-reviewed+the+code%0A-+%5Bx%5D+Added+tests+covering+the+change%0A-+%5Bx%5D+No+user-facing+documentation+or+API+changes+required%3B+reproduction+steps+above%0A-+%5Bx%5D+No+breaking+changes%0A%0A%23%23+Screenshots+%2F+Recordings%0A%0AIsolated+component+harness%2C+with+simulated+system-info+responses+and+no+business+data.%0A%0A%7C+Before%3A+HTTP+500+%7C+After%3A+HTTP+500+%7C%0A%7C+---+%7C+---+%7C%0A%7C+%21%5BBefore%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fbefore-error-standard-en.png%29+%7C+%21%5BAfter%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fafter-error-standard-en.png%29+%7C%0A%0A%7C+Retry+succeeds+%7C+Embedded+error+%7C%0A%7C+---+%7C+---+%7C%0A%7C+%21%5BRecovered%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fafter-retry-standard-en.png%29+%7C+%21%5BEmbedded%5D%28https%3A%2F%2Fraw.githubusercontent.com%2Fyeungmingyik%2FWeKnora%2F6177f5ca1248fbf86dc9c50cebf3218b7b901c9f%2Fdocs%2Fpr-evidence%2Fgraph-availability%2Fafter-error-embedded-en.png%29+%7C%0A)

[审阅代码差异](https://github.com/Tencent/WeKnora/compare/main...yeungmingyik:feat/graph-availability-state)。GitHub 比较验证为 1 个提交、8 个文件；检查时官方对应 PR 数量为 0。预填使用 GitHub 支持的 [query parameters](https://docs.github.com/en/pull-requests/reference/using-query-parameters-to-create-a-pull-request)，不会自动点击提交按钮。
