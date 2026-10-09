# 合作伙伴预设与文件模板

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
| B 方案策划 | 总部共享库与当前伙伴私有库；检索、读取、`boyan_document` | 检查输入、形成大纲、等待确认、按模板生成 |
| C 内容审校 | 固定总部与本伙伴范围；检索和读取 | 检查候选文件、来源、品牌、预算和承诺；阻断问题不能发布 |

五个场景 Skill：课程课件、活动策划、本地推广、合作提案、常见问答。四个格式 Skill：Word、PPT、Excel、Markdown。每个 `SKILL.md` 都包含触发描述、输入检查、结构、依据规则和失败处理，位于 `skills/boyan/`。

指南遵循项目 Skill 文件格式，但运行方式为受限指南加载器 `internal/boyanskills`。开户脚本登记目录包、固定版本和摘要，B 通过 `boyan_document` 的 `read_skill` 读取。原生 `skills_selection_mode` 为 `none`，没有宣称安装通用代码沙箱或生成原生 Skill 快照。缺失文件、安装登记或摘要不一致都会拒绝执行。

本机当前使用既有 DeepSeek 对话模型及 BGE-M3 向量模型，不修改用户已设置的模型名称。没有另外配置重排序模型时，三名受管 Agent 显式启用 `boyan_hybrid_search_without_rerank`，使用已有向量／关键词融合、去重及 MMR 路径。服务端校验 ready 开户记录与绑定 Agent 身份后才允许；不能给任意自建 Agent 勾选一个布尔值就绕过要求。若指定重排模型而加载失败，仍返回错误，不静默降级。

## 确认、生成与审校

1. B 读取对应场景和格式指南，检索白名单资料，最多一次追问三项关键信息。
2. 工具保存结构化大纲、事实来源、文件类型及内容摘要。聊天中的文字“确认”不能伪造前端授权。
3. 用户在会话确认卡中核对大纲并确认。每个格式各自确认，修改同场景同格式的大纲使旧稿失效。
4. 固定渲染器生成候选文件。无任意代码执行、Shell、路径或外部 URL 输入。
5. C 使用固定提示词独立审校文件中实际提取的文本；模型错误、超时、结果无法解析或有阻断问题均不发布。
6. 通过后显示下载。生成和下载时再次校验用户、工作空间、会话、来源可用性及权限。

候选文件保存在数据库中，不放入通用沙箱产物目录。完整接口、状态机与限制见 [文件工作流](business/document-workflow.md)。审校模型不能提供数学意义上的“永不编造”保证；来源和权限由代码校验，内容准确性仍须用回归样例及人工审阅评估。

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

## 验证入口

```bash
python scripts/check-presets.py
python -m unittest discover -s scripts -p test_onboard_partner.py
python -m unittest discover -s scripts -p test_acceptance_presets.py
python scripts/acceptance-presets.py --fixtures path/to/fixtures.json --check-only
python scripts/acceptance-presets.py --fixtures path/to/fixtures.json --env-file path/to/fixtures.env --mode all
```

验收使用独立非管理员伙伴身份。检查大纲确认门槛、跨空间 API 拒绝、提示词注入、来源变化、两次开户资源 ID 一致、真实生成文件结构与公式。退出码 2 表示自动项通过但仍有人工项，不能当成全量人工验收通过。

需要人工验证：业务结论是否准确、无依据问题是否充分拒答、教学难度是否适合对象、商务表达是否越过总部授权、PPT 逐页排版和表格可读性。操作：用普通伙伴账号执行 `presets/boyan/v1/tests/behavior-cases.json` 的正常／缺失／违规样例，核对引用原文，下载并在 Office 中逐页审阅；对问题记录会话、文件版本和复现输入。

## 本机验收记录

2026-10-09，普通 Contributor 验收伙伴 A、B 的空间分别为 `10003`、`10004`。各自安装三名 Agent、九个指南包，拥有独立私有库和总部 viewer 分享。对两者再次执行同一开通命令，报告及资源 ID 完全相同。开户前备份位于本机忽略目录 `temp/presets-acceptance/before-presets.dump`，已验证归档目录可读。

| 验证 | 结果与证据 |
| --- | --- |
| 模板兼容性 | 十二份模板；九份 Office 模板实际打开。十八张 PPT、六页 Word 逐页检查；另用 LibreOffice 渲染三份 Word |
| 渲染器独立验证 | 七项自动测试通过；生成后的四份 PPT（含长内容）、三份 Word、三份 Excel 在 Office 打开；预算 1480 元及舍入样例验证通过 |
| 真实权限 API | `temp/presets-acceptance/access-audit.json`：74 通过、0 失败。包括自身 canary 正例、HQ 写入/分享拒绝、跨伙伴文档/Agent/组织与成员名隔离、伪造租户拒绝 |
| 真实 Agent 对话 | `temp/presets-acceptance/behavior-live-03/report.json`：24 个用例、146 自动断言通过；业务语义仍需人工验证 |
| 提示词注入与检索隔离 | `temp/presets-acceptance/isolation-live-01/report.json`：22 自动断言通过；双向自身 canary 可检索，对方 canary 未泄漏 |
| 审校阻断 | 初批候选文件因来源缺失、无依据内容或预算表问题被 C 阻断，未提供下载；保留原任务记录，不把阻断文件记为成功产物 |
| 新任务卡浏览器检查 | 桌面可查看审校结果；390px 视口下使用纵向问题列表，组件宽/滚动宽同为 350px。聊天页原有 400px 最小宽度仍有外层横向溢出，不代表整页移动适配已完成 |

模板和独立渲染通过不能替代真实业务文件生成。真实产物的最终下载、审校及 Office 检查结果应单独登记；自动断言通过也不能替代业务负责人签字。

## 升级重放

保留 `branding/templates/`、`presets/boyan/`、`skills/boyan/` 与渲染器独立目录。核心接入点登记于 [CUSTOMIZATIONS.md](CUSTOMIZATIONS.md)。新上游先在隔离数据库副本上迁移、跑 Go/Python 测试，再重建三个镜像并执行真实伙伴验收。核对工具默认行为、KB 权限、路由参数名、Skill schema、模型 ID 和引用分块结构后才能发布。

回滚镜像前确认 schema 向后兼容；不要删除已有开户和文件记录来“重新开始”。模板版本、Agent 提示词和 Skill 摘要要作为一组回滚。云端升级步骤继续参照 [05-upgrade-runbook.md](05-upgrade-runbook.md)。
