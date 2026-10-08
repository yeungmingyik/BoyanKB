# 企业定制登记

## 飞书指定空间同步

日期：2026-10-08。基线：v0.8.2，提交 `3e8b0bfc`。分支：`feat/feishu-space-sync`。

| 文件 | 原因 | 改动 |
| --- | --- | --- |
| `internal/middleware/logger.go` | 数据源请求中的 `app_secret` 未被原脱敏规则覆盖 | 补充 App ID、App Secret、飞书前缀字段、tenant token 的脱敏；覆盖转义引号和截断字符串 |
| `internal/middleware/logger_test.go` | 防止正常及错误响应泄漏凭据 | 测试嵌套字段、请求/响应、201/400/500 等路径 |
| `internal/datasource/connector/feishu/core/client.go` | 原获取 token 日志输出 token 前后片段 | 日志仅记录有效期，不记录 token 或片段 |
| `internal/datasource/connector/feishu/core/client_credentials_test.go` | 防止 token 日志回归 | 用内存 HTTP 响应验证凭据及片段不会进入日志 |
| `internal/datasource/connector/feishu/wiki/connector.go` | 原连接器不校验节点空间和快捷方式来源 | 可选 `settings.allowed_space_id`；在获取正文前预检所有选中目录树，拒绝异空间、快捷方式、缺失或未知范围信息；拒绝显式空范围，锁定对应地区的官方 API origin；仅允许 export 模式，资源选择器也限制在目标空间 |
| `internal/datasource/connector/feishu/wiki/connector_scope_test.go` | 证明范围拒绝发生在正文读取前 | 模拟跨空间、快捷方式、分页异常、多根目录、默认兼容、空范围和非官方 origin 等场景，检查不安全配置在发出请求前被拒绝 |
| `docker/Dockerfile.feishu-local` | 本机需要运行修复后的二进制 | 在 v0.8.2 发布镜像上替换 app 二进制，保留原运行时、AnyDoc 功能及上游许可证；镜像标记 `io.weknora.feishu-guard=1` |
| `docker-compose.local.yml` | 启用本机定制镜像与 export 模式 | 固定本机 app 镜像，精确追加 `open.feishu.cn` 的 SSRF 白名单，适配本机 fake-IP DNS |
| `scripts/feishu-space-sync.py` | 可重复执行权限检查、目录预检及同步对账 | 凭据仅由进程读取，不输出真实值；目标空间固定，默认关闭定时与删除；复用数据源时原子更新凭据，增加受限快照、指定 probe 的关键词与基线验证、Wiki 可见空间及用户指定拒绝样本审计；`audit-documents` 只读检查目标 docx 的阅读与导出权限 |
| `scripts/test_feishu_space_sync.py` | 防止权限申请越界、凭据泄漏或验收误判 | 模拟权限申请、目录分页与范围、外部拒绝判定、快照路径与不覆盖、真实分块标记及更新证据、运行镜像检查、HTTP 地址与错误数值处理、文档权限布尔值及范围校验 |

未开启 `allowed_space_id` 的其他数据源继续使用原连接器路径。该开关约束本连接器的同步流程，不会撤销飞书应用原有权限，也不限制拥有数据源管理权限的 WeKnora 管理员另建数据源。飞书端 ACL 仍须单独收窄。

新增文档权限诊断后的完整 19 项 helper 模拟测试通过；定制镜像构建中的 middleware、飞书 core、wiki 三包 Go 测试全部通过，包含最新官方 origin 修复。镜像已部署，6 个容器均 healthy。用户调整导出选项并明确选择应用可编辑授权后，原失败四篇均可阅读、可导出；累计 19 篇解析完成，external ID 完整覆盖，完整 V1 验证退出 0，`temp/feishu-before.json` 已保存 19 篇基线。用户实际修改 probe 为 V2 后，增量同步 success、updated 1、failed 0，带 V1 基线的完整 V2 验证退出 0，内容哈希与源更新时间均变化，真实关键词命中 V2。无变化重复同步也已通过，`temp/feishu-after.json` 已保存，前后 19 篇仅 probe 的知识 ID、哈希和源更新时间变化，其余 18 篇不变。真实新建文档、删除和外部拒绝样本仍未验证。原冒烟脚本使用 testdata 的独立知识库运行成功，不涉及飞书正文的远程模型问答。

补齐目录的同步 `7f02e8fe-b7b2-4d69-86e8-49df6b04b701` 为 success、total 3、created 3、updated 0、failed 0。旧失败日志 `f1c50627-38a4-4ba4-9636-cb84ec41538a` 经队列自动重试，04:42:55 UTC 变为 partial、total 4、created 1、failed 3，而 `started_at` 仍为 04:31:23 UTC。因此该次手动同步只新增 3 篇，总数仍达到 19；日志不是不可变事件。空 schedule 和 paused 不取消已入队任务的 Asynq `MaxRetry(5)` 重试。之后 V2 更新日志 `1df478b1-0e73-4a77-8af8-7dd84f82d338` 为 success、total 1、created 0、updated 1、failed 0。

