# 博研文化预设 boyan/v1

`manifest.json` 是开通脚本的唯一预设入口，路径相对本目录。三个 Agent 的 JSON 对应项目 `CreateAgentRequest`；`prompt_files` 按顺序合并为 `config.system_prompt`。`@hq` 展开为明确授权的总部知识库 ID，`@partner` 展开为当前伙伴私有知识库 ID，`@chat_model` 和 `@rerank_model` 由开通配置绑定。未完成绑定的 JSON 不可直接提交给业务 API。

文件品牌为博研文化，平台品牌为 BoyanKB。教学场景使用 `teaching`，商务提案、本地推广和 FAQ 使用 `business`，活动执行使用 `operations`；模板版本为 `boyan-v1`，文件位于 `branding/templates/`。模板内容只用于结构与视觉，不能作为业务事实来源。

九个包遵循项目 `SKILL.md` 的 YAML frontmatter 与 Markdown 指令格式。安装名使用 `boyan-` 前缀，版本 `boyan/v1`，manifest 中的 SHA-256 对应原始 `SKILL.md` 字节。修改包后必须更新摘要并显式升级伙伴预设，旧安装摘要不匹配时拒绝读取。

执行路径为受控指南安装，不使用原生 Sandbox 快照。目录注册不代表已经安装；平台管理员验证固定包、目录元数据和摘要后，将租户安装版本及启用状态记录在 `partner_provisioning_jobs.state.restricted_skills`。`internal/boyanskills` 只读取固定白名单指南，要求开通状态 ready、该租户已启用、版本与摘要一致；不执行包内脚本，不读取用户上传的任意 Skill 正文。原生 `tenant_skills` 的 ready 状态与本路径不同，不能互相冒充。

落地方案官使用 `boyan_document` 的 `read_skill` 加载已安装指南，`propose` 提交大纲，`status` 检查状态，`generate` 请求固定渲染。模型没有确认操作；用户确认由服务端 API 完成并绑定当前大纲 hash。自动审校使用固定共同规则与 reviewer 提示词，调用方附加严格 JSON 结果约束。存在阻断、失败或超时时不发布文件。

三个 Agent 的原生 `skills_selection_mode` 均为 none，`selected_skills` 为空，不启用 Sandbox Skill 路线。九个受控指南由 manifest 和租户安装索引共同限定，工作流在生成前检查对应场景与格式。此预设不需要通用 Shell、网页搜索、外部 MCP 或长期记忆。业务导师和内容审校官只保留知识库检索工具。

验证包结构、摘要、权限绑定和行为测试覆盖：

```sh
python scripts/check-presets.py
go test ./internal/boyanskills
```

`tests/behavior-cases.json` 包含三个 Agent 与五个场景各三条独立行为用例。必须在模拟伙伴真实会话中运行，并保留检索、工具、确认与审校事件证据。静态检查只验证用例完整性，不表示模型行为已经通过。其他伙伴对照资料只使用测试空间中的测试标记，不获取真实伙伴私有内容。

正式文件还需检查真实下载、ZIP/XML 完整性、文本提取、渲染、品牌与引用、Excel 公式和计算结果。Microsoft Office 中的字体、分页、编辑与幻灯片放映效果需要人工验证。

真实验收脚本只接受本机 HTTP 入口和两个独立模拟伙伴。使用 `tests/acceptance-fixture.example.json` 的结构填写空间、Agent 与测试标记；凭据字段只填写环境变量名，值保存在本机忽略的 `.env` 文件。开通报告可通过 `onboarding_report` 引用，脚本读取其中的租户、Agent 和私有库 ID。必须使用两个不同的非管理员账号，不能用总部 Owner 代替隔离验证。

```sh
python scripts/acceptance-presets.py --fixtures temp/presets-acceptance/fixtures.json --check-only
python scripts/acceptance-presets.py --fixtures temp/presets-acceptance/fixtures.json --env-file temp/presets-acceptance/.env
```

脚本创建模拟伙伴会话并调用已配置模型，会产生正常模型用量。`--mode behavior` 运行 24 条行为用例，`--mode artifacts` 为两个伙伴分别验证 PPT、Word 和 Excel 的完整流程，`--mode isolation` 验证私有库、伪造租户请求和提示词注入的正反对照。`--case` 可重复指定单条行为用例。默认输出到新的 `temp/presets-acceptance/run-*` 目录，不覆盖旧报告，不删除测试空间或业务资料。

每份报告包括服务端工具事件、独立会话、大纲确认状态、审校结果、文件摘要和自动检查。语义与视觉检查列为“需要人工验证”，不把关键词匹配、工具返回成功或 ZIP 可解压当作所有业务规则通过。退出码 0 表示全部自动与人工待办均已满足，1 表示自动检查失败，2 表示自动检查通过但仍有人工验证项。脚本不能替代人工签字，存在人工项时不会返回 0。

离线测试不会向业务 API 发起请求：

```sh
python -m unittest discover -s scripts -p test_acceptance_presets.py -v
```
