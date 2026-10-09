# 正式文件工作流

落地方案官使用 `boyan_document` 工具读取已安装的受限业务 Skill，提交方案并生成文件。该路径不启用原生沙箱，不提供 Shell、任意 Python、网络抓取或运行用户上传脚本的能力。它不代表生产环境已经开启通用 Skill 执行。

`read_skill` 只读取安装索引中的九个固定包；`propose` 提交五类场景之一、格式、必填需求、大纲、结构化内容和已检索的分块 ID；`status` 查询任务；`generate` 只处理当前用户已明确确认的任务。模型没有 `confirm` 操作。

任务绑定当前用户、工作空间和会话。确认哈希包含大纲、结构化内容、需求、格式、模板版本、真实来源及 Skill 摘要。同一会话、场景和格式提交新版，会将旧的待确认或已确认任务标记为 `superseded`，新任务必须重新确认。其他格式的任务不因此失效。

工具先校验分块位于本轮 Agent 的 `SearchTargets`。服务端读取真实分块内容，校验其属于开通记录中的总部或伙伴知识库、当前落地方案官所选知识库及当前共享权限。确认、生成、发布前和下载时再次检查权限、来源内容摘要和已安装 Skill 摘要；模型提交的来源正文不能替代数据库中的原文。

状态依次为 `awaiting_confirmation`、`approved`、`generating`、`reviewing`、`published`。审校发现阻断问题进入 `blocked`；渲染、审校、解析或发布失败进入 `failed`。中断超过五分钟的执行任务在读取状态时失效，不会自动发布。

固定渲染器位于管理员配置的 `BOYAN_DOCUMENT_RENDERER_URL`，使用 `BOYAN_DOCUMENT_RENDERER_TOKEN` 鉴权。它接收结构化 JSON，返回文件字节、摘要、文件类型和机械检查结果。应用禁止跟随重定向，再次核对字节摘要、实际 OOXML 类型、宏和外部关系。渲染器不接收模型密钥或任意命令。

候选字节保存在 `boyan_document_jobs.artifact` 中，不进入通用会话附件或公开对象 URL。应用从真实候选文件提取文本，将已确认方案和授权原文交给固定内容审校官指令审查。审校模型来自开通记录绑定的审校官配置；模型不能替换审校指令或身份。审校 JSON 必须可严格解析，包含明确布尔结果和完整问题列表；任何阻断项都禁止发布。错误、超时、缺字段和拒绝结果都不能变为通过。内容审校不等于对事实准确性的数学保证，正式对外承诺仍需总部按业务要求复核。

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
