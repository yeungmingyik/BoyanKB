# 合作伙伴预设与文件模板

2026-10-09 后续优化：本机新增“工具箱 → 业务技能”中文入口，成员可查看当前空间九项受限指南及真实启用状态；内置提示词通过独立覆盖中文化。新 PPT 使用单独的 `boyan-v2` 八版式设计，已有业务预设、Skill、Word/Excel/Markdown 和历史文件继续保留 v1。操作与验收见 [业务技能验证](06-agent-skill-validation.md)，设计与视觉版本见 [PPT 模板](06-ppt-design.md)。下文历史验收数据对应当时的 v1 文件，不应当作新版真实任务验收结果。

## 实现选择

采用“版本化预设文件 + 幂等 API 编排 + 固定文件渲染器”。不直接写业务表种子数据，也不把全局默认 Agent 当作所有伙伴共用的可编辑实例。

| 方案 | 适用性与取舍 |
| --- | --- |
| 初始化 SQL／全局内置 YAML | 初次创建方便，但难以处理已有工作空间、权限绑定、模型 ID 和幂等升级；不作为业务预设入口 |
| 每个伙伴手工配置 | 可立即操作，但容易漏掉知识库范围、角色或审校工具；只用于检查脚本结果 |
| API 编排、版本化清单 | 本次采用；保留原业务校验，每个伙伴有独立 Agent 和私有库，共享总部只读知识库；新增受限开户事务与安装登记 |
| 通用代码沙箱生成 Office | 能运行更多程序，但需要单独完成运行时隔离；本次文件生成使用只接受结构化输入的固定渲染器 |

用户已选择先暂停云端部署，当前配置和验收只针对本机。没有修改阿里云服务器或生产 Compose。

## 品牌与场景

平台品牌 BoyanKB，正式文件品牌博研文化。使用原 LOGO，主色浅蓝 `#60B9E8`，深蓝 `#1F4884`，操作蓝 `#2D64B0`，运营辅助色 `#379CB7`。

模板位于 `branding/templates/`，教学、商务、运营三类各有 PPTX、DOCX、XLSX、Markdown，共十二份。PPT 原生文字、图形、表格可编辑；Word 有统一页眉页脚；Excel 数字输入、预算公式与来源分开。详见 [模板说明](../branding/templates/README.md)。

| 教学 | 商务 | 运营 |
| --- | --- | --- |
| ![教学封面](assets/presets/teaching.png) | ![商务封面](assets/presets/business.png) | ![运营封面](assets/presets/operations.png) |

模板结构根据授权知识库的教学套件、运营 SOP 和业务规格设计。部分源文档只有标题，不能据此补写课程、案例、学校、价格或效果承诺。内部手册不预填到对外商务模板。

## Agent 与 Skill

`presets/boyan/v1/manifest.json` 是预设入口，包含三名 Agent、提示词文件、九个 Skill 名称、版本和摘要。

| Agent | 知识范围与工具 | 职责 |
| --- | --- | --- |
| A 业务导师 | 总部白名单知识库；检索与读取工具 | 基于证据答疑；无正式文件生成能力 |
| B 落地方案官 | 总部共享库与当前伙伴私有库；检索、读取、`boyan_document` | 检查输入、形成大纲、等待确认、按模板生成 |
| C 内容审校 | 固定总部与本伙伴范围；检索和读取 | 检查候选文件、来源、品牌、预算和承诺；阻断问题不能发布 |

五个场景 Skill：课程课件、活动策划、本地推广、合作提案、常见问答。四个格式 Skill：Word、PPT、Excel、Markdown。每个 `SKILL.md` 都包含触发描述、输入检查、结构、依据规则和失败处理，位于 `skills/boyan/`。

指南遵循项目 Skill 文件格式，但运行方式为受限指南加载器 `internal/boyanskills`。开户脚本登记目录包、固定版本和摘要，B 通过 `boyan_document` 的 `read_skill` 读取。原生 `skills_selection_mode` 为 `none`，没有宣称安装通用代码沙箱或生成原生 Skill 快照。缺失文件、安装登记或摘要不一致都会拒绝执行。

本机当前使用既有 DeepSeek 对话模型及 BGE-M3 向量模型，不修改用户已设置的模型名称。没有另外配置重排序模型时，三名受管 Agent 显式启用 `boyan_hybrid_search_without_rerank`，使用已有向量／关键词融合、去重及 MMR 路径。服务端校验 ready 开户记录与绑定 Agent 身份后才允许；不能给任意自建 Agent 勾选一个布尔值就绕过要求。若指定重排模型而加载失败，仍返回错误，不静默降级。

## 确认、生成与审校

