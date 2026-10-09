# 正式文件工作流

落地方案官使用 `boyan_document` 工具读取已安装的受限业务 Skill，提交方案并生成文件。该路径不启用原生沙箱，不提供 Shell、任意 Python、网络抓取或运行用户上传脚本的能力。它不代表生产环境已经开启通用 Skill 执行。

`read_skill` 只读取安装索引中的九个固定包；`propose` 提交五类场景之一、格式、必填需求、大纲、结构化内容和已检索的分块 ID；`status` 查询任务；`generate` 只处理当前用户已明确确认的任务。模型没有 `confirm` 操作。

任务绑定当前用户、工作空间和会话。确认哈希包含大纲、结构化内容、需求、格式、模板版本、真实来源及 Skill 摘要。同一会话、场景和格式提交新版，会将旧的待确认或已确认任务标记为 `superseded`，新任务必须重新确认。其他格式的任务不因此失效。

工具先校验分块位于本轮 Agent 的 `SearchTargets`。服务端读取真实分块内容，校验其属于开通记录中的总部或伙伴知识库、当前落地方案官所选知识库及当前共享权限。确认、生成、发布前和下载时再次检查权限、来源内容摘要和已安装 Skill 摘要；模型提交的来源正文不能替代数据库中的原文。

状态依次为 `awaiting_confirmation`、`approved`、`generating`、`reviewing`、`published`。审校发现阻断问题进入 `blocked`；渲染、审校、解析或发布失败进入 `failed`。中断超过五分钟的执行任务在读取状态时失效，不会自动发布。

固定渲染器位于管理员配置的 `BOYAN_DOCUMENT_RENDERER_URL`，使用 `BOYAN_DOCUMENT_RENDERER_TOKEN` 鉴权。它接收结构化 JSON，返回文件字节、摘要、文件类型和机械检查结果。应用禁止跟随重定向，再次核对字节摘要、实际 OOXML 类型、宏和外部关系。渲染器不接收模型密钥或任意命令。

应用按格式核验渲染器检查项白名单，拒绝缺项、重复项、未知项及额外字段，并将结果绑定候选文件 SHA256，以独立的 `renderer_validation` 字段交给 C。正文、需求或来源中的同名文字不能构成检查证据。XLSX 证据包括当前文件经 LibreOffice 重算、公式错误扫描；含结构化预算时还包括逐项金额与合计和独立计算结果一致。该证据不代表执行过输入扰动、逐一验证全部未来输入或视觉排版验收，也不能自动将 C 的拒绝改为通过。

Excel 的数量和单价以可编辑数值保存；`ROUND(B44*C44,2)` 和 `SUM(D44:D46)` 是引用输入或金额单元格的真实公式，缓存值只是当前重算结果。C 不应将这种结构误判为静态正文或要求改称一次性计算。余额、利用率和超支提醒仅在用户明确要求或正文声称提供时验收，预算上限输入本身不隐含这些附加要求。静态正文冒充动态汇总及未联动的重复预算输入仍属于阻断问题。

无模型测试可运行 `go test -ldflags='-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn' ./internal/businessworkflow -run 'Test(RendererEvidence|ReviewEvidence)' -v`，覆盖真实公式与缓存传递、检查项缺失或伪造、正文冒充证据、跨文件摘要及 C 拒绝不被覆盖。真实 C 能否遵循审校规则仍需部署后重新生成并验收，单元测试不证明模型一定不会误判。

候选字节保存在 `boyan_document_jobs.artifact` 中，不进入通用会话附件或公开对象 URL。应用从真实候选文件提取文本，将已确认方案和授权原文交给固定内容审校官指令审查。审校模型来自开通记录绑定的审校官配置；模型不能替换审校指令或身份。审校 JSON 必须可严格解析，包含明确布尔结果和完整问题列表；任何阻断项都禁止发布。错误、超时、缺字段和拒绝结果都不能变为通过。内容审校不等于对事实准确性的数学保证，正式对外承诺仍需总部按业务要求复核。

审校抽取保留 XLSX 工作表、行列地址、实际公式与缓存值，并解析共享字符串；PPT 按实际播放顺序分开正文和关联备注，备注中的真实分块 ID 不丢失；DOCX 仅读取正文及页眉页脚的可见文本，不读取样式和设置中的文本。超过文本上限会失败，不静默截断。服务器提供实际 UTC 日期及时间；已配置的固定品牌、负责人规则可作为依据。无依据的总部事实、承诺和实质错误继续阻断，有依据的同义表达、纯排版建议及已标明的用户预算假设不因此被当成虚构总部事实。