无变化重复同步 `6a005d74-f6b9-4e74-bd34-8e6e29f45ce2` 为 success，total、created、updated、failed 均为 0；19 篇仍解析完成，数量与 ID 不变，前后快照的 external ID 集合一致。

核心安全修复的六个源码及测试文件已提交为 `5af368ad`；同步工具、部署配置及对应文档初版已提交为 `16be84d5`。

权限调整前的只读诊断曾确认四篇有应用的只读协作者记录，历史 v1 public 返回 `security_entity=anyone_can_edit`，probe 为 `anyone_can_view`；这些记录不代表用户调整后的当前 ACL。v2 public 当时均返回 HTTP 403、`1063002`，`copy_entity` 与 `lock_switch` 仍未验证，不据此推断现代继承或企业策略。当前沿用用户已选择的可编辑授权；可阅读身份加允许阅读者导出仅作为更小权限的可选方案，不要求改回。诊断未输出 AppID、成员 ID 或凭据。

最终使用同一 tenant 应用的原生 auth 接口只读遍历目标 19 篇 docx，view、export、edit 三项均为 19 true，无同步权限拒绝。当前目标资源 ACL 实际允许编辑，按用户选择保留，不自动撤销；`audit-documents` CLI 本身仍只检查 view/export。可见 Wiki 空间仍只有目标 1 个，不据此推断其他 Drive、单篇外链或外部资源均不可访问。

验证命令、镜像 config ID、同步记录和后续实测状态见 [02-feishu.md](02-feishu.md)。不以模拟测试替代真实授权、数量和增量验收。

## BoyanKB 品牌定制

日期：2026-10-08。基线：v0.8.2，继承飞书安全修复。分支：`feat/branding-boyankb`。用户选择不显示 Powered by、隐藏官方帮助链接、隐藏未使用第三方入口。完整部署、兼容边界和升级步骤见 [03-branding.md](03-branding.md)。

| 文件 | 原因 | 改动 |
| --- | --- | --- |
| `branding/brand.json`、`branding/logo.svg` | 单一品牌配置和用户素材 | 集中名称、slug、浅蓝色、资源路径、空帮助链接和显示开关；原始 Logo 保持原文件 |
| `internal/branding/brand.go`、`internal/branding/swagger.go` | 后端产品文案无法通过前端镜像覆盖 | 读取同一 JSON，验证配置；仅替换产品文字、Swagger 元数据，保留真实第三方与协议标识 |
| `internal/branding/brand_test.go`、`internal/branding/swagger_test.go` | 避免替换污染协议或第三方身份 | 覆盖配置验证、默认兼容、身份句、真实 Swagger 文档与 schema 保留 |
| `internal/config/config.go`、`internal/config/branding.go`、`internal/config/branding_test.go` | 默认助手仍自称上游产品 | 配置加载时处理四类内置提示词，保留 YAML 原文件、模板 ID 与用户显式提示词 |
| `internal/router/router.go`、`internal/router/swagger_branding.go` | Swagger 编译嵌入信息含上游品牌 | 在原有非 release 文档路由输出品牌化规范，空链接移除上游联系信息 |
| `internal/handler/memory.go`、`internal/handler/sandbox_check.go` | 导出文件名与自检文字包含旧产品名 | 使用统一品牌名称与 slug |
| `internal/mcp/client.go`、`internal/mcp/oauth_manager.go`、`internal/mcpserver/server.go`、`internal/mcpserver/tools_ask.go` | MCP 握手、介绍与注册展示名可见 | 从品牌配置生成名称和说明，保留授权协议及持久标识 |
| `internal/im/service.go`、`internal/im/feishu/adapter.go` | 消息卡片和授权提示含旧名称 | 默认产品名称读取品牌配置 |
| `internal/agent/tools/todo_write.go`、`internal/application/service/tenant_skill_install.go`、`internal/application/service/workspace_reset.go`、`internal/browserskill/authorization.go` | 工具、安装、重置与浏览器授权说明含旧名称 | 使用品牌化说明；保留功能、第三方包与兼容目录 |

后端镜像构建已通过 `internal/branding`、`internal/config`、`internal/middleware`、飞书 `core` 与 `wiki` 五包测试，包含实际四类 YAML 和 Swagger 文档的品牌边界测试。前端五语言检查首轮 13 项通过；镜像和浏览器最终验证进行中，完成后追加记录。