1. B 读取对应场景和格式指南，检索白名单资料，最多一次追问三项关键信息。
2. 工具保存结构化大纲、事实来源、文件类型及内容摘要。聊天中的文字“确认”不能伪造前端授权。
3. 用户在会话确认卡中核对大纲并确认。每个格式各自确认，修改同场景同格式的大纲使旧的待确认或已确认稿失效；历史已发布文件保留版本记录，最终验收只收集当前最新稿。
4. 固定渲染器生成候选文件。无任意代码执行、Shell、路径或外部 URL 输入。
5. C 使用固定提示词独立审校文件中实际提取的文本、公式及与文件摘要绑定的机械检查证据；模型错误、超时、结果无法解析或有阻断问题均不发布。
6. 通过后显示下载。生成和下载时再次校验用户、工作空间、会话、来源可用性及权限。

候选文件保存在数据库中，不放入通用沙箱产物目录。完整接口、状态机与限制见 [文件工作流](business/document-workflow.md)。审校模型不能提供数学意义上的“永不编造”保证；来源和权限由代码校验，内容准确性仍须用回归样例及人工审阅评估。

被阻断后，B 可以通过 `status` 取回本用户、本空间、本会话的完整草稿、预算和仍有权访问的来源，再提出新版本。每次恢复都检查指南安装、来源权限和内容摘要；新稿必须重新确认。历史草稿读取遵循当前 Agent 的知识库和共享权限，不受本轮 `@mention` 检索过滤约束；重新提交来源时仍检查本轮允许的检索范围。

C 若返回“没有阻断意见但不通过”的矛盾结论，系统只向同一审校模型澄清一次，保留原证据和响应。必须由 C 给出一致结论；仍矛盾、超时或无法解析则失败，不由后端把不通过改成通过。输入长度和表格数量也在提交大纲时按渲染器同一限额检查，避免用户确认后才发现超限。

## 本机启用

本机 `.env` 需要 `BOYAN_DOCUMENT_RENDERER_TOKEN`，使用至少 32 字符随机值；应用与渲染器共用该内部鉴权令牌，不输出到日志。不更改现有 DeepSeek 密钥。

```bash
bash scripts/build-presets-images.sh
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml -f docker-compose.presets.yml config -q
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml -f docker-compose.presets.yml up -d --no-deps document-renderer app frontend
```

渲染器没有宿主机端口，只加入 internal 网络；只读根文件系统、非 root、去除 capabilities、限制内存/CPU/PID，临时文件在 tmpfs。该网络上应用和渲染器仍能互通，不等同于通用任意代码沙箱的完全断网环境。

应用新增迁移 111、112，存储幂等开户登记与文件工作流。部署前保留数据库备份。生产环境继续执行原运行手册的备份、显式迁移和固定 digest 流程，不直接套用本机自动迁移配置。

## 一键开户

先通过现有管理员开户或邀请流程准备伙伴账号，再编辑无密钥的 manifest。每个伙伴使用稳定 `partner_key`，不能把同一个 key 重用于另一家公司。

```bash
bash scripts/onboard-partner.sh --manifest path/to/partner.json
bash scripts/onboard-partner.sh --manifest path/to/partner.json --apply --base-url http://127.0.0.1:8080 --env-file path/to/operator.env --report path/to/report.json
```

第一条只给计划；第二条执行。凭据字段为 `ONBOARD_ADMIN_EMAIL`、`ONBOARD_ADMIN_PASSWORD`，或短期 `ONBOARD_ADMIN_TOKEN`。操作者必须是系统管理员且实际拥有总部空间 Owner 权限。详细清单、角色与失败重试见 [开户说明](partner-onboarding.md)。

总部与每一个伙伴各建一个双成员组织，仅共享指定知识库为只读；不会把所有伙伴放进同一个可枚举名称的组织。伙伴私有库归指定 Contributor 管理。每个伙伴独立生成 A/B/C Agent，不共用一个总部 Agent 执行伙伴数据任务。

伙伴资料上传到开户生成的私有知识库。以后新建其他知识库不会自动扩展 Agent 权限；需由管理员更新授权配置和开通校验，不能以“同一空间”代替明确白名单。

## 验证入口

```bash
python scripts/check-presets.py
python -m unittest discover -s scripts -p test_onboard_partner.py
python -m unittest discover -s scripts -p test_acceptance_presets.py
python scripts/acceptance-presets.py --fixtures path/to/fixtures.json --check-only
python scripts/acceptance-presets.py --fixtures path/to/fixtures.json --env-file path/to/fixtures.env --mode all
```