预算只通过 `budget.items` 的 `label`、`quantity`、`unit_price` 输入，由固定渲染器追加可计算预算表。XLSX 正文表格中的 `=` 或 `'=...` 公式文本会在提出大纲时拒绝，并提示使用结构化预算；不执行模型自定义公式。真实来源标题和 ID 由服务器追加，模型不应手写来源附录。`BOYAN_RENDERED_FIXTURES_DIR` 指向本机渲染器生成目录时，`go test ./internal/businessworkflow -run TestGeneratedOfficeArtifactText -v` 可对已有 DOCX/PPTX/XLSX 实文件验证文本与公式缓存抽取；此测试不调用模型。

提案在创建确认任务前校验渲染器的静态输入限额，错误返回需要缩短或修正的字段。文本长度按 Unicode 字符计数，中文和 emoji 均按码点计；整体 JSON 与大纲另有字节上限。模板布局容量和文件生成结果仍需运行时检查，不能认为所有生成错误都能提前排除。

| 输入 | 上限或范围 |
| --- | --- |
| 文档标题 | PPTX 为 36 字符；其他格式为 100 字符，必填 |
| 章节 | 1–40 节 |
| 章节标题 | PPTX 为 40 字符；其他格式为 100 字符，必填 |
| 每节正文 / 备注 | 12000 / 5000 字符 |
| 每节项目符号 | 最多 40 条，每条 1–2000 字符且不可为空白 |
| 每节表格 | 最多 4 个，每表 1–8 列、最多 150 行；空行集合必须为 `[]` |
| 表头 / 单元格 | 100 / 1000 字符；表头不可为空白，行列数必须一致 |
| 来源 | 1–30 条；分块 ID 最多 100 字符，真实标题最多 300 字符，原文最多 24000 字符 |
| 预算 | 1–150 项，项目名称 1–150 字符；数量和单价均须为 0–100000000 的有限数值 |
| 大纲 / 提案 JSON / 含验证来源的内部载荷 | 10–30000 UTF-8 字节 / 最多 256 KiB / 最多 512 KiB |

渲染文本禁止 U+0000–U+001F 控制字符（正常制表、换行和回车除外）及 `{{...}}` 模板占位符。以上限制在 `internal/businessworkflow/render_limits.go` 与固定渲染器入口一致；总载荷限额仍可能要求减少来源或内容。边界测试覆盖四格式、中英文与 emoji、数组数量、预算数值、来源和确认任务不落库，运行 `go test ./internal/businessworkflow -run 'Test(RendererTextLimitsUseUnicodeCharacters|RendererCollectionLimitsFailBeforeConfirmation|RendererTextAndBudgetInvalidInputs|SourceRendererTextLimitsFailBeforeConfirmation)$'`。

PPT 一节拆成多个正文页和表格页时，讲师备注按整节分组标明当前第 `i/n` 页，并注明备注中的预计时长是整节总时长，不按页重复累加。原始分钟数和每页来源保持原样；单页不增加分组说明。预算续页独立分组，不添加不存在的讲师时长。

Markdown 与 Excel 仅在标题尚未以“博研文化”开头时追加品牌前缀，保留原始标题文字。商务 Markdown 不输出模板中的固定“合作方案”类别，以免将 FAQ 等资料错误分类；教学和运营类别、正文及来源保持原样。此处理不改模板文件或摘要，也不改 Word/PPT 标题。渲染器的 `python -m unittest test_render -v` 已通过 15 项测试，包含三场景 Markdown、带或不带品牌的标题、实际 Excel 生成和 LibreOffice 重算；已有下载文件需重新提交确认并生成才能应用此修复。

PPT 正文及来源标题按句读优先分段，避开数字、ASCII 单词内部和极短尾段；文本框换行使用同样的安全边界。每页最多三个要点，四点分为两页各两点，七点分为三、二、二点，避免末页只有一个碎片。分段不会补写、删除或校改原文事实；无标点长中文仍可能在字间换段。单个不可拆词超出版面容量会明确失败，不截断文字。测试覆盖真实“合计45分钟”问题句、教学质控红线、无标点长中文、英文数字与来源标题，以及来源和讲师备注保留、文本框尺寸、LibreOffice 打开导出 PDF；实物位于 `temp/brand-template-build/generated/teaching-semantic-pages.pptx` 和同名 PDF。

