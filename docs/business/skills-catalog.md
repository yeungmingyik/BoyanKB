# 业务技能目录接口

业务技能使用固定九项只读指南，由合作伙伴开通记录登记版本和内容摘要。它们通过受管的“落地方案官”及 `boyan_document` 使用，不是原生沙箱技能，不提供安装按钮，不执行指南中的任意代码。

总部工作空间未开通合作伙伴预设时，接口仍列出九项能力说明，但明确返回 `installed=false`、`available=false`、`status=not_installed`。不会据此新建智能体、安装沙箱或伪造可用状态。

## 身份与范围

两个接口均位于现有 JWT 认证路由下，要求当前工作空间 Viewer 或更高角色。业务层再次核实 Web 用户身份与调用者一致，并查询该用户在当前工作空间的有效成员记录。API Key、嵌入会话、即时通信主体、非成员、已暂停或已删除的成员均不可访问。

工作空间只取认证上下文，不接受查询参数选择其他空间。即使用户具有系统管理权限，也不能绕过本接口的成员核验。接口不返回其他工作空间、总部组织、创建人、安装任务或原生技能目录记录的标识。

所有响应设置 `Cache-Control: no-store`。查询参数返回 HTTP 400；无权访问返回 HTTP 403；数据库不可用等系统故障返回 HTTP 503，不能冒充“尚未开通”。

## 目录

`GET /api/v1/business-workflow/skills`

```json
{
  "success": true,
  "data": {
    "installed": true,
    "available": true,
    "status": "ready",
    "reason": "九项业务技能均已安装并通过内容校验。",
    "planner_agent_id": "当前工作空间的受管智能体ID",
    "skills": [
      {
        "name": "boyan-courseware",
        "display_name": "课程讲义",
        "description": "根据授权资料编写课程讲义，明确对象、目标、内容和教学安排。",
        "kind": "scene",
        "version": "boyan/v1",
        "installed": true,
        "available": true,
        "hash_verified": true,
        "sha256": "固定指南原始字节的64位十六进制摘要",
        "status": "ready",
        "reason": "已安装并通过内容校验，可由落地方案官读取使用。"
      }
    ]
  }
}
```

正式响应固定返回九条，示例仅展示第一条。

| 内部名称 | 中文显示名 | 分类 |
| --- | --- | --- |
| `boyan-courseware` | 课程讲义 | `scene` |
| `boyan-event-plan` | 活动方案 | `scene` |
| `boyan-local-promotion-plan` | 本地推广方案 | `scene` |
| `boyan-cooperation-proposal` | 合作提案 | `scene` |
| `boyan-faq-guide` | 常见问题指南 | `scene` |
| `boyan-docx` | 文字文档 | `format` |
| `boyan-pptx` | 演示文稿 | `format` |
| `boyan-xlsx` | 电子表格 | `format` |
| `boyan-md` | 轻量文本 | `format` |

`installed` 表示该项存在带目录标识的安装登记；全局值表示九项均已登记。它不等同于启用或内容校验通过。

`hash_verified` 要求服务端固定文件通过允许目录、manifest、版本、内容摘要校验，同时与当前工作空间安装索引的版本和摘要一致。`sha256` 仅在固定文件校验成功时返回。未开通空间可以看到固定包的公开摘要，但 `hash_verified` 仍为 `false`。

`available` 还要求开通任务状态为 `ready`、该技能已启用，并且绑定的“落地方案官”确实属于当前空间、由登记的开通 Owner 创建且不是内置智能体。无效或跨空间的智能体 ID 不返回。全局 `available` 表示九项均满足要求。

| 状态 | 含义 |
| --- | --- |
| `not_installed` | 无开通任务，或该技能缺少安装登记 |
| `not_ready` | 已有开通任务，但任务未完成 |
| `ready` | 本接口的安装、文件和绑定检查均通过 |
| `unavailable` | 已停用、版本或摘要不一致、固定文件不可用，或智能体绑定无效 |

`reason` 是可直接展示的中文说明。目录可用仅表示受限指南可读取，不代表模型、知识来源授权或文档渲染服务实时可用。实际生成仍须执行工作流自身的权限、来源、人类确认、渲染与审校检查。

## 指南详情

`GET /api/v1/business-workflow/skills/:name`

名称必须是表中的固定九项之一，且该项当前可用。响应为 `{"success":true,"data":{...目录条目,"content":"完整 SKILL.md 文本"}}`。

返回前再次通过固定指南读取器校验当前安装记录与文件，并核对返回内容的摘要。未知名称、路径穿越、未安装、停用或校验失败均不返回内容。接口不接收文件路径、空间 ID 或任意包名。

前端应以纯文本展示指南，不能执行 HTML 或指南中的命令。英文内部名称、工具名、格式标识和既有 `boyan/v1` 字节保持不变，避免破坏已登记的安装摘要及开通幂等性。

## 实现与验证

实现文件：

- `internal/businessworkflow/skills_catalog.go`：当前空间授权、安装状态、固定包摘要和智能体绑定核验。
- `internal/handler/business_skills.go`：目录与详情响应、禁止查询选择器、禁止缓存。
- `internal/router/routes_business_workflow.go`：Viewer 路由门禁。
- `internal/businessworkflow/skills_catalog_test.go`、`internal/handler/business_skills_test.go`：身份、成员状态、跨空间、缺失安装、停用、错误摘要/版本、无效智能体、篡改文件、路径名与存储故障。

定向测试已通过：

```text
go test -ldflags="-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn" ./internal/businessworkflow ./internal/handler -run "BusinessSkill|BusinessSkills" -count=1
```

部署后使用已登录浏览器分别切换总部与已开通伙伴空间，在开发者工具检查上述目录 GET：总部应明确未开通；已开通伙伴应返回九项真实状态和本空间关联智能体。详情只能从可用条目打开。需要人工验证：未部署前不能声称浏览器页面或在线新接口已通过。

## 测试智能体中文名称

本机总部空间 `10000` 的测试智能体 `8a59838d-d859-4fc2-bda4-a43936fd1594` 已由 `BoyanKB Branding Verification` 改名为“品牌验收（测试专用）”。操作使用完整 GET/PUT/GET，保留描述、头像和完整配置；回读核验仅 `name`、`updated_at` 变化，配置摘要及其他字段相同。

`scripts/verify-branding.mjs` 新建测试对象时使用中文名，同时接受旧名与原有专用描述标记。存在多个候选或测试范围不符仍失败，不会因为改名自动新建重复对象。`node --check scripts/verify-branding.mjs` 已通过。