Windows 已安装 Microsoft Office 时，可对实际下载报告进行原生验证。先保存并关闭 Word、Excel、PowerPoint；脚本发现已有进程会停止，避免影响正在编辑的文件。使用 PowerShell 7，只读打开产物、禁用宏、不保存源文件，将预览另存到指定目录。

```powershell
pwsh -NoProfile -File scripts/verify-presets-office.ps1 -Report path/to/report.json -OutputDirectory temp/native-office
```

该脚本校验下载摘要、实际页数、PPT 文本框溢出与 Excel 原生重算，输出 PNG/PDF 和 `native-office.json`。预算表逐项在内存中修改数量和单价，核对行金额与总额同步变化，恢复输入后关闭且不保存原文件。仍需逐页查看预览，核对教学时长、表格可读性和商务用语。

验收使用独立非管理员伙伴身份，由脚本为合成任务执行模拟用户确认。检查大纲确认门槛、跨空间 API 拒绝、提示词注入、来源变化、两次开户资源 ID 一致、真实生成文件结构与公式。退出码 2 表示自动项通过但仍有人工项，不能当成全量人工验收通过。

需要人工验证：业务结论是否准确、无依据问题是否充分拒答、教学难度是否适合对象、商务表达是否越过总部授权、PPT 逐页排版和表格可读性。操作：用普通伙伴账号执行 `presets/boyan/v1/tests/behavior-cases.json` 的正常／缺失／违规样例，核对引用原文，下载并在 Office 中逐页审阅；对问题记录会话、文件版本和复现输入。

## 本机验收记录

2026-10-09，普通 Contributor 验收伙伴 A、B 的空间分别为 `10003`、`10004`。各自安装三名 Agent、九个指南包，拥有独立私有库和总部 viewer 分享。对两者再次执行同一开通命令，报告及资源 ID 完全相同。开户前备份位于本机忽略目录 `temp/presets-acceptance/before-presets.dump`，已验证归档目录可读。

| 验证 | 结果与证据 |
| --- | --- |
| 配置与脚本 | 静态检查 177 项通过；开户脚本 14 项、验收脚本 16 项离线测试通过；Go 文件工作流全包、开户相关测试及工具编译通过。这些与真实对话、Office 打开分别记录 |
| 模板兼容性 | 十二份模板；九份 Office 模板实际打开。十八张 PPT、六页 Word 逐页检查；另用 LibreOffice 渲染三份 Word |
| 渲染器独立验证 | 15 项自动测试通过，含品牌去重、长内容分段、数字与 ASCII 词完整、平衡换行失败回退、真实 PPT 重开和 PDF 导出；此前独立生成的四份 PPT、三份 Word、三份 Excel 在 Office 打开，预算及舍入样例验证通过 |
| 真实权限 API | `temp/presets-acceptance/access-audit.json`：74 通过、0 失败。包括自身 canary 正例、HQ 写入/分享拒绝、跨伙伴文档/Agent/组织与成员名隔离、伪造租户拒绝 |
| 真实 Agent 对话 | 首轮 24 个用例的 146 个自动断言通过，但语义审阅仅 15 通过、9 失败。修复运行约束后对失败项重测；`temp/presets-acceptance/semantic-review-final.json` 汇总每项最新样本为 24 通过，保留旧失败和修订历史。这不是首次全部通过，也不能替代业务负责人审阅 |
| 完整输入回归 | `temp/presets-acceptance/behavior-complete-final/report.json`：14 个自动断言通过。学校提供场地设备没有被改写为免费；活动日程含休息合计 480 分钟；提案不因本次资料缺失而否认公司业务，180 分钟安排包含休息 |
| 提示词注入与检索隔离 | `temp/presets-acceptance/isolation-live-01/report.json`：22 自动断言通过；双向自身 canary 可检索，对方 canary 未泄漏 |
| 审校阻断 | 初批候选文件因来源缺失、无依据内容或预算表问题被 C 阻断，未提供下载；保留原任务记录，不把阻断文件记为成功产物 |
| 最终真实产物 | `temp/presets-acceptance/delivery-final/report.json`：18 个收集与隔离断言通过，集中保存七份当前已发布文件；使用 `--download-only`，没有重新调用模型、确认或生成 |
| 原生 Office | `temp/presets-acceptance/delivery-final/native-office.json`：六份 Office 文件实际打开，35 张 PPT、10 页 Word 已逐页查看；PPT 无文本框溢出，两份课件备注各合计 45 分钟；MD 全文及 UTF-8 已检查。每项证据与最终文件 SHA-256 一致 |
| Excel 动态公式 | 两份预算各含三条金额公式及一条合计公式，当前合计均为 1480 元；在 Microsoft Excel 中逐项修改数量和单价，共 12 次联动检查通过，恢复后关闭且不保存原文件。输入仅为合成预算假设，非总部报价 |
| 新任务卡浏览器检查 | 点击“下载已审校文件”下载 A 最终 Excel，与验收文件字节及摘要一致；截图和记录位于 `temp/presets-acceptance/ui/`。390px 视口下组件宽/滚动宽同为 350px；聊天页原有 400px 最小宽度仍有外层横向溢出，不代表整页移动适配已完成 |
| 本机运行 | 应用、前端、文档渲染器、docreader、Ollama、PostgreSQL、Redis 七个容器均健康；未改云端、生产、staging 或 restore 配置 |