短尾行的平衡排版仅在可容纳时采用；若平衡导致额外行数超限，会回退到同字号下原本可容纳的安全换行，不错误拒绝整份文件。真实 B 请求已生成 18 页并通过 LibreOffice 打开导出，原失败句保持完整两行；验证文件为 `temp/brand-template-build/generated/b-rebalanced-real.pptx` 和同名 PDF。

PPT 的文本框可容纳行数、表头和单行表格高度由实际模板尺寸及字体计算，整份演示文稿最多 80 页；这些在生成时检查，失败不会发布。可减少长单元格内容、表格行数或拆分为多个任务，重新提交并确认。模板摘要、20 MiB 文件上限、Office 包重开、LibreOffice 重算和公式结果也保留为运行时门禁。未新增确认前布局预检 API，也不以静态校验通过承诺文件必定生成成功。

主站对话下方显示大纲、确认按钮、审校问题表及已通过文件的下载按钮。需要修改时继续对话产生新版。C 的自动审校不修改原件。当前一次任务生成一种格式；同一方案需要多种文件时，分别提交并确认对应任务。

接口要求正常登录的 Web 用户及当前会话所有权。API Key、嵌入访客和其他用户不能代为确认。

| 方法与路径 | 请求或结果 |
| --- | --- |
| `GET /api/v1/business-workflow/readiness` | 工作空间管理员校验固定九个包与渲染器模板、格式能力，不依赖伙伴任务已完成 |
| `GET /api/v1/sessions/{session_id}/documents` | 最近二十个任务；不返回候选字节、内部载荷或凭据 |
| `GET /api/v1/sessions/{session_id}/documents/{job_id}` | 当前任务与审校问题 |
| `POST /api/v1/sessions/{session_id}/documents/{job_id}/confirm` | `{"outline_hash":"当前哈希"}` |
| `POST /api/v1/sessions/{session_id}/documents/{job_id}/generate` | 同上，最长执行时间为一百八十秒 |
| `GET /api/v1/sessions/{session_id}/documents/{job_id}/download` | 仅当前授权用户获取审校通过的字节；响应禁止缓存 |

部署必须执行 `000112_boyan_document_jobs` 迁移，并完成伙伴开通记录、固定包安装索引、Agent 绑定、内部渲染器和模型配置。不能以源代码已存在推定服务已部署或文件已生成。

三名受管 Agent 可显式设置 `boyan_hybrid_search_without_rerank: true`。仅当重排模型 ID 为空，服务端核实当前空间的开通记录为 `ready`、Agent 属于绑定的导师/方案官/审校官且创建者仍为记录中的 Owner，才允许使用原有混合检索结果，不调用重排模型。向量与关键词召回、融合排序、去重、MMR 和权限范围保持原路径。普通 Agent、未完成开通、跨空间共享 Agent 不享有此例外；已填写的重排模型加载失败仍报错。

前端仅将该显式配置视为允许提交服务器验证，并提示“未配置重排，受管预设由服务端校验”，不据此证明 Agent 已受管。该字段不提供普通 UI 开关。更新已有开通模板会改变 `spec_hash`，不能用普通重跑绕过开通版本锁；应由管理员执行受控升级。验证命令为 `go test -ldflags='-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn' ./internal/application/service -run 'TestBoyan(HybridSearchRequiresReadyBoundAgent|OptionalRerankPreservesModelFailures)$'` 及 `cd frontend && npm test -- src/utils/agent-readiness.test.mjs`。检索质量仍需真实业务问题验收，未部署前不视为已验证。

验证命令：

```text
go test -ldflags='-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn' ./internal/businessworkflow ./internal/agent/tools ./internal/modelcontext
```

单元测试覆盖审批绕过、错误哈希、新版失效、跨用户/空间/会话、机器身份确认、候选文件隔离、审校失败、来源变化和无效文件。真实验收还需通过对话工具提交方案，确认前验证生成和下载被拒，确认后生成 DOCX/PPTX/XLSX/MD，检查自动审校与跨空间下载拒绝，并在 Office 中打开检查中文、公式和版式。模拟测试不能替代这些真实链路结果。