最终文件保存在本机忽略目录；下表摘要为 SHA-256 前十二位，完整摘要和任务 ID 见集中报告。旧版失败、被阻断或后续发现问题的文件均保留为历史，不混入最终交付。

| 伙伴 | 文件 | 验证 | 摘要前缀 |
| --- | --- | --- | --- |
| A | [教学课件 PPT](../temp/presets-acceptance/delivery-final/artifacts/a-artifact-courseware.pptx) | 17 页；讲师备注合计 45 分钟 | `195a095629f6` |
| A | [合作提案 Word](../temp/presets-acceptance/delivery-final/artifacts/a-artifact-cooperation-proposal.docx) | 5 页；商务模板 | `b531ba0931b2` |
| A | [活动预算 Excel](../temp/presets-acceptance/delivery-final/artifacts/a-artifact-event-plan.xlsx) | 1 张工作表；公式及六次输入联动检查 | `ba97e4b6f5eb` |
| A | [教学 FAQ Markdown](../temp/presets-acceptance/delivery-final/artifacts/a-artifact-faq-guide.md) | 内部学习用途；未知项、品牌及来源已核对 | `6e9e091eafe1` |
| B | [教学课件 PPT](../temp/presets-acceptance/delivery-final/artifacts/b-artifact-courseware.pptx) | 18 页；讲师备注与议程均合计 45 分钟 | `50386994c099` |
| B | [合作提案 Word](../temp/presets-acceptance/delivery-final/artifacts/b-artifact-cooperation-proposal.docx) | 5 页；商务模板 | `bd5bb2161aff` |
| B | [活动预算 Excel](../temp/presets-acceptance/delivery-final/artifacts/b-artifact-event-plan.xlsx) | 1 张工作表；公式及六次输入联动检查 | `935eaeaf0c95` |

本机重新收集及原生检查：

```bash
python scripts/acceptance-presets.py --fixtures temp/presets-acceptance/fixtures.json --env-file temp/presets-acceptance/.env --mode artifacts --resume temp/presets-acceptance/resume-final-merged --download-only --output temp/presets-acceptance/delivery-recheck
```

```powershell
pwsh -NoProfile -File scripts/verify-presets-office.ps1 -Report temp/presets-acceptance/delivery-recheck/report.json -OutputDirectory temp/presets-acceptance/native-recheck
```

`temp/` 不进入 Git，克隆仓库不会包含测试账号凭据或业务产物；其他环境须先用独立账号完成开户并准备 fixture。验收脚本仍保留“需要人工验证”结果，不自动代替业务负责人签字。

需要人工验证：正式授课的难度与时长是否适合真实学员、知识库事实的业务解释是否准确、商务提案是否有总部授权。请业务负责人对照七份最终文件及其来源逐项签字后对外使用。Excel 原生打印会有来源续页，实际打印前还需按纸张与用途调整分页。

C 曾误判真实公式，也漏掉过时长、重复预算表述及备注页号的问题；本次通过机械校验、真实文件复核和修订处理了最终样本中的这些问题，不能据此推断任意未来生成均无需审阅。模型超时、格式解析错误或渲染失败继续拒绝发布；遇到 `failure_code=render_failed`，检查渲染器健康、模板摘要和页面容量，修订或重新提交后必须重新确认。界面当前可能将渲染失败统一显示为服务不可用，应结合任务记录定位。

## 升级重放

保留 `branding/templates/`、`presets/boyan/`、`skills/boyan/` 与渲染器独立目录。核心接入点登记于 [CUSTOMIZATIONS.md](CUSTOMIZATIONS.md)。新上游先在隔离数据库副本上迁移、跑 Go/Python 测试，再重建三个镜像并执行真实伙伴验收。核对工具默认行为、KB 权限、路由参数名、Skill schema、模型 ID 和引用分块结构后才能发布。

回滚镜像前确认 schema 向后兼容；不要删除已有开户和文件记录来“重新开始”。模板版本、Agent 提示词和 Skill 摘要要作为一组回滚。云端升级步骤继续参照 [05-upgrade-runbook.md](05-upgrade-runbook.md)。
