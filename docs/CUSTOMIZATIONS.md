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
| `branding/brand.json`、`branding/logo.svg` | 单一品牌配置和用户素材 | 集中名称、slug、浅蓝色、资源路径、空帮助链接和显示开关；Logo 后续按下节替换为 VIS 矢量版本 |
| `internal/branding/brand.go`、`internal/branding/swagger.go` | 后端产品文案无法通过前端镜像覆盖 | 读取同一 JSON，验证配置；仅替换产品文字、Swagger 元数据，保留真实第三方与协议标识 |
| `internal/branding/brand_test.go`、`internal/branding/swagger_test.go` | 避免替换污染协议或第三方身份 | 覆盖配置验证、默认兼容、身份句、真实 Swagger 文档与 schema 保留 |
| `internal/config/config.go`、`internal/config/branding.go`、`internal/config/branding_test.go` | 默认助手仍自称上游产品 | 配置加载时处理四类内置提示词，保留 YAML 原文件、模板 ID 与用户显式提示词 |
| `internal/router/router.go`、`internal/router/swagger_branding.go` | Swagger 编译嵌入信息含上游品牌 | 在原有非 release 文档路由输出品牌化规范，空链接移除上游联系信息 |
| `internal/handler/memory.go`、`internal/handler/sandbox_check.go` | 导出文件名与自检文字包含旧产品名 | 使用统一品牌名称与 slug |
| `internal/mcp/client.go`、`internal/mcp/oauth_manager.go`、`internal/mcpserver/server.go`、`internal/mcpserver/tools_ask.go` | MCP 握手、介绍与注册展示名可见 | 从品牌配置生成名称和说明，保留授权协议及持久标识 |
| `internal/im/service.go`、`internal/im/feishu/adapter.go` | 消息卡片和授权提示含旧名称 | 默认产品名称读取品牌配置 |
| `internal/agent/tools/todo_write.go`、`internal/application/service/tenant_skill_install.go`、`internal/application/service/workspace_reset.go`、`internal/browserskill/authorization.go` | 工具、安装、重置与浏览器授权说明含旧名称 | 使用品牌化说明；保留功能、第三方包与兼容目录 |

后端镜像构建已通过 `internal/branding`、`internal/config`、`internal/middleware`、飞书 `core` 与 `wiki` 五包测试，包含实际四类 YAML 和 Swagger 文档的品牌边界测试。前端五语言检查 13 项、定向 TS 检查 23 项、agent 编辑器检查 15 项、widget 检查 6 项、品牌检查器测试 8 项均通过。最终部署、真实问答与浏览器证据见 [03-branding.md](03-branding.md)。

前端、构建与验收文件登记：

| 文件 | 原因 | 改动 |
| --- | --- | --- |
| `branding/allowed-upstream.json` | 防止漏改与不受控例外 | 精确例外清单、源码检查与真实 HTTP 资源校验 |
| `docker-compose.branding.yml` | 独立构建和部署覆盖 | 品牌镜像、敏感文件排除、许可证交付；继承飞书修复 |
| `docker/Dockerfile.app-boyankb` | 独立构建和部署覆盖 | 品牌镜像、敏感文件排除、许可证交付；继承飞书修复 |
| `docker/Dockerfile.app-boyankb.dockerignore` | 独立构建和部署覆盖 | 品牌镜像、敏感文件排除、许可证交付；继承飞书修复 |
| `docker/Dockerfile.frontend-boyankb` | 独立构建和部署覆盖 | 品牌镜像、敏感文件排除、许可证交付；继承飞书修复 |
| `docker/Dockerfile.frontend-boyankb.dockerignore` | 独立构建和部署覆盖 | 品牌镜像、敏感文件排除、许可证交付；继承飞书修复 |
| `frontend/branding-plugin.ts` | 构建时配置与资源覆盖 | 注入 HTML、颜色和 widget；覆盖旧 Logo 引用并排除隐藏宣传图 |
| `frontend/embed.html` | 首屏标题与浏览器图标 | 构建时替换标题、描述和资源地址 |
| `frontend/index.html` | 首屏标题与浏览器图标 | 构建时替换标题、描述和资源地址 |
| `frontend/nginx.conf` | 新旧 widget、品牌素材和许可证分发 | 统一脚本缓存；品牌资源重新验证缓存；许可证使用纯文本类型 |
| `frontend/public/weknora-widget.js` | 嵌入展示与 SDK 兼容 | 品牌默认值、新代码示例和旧协议兼容 |
| `frontend/src/api/embed/index.ts` | 嵌入展示与 SDK 兼容 | 品牌默认值、新代码示例和旧协议兼容 |
| `frontend/src/api/embed/widgetBranding.test.mjs` | 验证隐藏目录、嵌入兼容与集成示例 | 针对品牌边界的回归检查 |
| `frontend/src/assets/theme/branding.css` | 统一浅蓝视觉 | 覆盖明暗主题品牌 token，保持语义状态色 |
| `frontend/src/components/AgentEmbedChannelPanel.vue` | 嵌入展示与 SDK 兼容 | 品牌默认值、新代码示例和旧协议兼容 |
| `frontend/src/components/Input-field.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/components/ModelEditorDialog.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/components/SandboxConfigEditorDrawer.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/components/UserMenu.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/components/menu.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/config/branding.ts` | 页面共享品牌来源 | 封装共享 JSON、帮助 URL 和厂商显示策略 |
| `frontend/src/config/integrations.ts` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/embed-main.ts` | 两入口加载品牌样式 | 加入颜色覆盖；主入口诊断前缀读取配置 |
| `frontend/src/i18n/embed.ts` | 多语言产品文字分散 | 引用统一名称和 slug，保留原翻译 key 与真实协议 |
| `frontend/src/i18n/locales/en-US.ts` | 多语言产品文字分散 | 引用统一名称和 slug，保留原翻译 key 与真实协议 |
| `frontend/src/i18n/locales/ja-JP.ts` | 多语言产品文字分散 | 引用统一名称和 slug，保留原翻译 key 与真实协议 |
| `frontend/src/i18n/locales/ko-KR.ts` | 多语言产品文字分散 | 引用统一名称和 slug，保留原翻译 key 与真实协议 |
| `frontend/src/i18n/locales/ru-RU.ts` | 多语言产品文字分散 | 引用统一名称和 slug，保留原翻译 key 与真实协议 |
| `frontend/src/i18n/locales/zh-CN.ts` | 多语言产品文字分散 | 引用统一名称和 slug，保留原翻译 key 与真实协议 |
| `frontend/src/main.ts` | 两入口加载品牌样式 | 加入颜色覆盖；主入口诊断前缀读取配置 |
| `frontend/src/views/agent/AgentEditorModal.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/auth/Login.vue` | 登录产品身份与明暗主题可读性 | 接入共享 Logo/名称、隐藏宣传页；提示、注册按钮与链接使用适配明暗主题的文字颜色 |
| `frontend/src/views/embed/EmbedPage.vue` | 嵌入展示与 SDK 兼容 | 品牌默认值、新代码示例和旧协议兼容 |
| `frontend/src/views/integrations/ApiIntegrationSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/integrations/CliIntegrationLanding.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/integrations/IntegrationSettingsSection.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/integrations/mcpServerIntegration.test.ts` | 验证隐藏目录、嵌入兼容与集成示例 | 针对品牌边界的回归检查 |
| `frontend/src/views/integrations/mcpServerIntegration.ts` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/knowledge/components/FAQEntryManager.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/knowledge/settings/DataSourceEditorDialog.test.ts` | 验证隐藏目录、嵌入兼容与集成示例 | 针对品牌边界的回归检查 |
| `frontend/src/views/knowledge/settings/DataSourceEditorDialog.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/knowledge/settings/DataSourceSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/knowledge/settings/GraphSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/knowledge/settings/KBParserSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/knowledge/settings/chunkingSamples.ts` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/BrowserConnectionSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/MemorySettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/ModelSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/ParserEngineSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/SandboxSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/Settings.vue` | 隐藏未用服务时必须保留已有凭据维护路径 | 按当前租户凭据/模型状态放行 Cloud 管理页；保留管理员门槛，拒绝晚到的跨租户响应 |
| `frontend/src/views/settings/settingsNavigation.test.ts` | 防止隐藏服务后无法恢复已有配置或跨空间误放行 | 覆盖未配置、已有/失效凭据、已有模型、查询失败及切空间竞态 |
| `frontend/src/views/settings/StorageBackendSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/SystemInfo.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/TenantMembers.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/VectorStoreSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/src/views/settings/WeKnoraCloudSettings.vue` | 产品文案与上游入口分散 | 接入统一品牌、隐藏空帮助链接及未使用第三方选项；保留已有配置 |
| `frontend/vite.config.ts` | 构建时配置与资源覆盖 | 注入 HTML、颜色和 widget；覆盖旧 Logo 引用并排除隐藏宣传图 |
| `scripts/build-image.sh` | 提供一条命令构建 | Docker 内构建，支持 --with-app，不自动启动服务 |
| `scripts/check-branding.mjs` | 防止漏改与不受控例外 | 精确例外清单、源码检查与真实 HTTP 资源校验 |
| `scripts/check-branding.test.mjs` | 防止漏改与不受控例外 | 精确例外清单、源码检查与真实 HTTP 资源校验 |
| `scripts/migrate-branding-smoke.mjs` | 原测试记录带旧品牌 | 读取品牌配置；只迁移固定测试名称且精确匹配旧描述的记录 |
| `scripts/smoke-test.sh` | 原测试标记带旧品牌且刷新环境会恢复旧镜像 | 读取品牌名称与 slug；检查现有 app 品牌标签，包含停止容器，刷新时保留品牌 Compose 覆盖 |
| `scripts/verify-branding.mjs` | 实际页面验收 | 隔离浏览器、五语言与主题、厂商隐藏、widget 和测试嵌入截图；只在准备模式修正经严格范围验证的测试渠道推荐开关 |
| `docs/01-deploy-local.md`、`docs/03-branding.md`、`docs/CUSTOMIZATIONS.md` | 避免后续部署遗漏品牌层 | 记录三层 Compose、配置入口、例外、重放升级及实际验证证据 |

## Logo 透明矢量版

日期：2026-10-08。分支：`feat/logo-vector`。来源：用户提供的 VIS PDF，保留第 2 页标志曲线，第 5 页用于结构参考。配色按用户授权简化为网页纯色。详情与验证见 [04-logo-vector.md](04-logo-vector.md)。

| 文件 | 原因 | 改动 |
| --- | --- | --- |
| `branding/logo.svg` | 原 SVG 内嵌截图，含白底且小尺寸模糊 | 换为 PDF 原始曲线组成的 8 个纯色 path，透明背景与紧凑边距 |
| `branding/logo.png` | 兼容 PNG 场景与触屏图标 | 从同一 SVG 渲染透明 RGBA 素材 |
| `frontend/branding-plugin.ts` | 原构建要求 SVG 内嵌 PNG | 分别读取 SVG、PNG，仍按共享配置分发 |
| `docker/Dockerfile.frontend-boyankb.dockerignore` | 前端构建原先未放行独立 PNG | 精确加入 `branding/logo.png` |
| `scripts/check-branding.mjs`、`scripts/check-branding.test.mjs` | 素材检查原先依赖内嵌 PNG | 改为读取独立 PNG，保留实际 HTTP 资源字节一致性校验 |
| `docs/03-branding.md`、`docs/04-logo-vector.md`、`docs/CUSTOMIZATIONS.md` | 保持素材维护说明与实际状态一致 | 记录来源、配色、透明通道、构建部署与验证方法 |

升级上游时一起保留两种 Logo 素材及上述四处构建/检查接入；无需保留原 PDF 或安装 PDF 提取库才能正常构建。LICENSE 与上游版权声明不变。

## 标签图标、登录布局与交互色

日期：2026-10-08。分支：`feat/branding-polish`。覆盖 favicon 未刷新、普通登录页过长、语言选择器及类似交互仍用旧绿色的问题。部署与证据见 [05-ui-polish.md](05-ui-polish.md)。

| 文件 | 原因 | 改动 |
| --- | --- | --- |
| `branding/brand.json`、`branding/favicon-32.png`、`branding/favicon.ico` | SVG 标签图标缺少传统格式兼容 | 增加 PNG/ICO 配置与透明图标，ICO 含 16/32/48 三种尺寸 |
| `frontend/branding-plugin.ts`、`frontend/index.html`、`frontend/embed.html` | 原图标地址固定，旧缓存可能继续命中 | 三种图标均使用内容哈希文件名；开发与生产分发稳定及哈希地址 |
| `frontend/nginx.conf` | `/favicon.ico` 原先回退为 SPA HTML | 专用 ICO 路由、类型和重新验证缓存 |
| `docker/Dockerfile.frontend-boyankb`、`docker/Dockerfile.frontend-boyankb.dockerignore` | 原构建删除 ICO，新增素材需进入上下文 | 放行两个精确资源；比较源/产物 ICO 字节；运行输入框相关现有测试 |
| `frontend/src/views/auth/Login.vue` | 登录留白过多，表单选择器未命中实际类名，语言菜单误用成功绿 | 收紧紧凑布局并修正 TDesign 字段/标签选择器；返回链接整块换行；保留自然滚动；语言与静态特点图标使用品牌 token |
| `frontend/src/components/icons/ResourceIcon.vue`、`frontend/src/components/menu.vue` | 旧绿色 SVG 不能随主题变色 | 复用遮罩图标，支持源资源 URL，按 currentColor 显示选中态 |
| `frontend/src/components/AgentSelector.vue`、`frontend/src/components/MentionSelector.vue`、`frontend/src/components/ResourceOriginBadge.vue` | 共享资源仍引用固定绿色图像或文本色 | 改用现有资源图标与品牌文字 token |
| `frontend/src/components/Input-field.vue`、`frontend/src/components/EmbedInputField.vue`、`frontend/src/components/KnowledgeBaseSelector.vue`、`frontend/src/components/PromptTemplateSelector.vue` | 模式、选择、焦点和禁用发送误用成功色 | 用对应的品牌选择、焦点、文字或禁用 token |
| `frontend/src/components/SettingsModalShell.vue`、`frontend/src/components/UserMenu.vue`、`frontend/src/components/document-preview.vue` | 导航、选择与预览中的品牌色不一致 | 使用明暗主题选择背景与品牌文字色 |
| `frontend/src/views/agent/AgentEditorModal.vue`、`frontend/src/views/agent/AgentList.vue` | 智能体选择与共享资源标记含旧绿 | 使用品牌 token 和共享遮罩图标 |
| `frontend/src/views/knowledge/KnowledgeBase.vue`、`frontend/src/views/knowledge/KnowledgeBaseList.vue`、`frontend/src/views/knowledge/components/FAQEntryManager.vue` | 知识库标签和 FAQ 操作混用成功色 | 选择、悬停、资源标记改用品牌 token；成功状态保留 |
| `frontend/src/views/organization/OrganizationList.vue`、`frontend/src/views/organization/OrganizationSettingsModal.vue` | 共享空间资源图标和品牌标记固定为绿色 | 复用资源图标与品牌文字色 |
| `frontend/src/views/settings/ParserEngineSettings.vue` | 内置解析器与外部服务共用厂商绿色 | 分离内置和外部供应商配色 |
| `scripts/check-branding.mjs`、`scripts/check-branding.test.mjs` | 原检测错误接受 ICO 地址返回 HTML | 检查哈希链接、三种文件格式/尺寸与字节，拒绝旧地址及 HTML 回退 |
| `scripts/verify-branding.mjs` | 既有浏览器检查未覆盖短屏与实际图标请求 | 添加桌面/移动布局、语言颜色、控件可达性及 favicon 网络证据 |
| `docs/03-branding.md`、`docs/05-ui-polish.md`、`docs/CUSTOMIZATIONS.md` | 维护升级、验证及已知边界 | 记录最新资源格式、分支重放顺序与验收结果 |

未全局改变 success token、真实厂商图标、状态语义或业务数据。保留 LICENSE 及上游版权声明。

最终镜像已在本机替换 frontend；15 个 HTTP URL 与 133 项浏览器检查通过，六个容器健康。升级时按 [03-branding.md](03-branding.md) 的分支顺序重放，并运行 [05-ui-polish.md](05-ui-polish.md) 的检查命令；尤其确认 TDesign 类名与图标资源字节。200% 缩放及用户既有标签栏外观未验证。
## 邀请制生产部署与恢复验收

日期：2026-10-09。分支：`feat/production-deployment`。目标域名 `kb.boyantech.com`，阿里云服务器信息待补全；没有连接或修改云端生产。采用现有 BoyanKB 镜像的配置覆盖，不修改上游认证、检索或沙箱核心。LICENSE 和版权声明保留。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `docker-compose.prod.yml` | 独立生产拓扑与版本锁 | 仅 Caddy 发布 80/443，内部 DB/Redis，邀请制、RBAC、无运行时凭据、资源限制、禁止自动迁移；`check-production.py` 验证实际运行参数 |
| `docker-compose.staging.yml`、`docker-compose.restore.yml` | 本机演练不能接管业务栈 | 各用回环端口与独立网段；恢复所有网络 internal；通过隔离报告及恢复后检索检查 |
| `deployment/production/images.lock.json` | 标签可能漂移 | 保存上游提交、七个运行镜像 digest、embedding manifest 哈希；启动检查内容一致 |
| `deployment/production/Caddyfile`、`Caddyfile.staging`、`Caddyfile.common` | TLS 与敏感入口控制 | 公网自动 HTTPS、本地 CA、注册/OIDC/沙箱/调试入口限制、可信代理头；真实 TLS/HTTP 请求验证 |
| `deployment/production/frontend-entrypoint.sh` | 两层代理需保留 HTTPS 与客户端地址 | 仅在容器内生成代理覆盖，精确信任 Caddy IP；不重新改动上游 Nginx 源文件 |
| `deployment/production/redis-entrypoint.sh` | Redis 密码不能出现在命令行 | 从环境写入 tmpfs 配置，限制格式；认证健康检查与恢复验证 |
| `deployment/production/builtin_agents.yaml` | 合作伙伴需要基于依据回答 | 唯一 quick-answer、BoyanKB 名称、禁止联网补答、固定无依据回复；实际配置与合成问答验证 |
| `scripts/init-production.py` | 新环境需要独立秘密和受保护文件 | 只创建忽略目录下新 `.env`，生成密钥、可选择导入模型凭据，限制 ACL，不输出值 |
| `scripts/bootstrap-production.py` | 关闭注册后仍需首位管理员 | 仅空库、无公开入口临时初始化与开户，结束后恢复邀请制；拒绝既有 schema |
| `scripts/check-production.py` | 配置文件不代表实际生效 | 检查运行镜像、资源、环境策略、端口、TLS、品牌与入口；报告只含状态 |
| `scripts/acceptance-prod.py` | 验证多伙伴与检索边界 | 默认只读，显式隔离模式创建合成样本；维护窗口/邀请/角色/Key/注入/拒答检查；独立离线测试 |
| `scripts/backup-prod.py`、`scripts/restore-prod.py`、`scripts/test_prod_backup.py` | 数据、对象与密钥必须一起恢复 | age 双层认证加密、停写快照、新目标保护、配置哈希与行数/文件校验；不覆盖现有卷 |
| `scripts/check-restored.py` | 存储恢复不代表应用可用 | 恢复管理员登录、原资源 ID、片段、文件下载与本地关键词/向量检索；不调用外部服务 |
| `scripts/restore-tunnel.py` | internal 网络下 Docker 未实际发布恢复测试端口 | 仅本机回环、固定目标 Caddy/端口、有限时长/并发的 TCP 字节转发；不解密 TLS、不改变容器网络 |
| `scripts/check-sandbox-isolation.py`、`deployment/sandbox/test_isolation.py` | app 与代码沙箱不能混同 | app 无运行时表面检查；专用远端容器严格前检与显式授权网络探针；mock 测试不代替远端验收 |
| `deployment/sandbox/offline-config.example.json`、`daemon.example.json`、`office-smoke.py` | 为将来受控 Office 能力留明确门槛 | 离线网络/mTLS/资源配置样例及 OOXML 探针；当前未启用远端执行，后续限制见沙箱文档 |
| `docs/05-deploy-production.md`、`05-upgrade-runbook.md`、`05-acceptance.md` | 部署、升级、回滚需要同一证据标准 | 固定镜像/配置重放流程、备份恢复命令、人工验收清单及本机证据 |
| `docs/production-auth.md`、`production-backup.md`、`production-sandbox.md` | 记录实现边界，避免误用开关 | 邀请和 OIDC 差异、原始跨空间标志、加密恢复保护、禁用执行与未来沙箱门槛 |

升级时保留上述独立文件，先在新上游上验证覆盖文件路径及配置优先级；重新构建品牌镜像后更新 Compose 与锁文件完整 digest。按 [05-upgrade-runbook.md](05-upgrade-runbook.md) 恢复旧数据到隔离副本、迁移、回归，再决定发布。不得把旧版本测试结果直接套用到新镜像；公网 HTTPS、Office 生成、真实飞书删除与真实伙伴授权仍需相应人工验收。

## 合作伙伴预设、场景模板与受限文件生成

日期：2026-10-09。分支：`feat/partner-presets`。根据 `docs/business/agents-spec.md` 和用户关于先设计模板再继续实现的授权执行。本机新增配置覆盖，云端部署继续暂停。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `branding/templates/*` | 教学、商务、运营需要统一但有区别的正式文件 | 十二份原生模板、版本摘要；PPT/Word/Excel 在本机 Office 打开，逐页检查，预算公式实算 |
| `presets/boyan/v1/*`、`skills/boyan/*`、`.gitattributes` | 可版本化并重复安装业务规范 | 三名 Agent、五个场景与四个格式指南、固定 SHA 与 LF；静态契约及 24 条行为案例 |
| `internal/boyanskills/*` | 指南读取不能变成任意 Skill 执行 | 固定路径、实际安装登记、版本和摘要校验；不打开原生沙箱 |
| `internal/types/partner_provisioning.go`、`internal/application/service/partner_provisioning*`、`internal/handler/partner_provisioning*`、`internal/router/routes_partner_provisioning.go` | 原开户 API 不提供受限、可恢复的标准预设事务 | 系统管理员 JWT 加总部 Owner、幂等 key、事务与执行租约、角色和资源读回校验 |
| `migrations/{versioned,sqlite}/000111_partner_provisioning*` | 保留开户步骤与安装登记 | 新增独立表；不改原业务数据 |
| `internal/businessworkflow/*`、`internal/agent/tools/boyan_document.go`、`internal/handler/business_workflow.go`、`internal/router/routes_business_workflow.go` | 提示词不能强制用户确认或阻断文件发布 | 大纲哈希、权限复验、候选隔离、固定渲染、独立审校、通过后下载 |
| `internal/businessworkflow/artifact_text*`、`reviewer.go`、`types.go`、`internal/agent/tools/boyan_document.go` | 审校必须读取真实文件结构，预算不能成为模型自写伪公式 | Excel地址/共享串/公式缓存，PPT实际播放页序及关联备注，DOCX正文和页眉页脚；服务器日期与受信固定规则；结构化预算输入门禁；真实生成Office文件抽取测试 |
| `internal/businessworkflow/render_limits*`、`types.go`、`service.go` | 已确认内容不应因可预先检查的渲染限额而失败 | 提案阶段对齐 Unicode 字符数、章节/表格/预算数量、来源文本与数值范围；字段级纠正提示；边界与拒绝落库测试 |
| `internal/businessworkflow/revision*`、`internal/modelcontext/boyan_revision.go`、`registry.go`、`boyan_document_test.go` | 跨轮修订不能丢失原稿及来源，也不能从任意正文伪造来源句柄 | 身份、摘要、当前共享权限和指南重新验证；仅服务器验证来源恢复映射；新稿重新确认；撤销、伪来源和跨空间测试 |
| `internal/businessworkflow/reviewer_retry_test.go`、`reviewer.go`、`render_limits*`、`types.go`、`service.go` | 矛盾审校与确认后超限会使正常任务无法完成 | 同一 C 模型最多一次结论澄清，仍异常拒绝发布；Unicode 字符、集合、预算及来源限额提前校验；边界与失败关闭测试 |
| `internal/businessworkflow/budget_tables*`、`types.go`、`reviewer.go`、`internal/agent/tools/boyan_document.go` | 两套未联动的 Excel 输入和静态汇总会误导修改者 | 普通表格不得重复结构化预算的数量与单价；真正公式与静态文本分开审校；中英文表头、单位和保留场景测试 |
| `internal/businessworkflow/renderer.go`、`renderer*_test.go`、`reviewer.go` | C 缺失机械检查证据并误判真实 Excel 公式 | 按格式白名单核验内部渲染检查并绑定文件摘要，独立传递给 C；明确输入、公式和缓存含义及未执行扰动边界；伪造、缺项、摘要和拒绝不被覆盖测试 |
| `internal/application/service/boyan_runtime_policy.go`、`boyan_rerank*`、`session_agent_qa.go` | 真实对话出现重复追问、时长及风险结构遗漏 | ready 记录绑定的三名 Agent 附加运行约束；不能仅凭客户端开关生效；身份与状态门禁测试 |
| `migrations/{versioned,sqlite}/000112_boyan_document_jobs*` | 文档状态和候选需持久化 | 每个租户/用户/会话独立记录、确认和发布约束、候选二进制留在受控表 |
| `internal/container/container.go`、`internal/router/router.go`、`internal/application/service/agent_service.go`、`session_agent_qa.go`、`internal/types/agent.go`、`internal/modelcontext/tool_policy.go`、`internal/agent/tools/definitions.go` | 接入业务工具及可验证来源 | 依赖注入、路由、新工具显式允许、实际 Agent ID 与分块 ID 解码；保留其他 Agent 默认工具行为 |
| `internal/application/service/boyan_rerank*`、`internal/types/custom_agent.go`、`frontend/src/api/agent/index.ts`、`frontend/src/utils/agent-readiness*`、`frontend/src/components/Input-field.vue`、`frontend/src/views/agent/AgentEditorModal.vue` | 本机没有单独的重排服务，原 Agent 入口强制报错 | 受管 ready 预设显式允许已有混合检索路径，服务端校验绑定；非受管 Agent 默认要求不变，非空模型失败不降级 |
| `frontend/src/api/business-workflow.ts`、`frontend/src/views/chat/components/BusinessDocumentPanel.vue`、`frontend/src/views/chat/index.vue` | 用户需看到并确认真实服务端大纲 | 会话任务卡、明确确认、生成与审校状态、授权下载 |
| `deployment/document-renderer/*`、`docker-compose.presets.yml` | 不以任意代码执行实现 Office 生成 | 固定四种格式、模板摘要、预算公式重算；内部网络、只读根、非 root、资源限制 |
| `deployment/document-renderer/render.py`、`test_render.py` | Markdown/Excel 标题重复品牌，商务 FAQ 被固定标为合作方案 | 两种格式共用标题前缀去重；商务 Markdown 省略固定类别，保留正文和来源；不改模板摘要；11 项渲染测试通过，覆盖三场景及 Excel 真实重算 |
| `deployment/document-renderer/render.py`、`test_render.py` | PPT 固定字符切割拆开数字并产生孤立续页 | 正文及来源标题按安全句读边界分段，数字/ASCII 词保持完整；三要点分页平衡尾页，保留原字、来源和备注；14 项渲染测试通过，含真实 PPT 重开、文本框尺寸和 LibreOffice PDF 导出 |
| `deployment/document-renderer/render.py`、`test_render.py` | 短尾行平衡将可用两行变为三行，错误拒绝渲染 | 保留同字号的原始安全换行作为回退；15 项回归测试通过；真实 B 请求生成 18 页并导出 PDF，不改模板或原文 |
| `deployment/document-renderer/render.py`、`render_pptx_v2.py`、`server.py`、`test_template_versions.py`、`test_render_v2.py` | 新视觉不能覆盖 v1 模板或只替换封面 | 双版本模板注册与摘要/布局校验；v2 按内容选择八类真实布局，保留正文、表格、来源和备注；旧版渲染分支保留 |
| `deployment/document-renderer/render_pptx_v2.py`、`test_render_v2.py` | 真实 PowerPoint 中中文正文和总结字形略超文本框 | v2 拟合、分页与写入统一使用 1.30 倍行高余量，短尾平衡遵循同一门槛；真实提案离线重放并保留原生 Office 检查，v1 不变 |
| `internal/businessworkflow/template_version*`、`service.go`、`renderer.go`、`readiness.go`、`docker-compose.presets.yml` | 视觉升级不能破坏既有确认、下载和开通幂等 | 服务器显式选择新 PPT 版本，版本写入确认哈希；未知版本拒绝；健康能力校验；本地镜像/配置使用 v2，生产与已安装预设不变 |
| `docker/Dockerfile.app-boyankb`、`scripts/build-presets-images.sh` | 定制镜像需要带上预设和迁移 | 三个本机版本镜像，应用嵌入配置、指南及新增迁移 |
| `scripts/onboard-partner*`、`scripts/test_onboard_partner.py` | 重复执行不重复资源，失败能恢复 | 默认本地计划，显式 apply、断点记录、readiness 通过才完成 |
| `scripts/check-presets.py`、`scripts/acceptance-presets.py`、`scripts/test_acceptance_presets.py` | 配置、工作流与真实行为要分开验证 | 静态检查、两个非管理员伙伴、真实文件与隔离测试；下载模式仅收集当前已发布稿，不调用模型或确认；明确人工项 |
| `scripts/verify-presets-office.ps1` | ZIP 检查不能代替 Office 真正打开 | PowerShell 7、实际下载摘要、只读打开、PPT 导出及溢出检查、Word/Excel PDF；Excel 原生重算、缓存值对比及数量/单价扰动验证，恢复输入、关闭且不保存后复核原文件摘要 |
| `docs/04-presets.md`、`docs/partner-onboarding.md`、`docs/business/document-workflow.md` | 安装与升级可重复 | 方案取舍、执行命令、边界、验收与重放顺序 |

升级时先重放独立资源，再逐项检查表中核心接入点；不得只复制提示词后宣称确认和权限约束仍然有效。重新构建并在隔离副本运行自动及人工验收。LICENSE 与上游版权声明保留。

## PPT 设计、业务技能入口与中文提示词

日期：2026-10-09。分支：`feat/ppt-design-chinese`。本机功能优化，云端部署继续暂停。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `branding/templates/ppt-v2/*`、`scripts/build-ppt-templates.mjs`、`docs/assets/ppt-v2/*` | 原生成课件版式单一 | 三场景、八布局、原创透明插画、原生文字及表格；单独版本与摘要，不覆盖 v1；原生 PowerPoint 打开并逐页检查 |
| `scripts/build-presets-images.sh` | 构建命令需要匹配本机视觉版本 | 默认三个镜像标签升级为 v2；应用、前端、文档渲染器分别构建 |
| `internal/businessworkflow/skills_catalog*`、`internal/handler/business_skills*`、`internal/router/routes_business_workflow.go` | 普通伙伴无法查看受限业务技能，目录状态与原生沙箱不同 | 当前空间 JWT 成员读取，真实预设/安装/摘要校验，只读正文；未开通与异常不伪装为可用；成员、跨空间与篡改测试 |
| `frontend/src/api/business-skills.ts`、`frontend/src/composables/businessSkillCatalog*`、`frontend/src/config/businessSkills.ts`、`frontend/src/views/toolbox/{BusinessSkills,Toolbox}.vue`、`frontend/src/config/toolbox*` | 工具箱需要清晰的业务入口 | 成员可见业务技能页、五场景和四格式、只读说明、关联落地方案官；切换空间清空旧数据，丢弃过期响应 |
| `frontend/src/views/settings/SkillSettings.vue`、`frontend/src/i18n/businessSkills.ts`、`frontend/src/i18n/locales/*` | 已知业务技能仍显示英文技术标识 | 九项业务技能中文显示与状态区分；标识保留在技术详情，不改变存储与调用参数；安装入口保护和界面测试 |
| `branding/prompt_templates/{agent_system_prompt,system_prompt}.yaml`、`docker/Dockerfile.app-boyankb`、`internal/config/branding_prompt_templates_test.go` | 内置 Agent 编辑器正文仍是英文 | 13 个中文配置覆盖，保持 ID/变量/规则/非中文元数据；启动加载及语义边界测试；不批量覆盖用户已保存的自定义提示词 |
| `scripts/verify-branding.mjs` | 旧品牌测试 Agent 的英文名字容易与业务 Agent 混淆 | 兼容中文名与旧标记，避免重复创建；现有指定对象完整读写后，仅名称及更新时间变化，配置摘要一致 |
| `docs/06-ppt-design.md`、`docs/06-agent-skill-validation.md`、`docs/business/{skills-catalog,chinese-prompts}.md` | 需要区分模板、指南安装、真实任务和人工验收 | 操作入口、可重复命令、正反例、权限与专业审校边界 |

重新合入上游时先保留 `branding/` 独立资源及 Dockerfile 的覆盖 COPY；检查模板配置目录、工具箱路由、成员校验和业务工作流的版本字段是否变化。运行本文对应测试，再进行真实任务和 Office 验证。对已有确认记录使用过的模板版本不原地覆盖，新设计发布新版本；旧版资源继续保留。运行时预设与技能仍为 v1，与 PPT 视觉 v2 分开管理。

## 合作伙伴交付界面整理

日期：2026-10-10。分支：`feat/partner-delivery-cleanup`。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `frontend/src/components/Input-field.vue`、`frontend/src/views/agent/AgentEditorModal.vue` | 可选重排提示干扰聊天，设置说明过于技术化 | 移除聊天常驻提示，配置页改为可选增强说明；不改变提交或后台校验 |
| `frontend/src/views/toolbox/BusinessSkills.vue`、`frontend/src/i18n/businessSkills.ts` | 普通使用说明直接暴露完整技能协议 | 默认场景、输入和确认流程说明；原协议折叠至技术详情，未开通时联系管理员 |
| `frontend/src/views/chat/components/{AgentStreamDisplay,BusinessDocumentToolResult}.vue`、`frontend/src/utils/{agent-tool-icons,business-document-display}.ts` | 文件工具流显示内部工具名和原始成功结果 | 中文名称与状态，技术字段折叠；错误仍可见，确认与下载门禁不变 |
| `docs/07-partner-delivery.md` | 交付审查不能将测试数据改名后当成正式内容 | 已知资源 ID 清单、保留范围、只读检查和现有生产配置差距 |
| `internal/application/service/partner_provisioning.go`、`internal/handler/partner_provisioning.go` | 总部和已有空间无法使用新建伙伴专用的开通路径 | 可选已有目标，双侧 Owner 和本人 Web 管理员校验；总部免跨空间组织、保持资料范围与技能校验；拒绝含糊目标字段，旧请求哈希兼容 |
| `scripts/onboard-partner.py`、`presets/workspaces/headquarters.example.json` | 已有空间需要幂等开通且不能误覆盖配置 | 默认计划；总部不改成员、不建共享；已有沙箱只读验证、已存 Agent 校验不覆盖；授权、漂移与重跑测试 |
| `scripts/audit-delivery.py` | 运行数据清理必须先列出具体对象 | 校验 Compose 项目与数据库服务，固定只读查询、脱敏输出、强验收标记与人工复核分开，无 apply 入口 |
| `docker/Dockerfile.app-boyankb`、`deployment/document-renderer/Dockerfile.dockerignore` | 交付镜像不应携带验收包或无关本机上下文 | 应用仅复制预设运行文件，渲染器上下文白名单限定代码和模板；实际镜像构建及资源核对 |
| `scripts/normalize-delivery-names.py`、`scripts/feishu-space-sync.py` | 总部及真实飞书源仍有本地验收命名 | 四个固定对象事务改名、默认计划、可逆恢复；同步兼容新旧名且保留来源标记与空间校验 |
| `scripts/onboard-partner.py`、`scripts/test_onboard_partner.py` | 不可变预设的旧追问字段与 API 序列化不同，导致幂等重跑误报 | 仅适配已禁用的固定旧配置，不改版本摘要；真实重跑相同 ID，启用追问漂移仍拒绝 |
| `frontend/src/views/{platform,chat}/index.vue`、`frontend/src/views/creatChat/creatChat.vue` | 手机宽度下聊天最小宽度导致输入框和文件卡裁切 | 仅聊天路由移动断点允许收缩、统一边距与输入框宽度；类型检查及 390px 浏览器几何和截图核对 |
| `docker-compose.delivery-local.yml` | 本机交付修复镜像需与旧生产锁分开核验 | app、frontend、渲染器使用本机固定摘要覆盖；只更新 weknora-local，云端与生产配置未更改 |

升级时检查聊天工具事件字段和技能详情响应契约，重跑状态展示测试、类型检查与构建，再用普通伙伴身份验证中文说明、真实错误、确认和下载。不要改不可变的 `skills/boyan` 或 `presets/boyan/v1` 来调整界面用语。

## 企业试用与员工邀请

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `frontend/src/api/{business-team,team-transport}.ts`、`frontend/src/composables/businessTeam*`、`frontend/src/stores/businessTeam.ts`、`frontend/src/views/team/TeamInvitations.vue` | 伙伴负责人需要邀请员工，同时保持资料只读 | 服务端能力控制入口；固定 Viewer、仅内存展示新链接、切空间清空及过期响应隔离；8 项状态及请求测试 |
| `frontend/src/components/menu.vue`、`frontend/src/router/index.ts`、`frontend/src/views/platform/index.vue`、`frontend/src/i18n/{businessTeam,locales/*}.ts` | 团队功能需要中文导航和移动端布局 | 增加 `/platform/team`，按能力显示导航；团队页允许窄屏收缩；类型检查及 13 项 i18n 检查 |
| `frontend/nginx{,-http}.conf`、`internal/middleware/logger.go`、`internal/middleware/logger_*test.go`、`scripts/{check-frontend-access-logs,test_frontend_access_logs}.py` | 邀请及资源链接含持有者令牌 | 访问日志去查询、Referer 和 User-Agent，资源短链路径固定脱敏，响应邀请 URL 脱敏；关闭可能泄漏原始请求的 Nginx 错误日志；保留状态码访问日志和健康检查，失败排障见备份文档 |
| `scripts/{trial_backup,backup-trial,restore-trial,verify-trial,prepare-trial-admin,configure-trial-auth}.py` 及对应测试 | 清理旧账号必须可恢复且限定具体对象 | 加密备份、隔离恢复、源容器和审计摘要绑定、新管理员先验证、单事务退役旧账号、保留源资料；认证设置读回；21 项交接测试及真实演练 |
| `scripts/{trial-cloudflare,run-trial-tunnel}.py` 及对应测试、`deployment/trial/*`、`docker-compose.trial.yml` | 未购买服务器时提供受控 HTTPS 试用 | 专用隧道、单主机 DNS 检查、令牌仅本机进程环境、PID 身份核对、固定网关与最小端口；42 项隧道离线测试 |
| `deployment/trial/{Dockerfile.gateway,Caddyfile,images.lock.json,frontend-entrypoint.sh}`、`docker-compose.trial.yml` | 本机 Docker Desktop 的端口发布和非 root 网关需要实际适配 | 移除 Caddy 低端口文件 capability，维持能力全移除及只读文件系统；独立内网与仅网关 edge 网络，三端口仅 loopback，全部运行镜像固定摘要；8 服务健康 |
| `scripts/{acceptance-trial,check-trial-gateway,test_acceptance_trial}.py`、`docs/{08-enterprise-trial,trial-acceptance,trial-tunnel}.md` | 账号交接后需要验证真实授权、文件确认和公网路径 | 默认只读检查，显式业务演练；原会话最多两次追加正式提交，审校阻断不绕过；公网 26 项账号与 6 项网关检查、真实问答和 Word 下载通过，Word 原生打开导出 3 页 |
| `scripts/{feishu-space-sync,test_feishu_space_sync}.py`、`docs/02-feishu.md` | 既有摘要配置漂移不应阻止只读核对，也不能悄悄扩大导入处理 | `verify/snapshot` 报告摘要配置及 `sync_allowed=false`；`sync` 继续拒绝非空摘要模型，其他来源和索引保护不变；29 项模拟测试通过，未更改运行配置 |
| `internal/types/partner_provisioning.go`、`internal/application/service/partner_provisioning.go`、`internal/businessworkflow/reviewer.go`、`scripts/onboard-partner.py` 及对应测试 | 一份企业知识库应支持所有已授权伙伴，无需额外工作库 | 可选 `shared_enterprise`，固定单源、伙伴只读共享、三个 Agent 绑定同源、旧清单哈希兼容、禁止转移源库创建者 |
| `internal/{teaminvitation,businessworkflow/team*,handler/business_team*}`、`internal/router/routes_business_workflow.go`、`internal/{types,application/repository,application/service}/tenant_invitation.go`、`internal/handler/tenant_invitation.go`、`migrations/{versioned,sqlite}/000113_team_invitations.*.sql` 及对应测试 | Viewer 负责人需要有限邀请权，不能提升为空间 Owner | 独立负责人绑定、只发本空间 Viewer、接受时复核撤权、链接仅创建响应返回、Owner 旧流程兼容；定向 Go 包及 SQLite 迁移往返通过 |

团队入口升级时同时验证后端 `available` 与请求空间绑定，禁止用单纯前端角色判断代替授权。操作方式及人工界面检查见 `docs/trial-team.md`。

## 登录布局恢复

日期：2026-10-10。分支：`feat/login-layout-restore`。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `frontend/src/views/auth/Login.vue` | 紧凑字段间距使绝对定位的红色校验提示被下一项遮住 | 恢复压缩前的布局间距、默认字段尺寸与纵向说明；登录及邀请注册提示参与自然布局并允许换行；匿名浏览器核对错误边界、短屏滚动及长文案 |
| `docs/09-login-layout.md` | 需要解释总部账号的伙伴入口和记录布局修复 | 记录真实 Owner 成员关系、开通代码及私人内容边界；本轮不更改授权 |
| `docker-compose.trial.yml`、`deployment/trial/images.lock.json`、`docs/08-enterprise-trial.md` | 试用站应运行修复后的前端且可以回滚 | 新增 `v0.8.2-trial-login-v2` 镜像，更新前端摘要，旧版保留；仅重建本机 frontend |

升级 TDesign 后核对 `.t-form__controls > .t-input__extra` 是否仍为校验提示节点，验证错误状态而非仅检查空白登录页。2026-10-08 的紧凑布局记录属于历史验收，本次恢复覆盖其中的压缩间距。

## 试用访问恢复与静态缓存

日期：2026-10-10。分支：`feat/trial-access-recovery`。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `deployment/trial/Caddyfile` | 网关统一禁止缓存，使重复访问也需下载所有前端资源 | 增加仅匹配 `GET/HEAD /assets/*` 的同级代理，保留 Nginx 带版本号资源的缓存头；原默认代理继续 `private, no-store`；Caddy 配置及路由排序核验、14 项实际响应头检查、公网 6 项门禁检查通过 |
| `docs/{trial-tunnel,08-enterprise-trial}.md` | 连接器停止导致公网中断，需要准确的恢复与验证范围 | 记录实际重新启动、正确 `.xyz` 入口、缓存边界、退出原因未验证及缺少自动启动/重启；修正旧版文档中全部公网流程未验证的过时说明 |

升级时确认 `/assets/*` 仍仅承载带内容哈希的公开前端文件。保留拒绝路由先于代理的执行顺序；不要直接替换为排序在 `respond` 之前的 `handle`。运行 Caddy 配置校验、`scripts/check-trial-gateway.py` 并检查实际响应头。此次未更换镜像摘要、未修改生产配置，也未更改隧道启动器。

## 技能目录、知识图谱与中文高级提示词

日期：2026-10-10。分支：`feat/skill-graph-prompt-fixes`。保留现有账号、成员关系、原生角色与不可变业务预设；本轮本机覆盖不修改生产配置。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `branding/brand.json`、`frontend/src/config/deploymentCapabilities{,.test}.ts`、`frontend/src/config/toolbox.test.ts` | 无沙箱部署也需要管理员保存和查看原生技能包 | 目录入口与沙箱设置分开，`nativeSkillExecution=false` 只控制界面模式；新增契约测试，不作为后端执行授权 |
| `frontend/src/views/settings/SkillSettings.vue`、`SkillSettings.test.ts`、`frontend/src/i18n/skillCatalog.ts`、`frontend/src/i18n/locales/{zh-CN,en-US,ja-JP,ko-KR,ru-RU}.ts` | 原页面因沙箱接口失败而无法显示目录，保存又容易误认成安装 | 目录和沙箱分别加载，错误可重试；当前仅保存步骤，不请求安装或沙箱接口；业务预设删除入口隐藏，中文提示明确保存不等于可执行 |
| `deployment/trial/Caddyfile`、`scripts/check-trial-gateway.py` | 公网原先把技能目录一并拦截 | 精确允许 catalog、指定项及文件读取路径进入原后端鉴权；其余原生技能执行和沙箱路由仍拦截；需要部署后的角色与路由验收 |
| `internal/application/repository/tenant_skill.go`、`tenant_skill_catalog_preset_test.go`、`internal/application/service/tenant_skill_catalog.go`、`tenant_skill_catalog_preset_test.go`、`tenant_skill_catalog_test.go`、`tenant_skill_install_test.go`、`tenant_skill_reaper_test.go` | 管理员普通上传或删除不能破坏已绑定的九项业务预设 | catalog 变更事务锁定开通记录，按名称和 catalog ID 检查预设引用；拒绝删除及内容漂移，完全相同包允许幂等登记；新增冲突、同包及事务测试 |
| `frontend/src/views/knowledge/settings/GraphSettings.vue`、`graphAvailability{,.test}.ts`、五份主语言文件 | 请求失败与未启用不应混同，旧响应不能覆盖重试结果 | 区分加载、启用、关闭、错误状态，支持重试并丢弃过期响应；未确认可用时维持禁用 |
| `docker-compose.graph-local.yml`、`.env.example` | 本机原栈缺少图数据库 | 独立 Neo4j 数据/日志卷、仅 app 与数据库加入内部网络、无宿主发布端口、锁定镜像摘要和资源限额；APOC core 过程白名单，凭据来自 `BOYAN_NEO4J_PASSWORD`；不代表图数据或生产容量已验收 |
| `internal/application/service/graph_backfill{,_test}.go`、`extract.go`、`internal/handler/graph_backfill.go`、`internal/router/routes_graph_backfill.go`、`router.go`、`internal/container/container.go`、`internal/types/task.go` | 普通重新解析会替换分块，已有文档需要单独补建图谱 | 新增本空间 Owner/Admin Web 身份的计划与提交 API；绑定来源、配置、模型及计划摘要；复用现有分块抽取队列，执行及写图前复验身份与来源，不走重新解析及旧任务完成计数；确定性任务 ID、30 天队列保留，拒绝过期计划 |
| `internal/application/service/chat_pipeline/search_entity.go`、`search_parallel.go` 及共享图谱检索测试 | 伙伴检索总部共享库时不能按伙伴租户查总部图谱分块，也不能扩大资料范围 | 从服务端 KB 记录取得源租户，按当前调用者检查共享权限与 SearchTargets；过滤文件、分块所属库/租户和启用状态，返回前重新授权；定向及完整 chat_pipeline 包测试通过，真实业务效果单独验收 |
| `scripts/backfill-enterprise-graph.py`、`scripts/check-skill-graph.py` | 补图要先审查范围，运行状态不能靠开关推断 | 总部固定企业库，默认只读计划，显式 `--apply --expected-plan-hash` 提交；角色、目录及服务状态检查；真实抽取结果由对应部署验收记录补充 |
| `branding/prompt_templates/{context_template,rewrite,fallback,intent_prompts}.yaml`、`docker/Dockerfile.app-boyankb`、`internal/config/branding_prompt_templates_test.go` | 高级上下文、改写、兜底与意图提示词仍主要显示英文 | 新增 19 个中文覆盖，与原两份共 32 个模板；保留 ID、变量、枚举、JSON 与引用协议；三个固定兜底直接返回中文回复；配置/品牌 Go 包测试通过，不改已保存自定义提示词或 `presets/boyan/v1` |
| `scripts/feishu-space-sync.py`、`scripts/test_feishu_space_sync.py` | 摘要及图谱启用后策略不再匹配，登录默认空间又可能是伙伴空间 | 显式 `--enrichment-policy` 绑定目标空间、既有总部库、摘要模型与完整抽取配置；`--tenant-id` 仅为本机请求选择空间，沿用服务端权限验证，403 不回退且不修改活动空间；保留其他增强、删除及空间保护 |
| `scripts/graph-trial-backup.py`、`scripts/test_graph_trial_backup.py` | 当前网关加 Neo4j 的九服务栈不能沿用七服务整栈备份证明 | 独立在线 PG/环境/KB 配置快照与 Neo4j 冷备格式；同镜像、新隔离卷恢复及离线一致性检查；禁镜像目录预填、固定隔离主机名，仅可信检查工具临时目录允许原生库加载；14 项新增、15 项旧备份测试通过，旧脚本保护不变 |
| `docs/graph-partial-backup.md`、`docs/trial-backup.md`、`docs/02-feishu.md`、`docs/12-skill-graph-prompts.md` | 运行、同步和备份的范围需保持一致 | 区分历史导入结果、显式增强策略、在线局部快照及已验证图谱冷备/离线恢复；不把单库检查作为全栈恢复 |

已实际完成在线 PG custom dump、`.env` 与指定 KB 配置快照，通过 age 解密认证、组件摘要及 `pg_restore --list`；没有恢复原库。Neo4j 冷备 `neo4j-cold-20261010a.age` 已恢复至全新 `boyankb-restore-graph-20261010c`，文件清单及 `neo4j`、`system` 两库离线一致性检查全部通过；报告为 `temp/graph-backup/neo4j-restore-20261010c.json`。a、b 失败目标卷保留，源服务及原卷未被恢复工具修改。此次不启动恢复 DBMS/应用，九服务整站恢复仍为**未验证**。图谱 API、最终镜像和真实抽取结果以 [本轮验收](12-skill-graph-prompts.md) 为准。

升级时核对原生技能路由、开通记录锁、抽取队列和工作流版本边界，保留六份提示词 COPY。不要修改已安装 v1 指南摘要来做界面翻译，不要移除七服务备份保护来迁就新增服务。当前备份命令与维护顺序见 [局部快照与图谱冷备](graph-partial-backup.md)。

## 腾讯云独立内部测试部署

日期：2026-10-10。分支：`feat/tencent-cloud-deployment`。

| 文件 | 原因 | 改动点与验证 |
| --- | --- | --- |
| `docker-compose.tencent.yml`、`deployment/tencent/{Caddyfile,frontend-entrypoint.sh,redis-entrypoint.sh,env.example,images.lock.json,validate-config.py}` | 将九服务和本地模型迁到4核8GB独立云主机，保留本机回退 | 独立项目和网络、回环入口、九镜像固定摘要、七数据卷、资源和任务并发限制；静态配置检查通过，实际数据与业务验收另记 |
| `docker-compose.tencent-ip-https.yml`、`deployment/tencent/{Caddyfile.ip-https,validate-ip-https.py}` | 无域名内部测试也需要加密登录与可配置来源 | LE IP短期证书、仅TCP443公网发布、环境变量控制HTTP来源、专用证书卷与手动初始化；初始白名单已按用户要求通过云端环境覆盖为所有来源，邀请制及后端授权保留 |
| `scripts/{init-tencent-access,tencent-ssh,tencent-images,tencent-transfer}.py`、对应测试、`deployment/tencent/install-host.sh` | SSH信任、镜像版本与大文件传输必须可核验 | 专用公钥、控制台指纹固定、严格主机校验；镜像receipt、私有路径白名单、二进制断点续传与远端SHA256；不传输SSH私钥或Cloudflare令牌 |
| `scripts/{tencent-snapshot,tencent-restore}.py`、对应测试 | 旧七服务备份不能覆盖当前Redis、Neo4j与模型缓存 | 新格式一致性快照、新云卷恢复、逐文件库存和数据摘要、失败保留现场；原备份保护不修改，真实整栈恢复另记 |
| `scripts/tencent-accounts.py`、对应测试 | 只替换云端两个测试账号，不能转移旧个人聊天 | 新UUID、新账号先登录验证、事务内移交总部资源、旧账号软退役与撤销令牌；计划摘要和有效备份门禁；空间频道历史保留 |
| `scripts/tencent-acceptance.py`、对应测试 | 云端启动健康不等于账号、问答和文件可用 | 两身份与九技能检查、真实引用及任务绑定、大纲确认和审校后生成Word/PPT；Office目视和事实核对明确人工验收 |
| `scripts/tencent-runtime.py`、对应测试 | 环境开关不能证明图谱已恢复或并发限制生效 | 只读核对九服务、固定镜像、端口、图谱实体/关系/分块范围、APOC能力与DB并发覆盖；不修改运行配置，不等同实际工作池容量验收 |
| `deployment/tencent/README.md`、`docs/13-tencent-deployment.md` | 区分准备、实际恢复与公网验收 | 命令、凭据位置、访问边界、资源限制和回退顺序；实际结果随部署更新 |

镜像导出保留原 OCI 索引，不向 `docker image save` 传递 `--platform`；源端仍核验 Linux/amd64。`scripts/test_tencent_images.py` 验证原索引归档、错误源镜像拒绝和已有归档保护。目标机必须同时验证 tag 与锁定摘要引用，不能以平台摘要差异为由跳过版本检查。

首次云端启动修复：`redis-entrypoint.sh` 先以gosu切换redis身份，避免受限root遍历恢复的0700目录；无网临时容器的认证、权限与AOF测试通过。`tencent-restore.py` 仅对两份YAML和两份Caddy配置设置root所有、服务组可读的0640，保持环境文件0600，拒绝软硬链接；25项恢复测试通过。九服务及HTTPS已上线，用户要求暂缓业务验收；附加图谱解析检查仍未通过，详见部署记录。

2026-10-10 网页来源调整，分支 `feat/tencent-public-web-access`：按用户要求，仅将云端 `/opt/boyankb/.env.tencent` 的 `WEB_ALLOWED_CIDRS` 覆盖为 `"0.0.0.0/0 ::/0"` 并重建 gateway。新增 `scripts/tencent-web-access.py`，默认预览，显式应用时私有备份、拒绝重复键、比较有效Compose仅有目标变量变化，并核对其他八个容器ID、端口和健康状态。登录页向所有来源开放，公开注册关闭、登录与RBAC、SSH来源限制、数据库无宿主端口等规则保持原配置。仓库的网关规则和Compose默认值未改；重新部署必须带实际环境文件。设置、白名单回退及200/401/403/404和端口检查命令见 `deployment/tencent/README.md`，变更后实际检查结果以部署记录为准，旧白名单拒绝报告仅为历史记录。

升级时先在隔离测试环境构建新定制镜像，更新本配置的镜像锁与兼容性检查，再演练新快照格式恢复和账号权限。不能直接将七服务生产模板覆盖到本九服务部署，不得把数据恢复通过写成全部业务验收通过。公网入口、账号和数据以 [腾讯云部署记录](13-tencent-deployment.md) 为准。

## 旧合作伙伴空间退役

日期：2026-10-12。分支：`feat/retire-legacy-partner`。

`scripts/tencent-retire-legacy-partner.py` 针对用户指定的星辰教育旧案例，默认只读，显式应用时备份 PostgreSQL 并校验目录和摘要，先回滚演练再在持锁事务内软删除固定的工作空间、共享空间与单一知识库共享。无成员、有效邀请、API Key、发布渠道及运行任务才可执行；总部资料和全部会话、资源等保护指纹必须一致。没有修改原生 RBAC、技能执行路径、页面或同步策略。十三项安全门禁测试及云端两身份列表检查通过。

文件包括 `scripts/test_tencent_retire_legacy_partner.py`、`docs/15-skills-sharing-sync-review.md` 及部署记录更新。升级后需重新核对相关表结构、组织软删除及成员模型，不能直接扩展固定 ID 脚本清理其他合作伙伴。保留的测试文档、总部测试会话、历史记录和复核步骤见 [核查记录](15-skills-sharing-sync-review.md)。

## 原生技能入口统一与云端自动同步

日期：2026-10-12。分支：`feat/unify-skills-cloud-sync`。前述保留测试内容和暂停同步的决定已按用户新要求更新，当前结果见 [实施与验证记录](16-skills-and-cloud-sync.md)。

| 文件 | 原因与改动 | 验证 |
| --- | --- | --- |
| `frontend/src/views/settings/SkillSettings.vue`、`frontend/src/i18n/{skillCatalog.ts,businessSkills.ts,locales/zh-CN.ts}` | 九项业务技能并入原生目录，增加中文用途、开通状态、使用说明及已授权智能体入口，操作按钮统一为“开始使用”；成员可使用，管理操作检查角色 | 角色、目录与受管预设测试，云端两身份 API 与真实浏览器检查 |
| `frontend/src/config/{toolbox.ts,settingsAccess.ts}`、`frontend/src/views/toolbox/Toolbox.vue`、`frontend/src/router/index.ts` | 删除重复业务导航；旧地址兼容跳转；空间与角色变化重新加载，隔离迟到响应 | 导航、角色和空间切换测试 |
| `frontend/src/views/toolbox/BusinessSkills.vue` | 删除独立页面，避免维护两套目录 | 旧入口自动跳转，九项技能各显示一次 |
| 上述对应测试及 `SkillSettings.business-skills.test.ts` | 防止成员误调用管理 API、跨空间展示旧说明或业务技能入口失效 | 36 项针对性检查与类型检查通过；构建附带回归通过 |
| `scripts/tencent-feishu-schedule.py`、`scripts/test_tencent_feishu_schedule.py` | 固定核对现有总部数据源、知识库和飞书空间；通过原生 API 启用或暂停六字段定时任务，不重写凭据或扩大同步范围 | 12 项测试、云端配置摘要保持不变、增量同步完成；自动触发证据见实施记录 |
| `scripts/tencent-update-frontend.py`、`scripts/test_tencent_update_frontend.py` | 仅替换指定前端镜像；部署标记、镜像摘要、原配置备份、同项目互斥、失败回滚与端口/健康校验 | 20 项测试；九服务健康，另外八项容器未替换 |
| `docker-compose.tencent.yml`、`deployment/tencent/images.lock.json` | 锁定 `v0.8.2-skills-unified-v2` 前端的 OCI 索引摘要 | 两份部署静态校验通过；云端 tag 与摘要一致 |
| `docs/{02-feishu,13-tencent-deployment,15-skills-sharing-sync-review,16-skills-and-cloud-sync}.md`、`deployment/tencent/README.md` | 更新技能权限、18 篇正式资料、中文知识库描述、云端半小时同步及本机暂停的实际状态 | 提供运行报告、复核和回滚命令，历史导入结果单独标记 |

后端 RBAC 和通用沙箱开关未修改；九项受管业务技能仍通过版本化预设流程维护。管理员使用原生 ZIP 上传或来源导入其他技能，不代表任意包具备执行环境。LICENSE 和上游版权声明保留。

升级时先合入上游原生技能目录与权限变化，再重新应用目录展示、旧入口跳转及受管业务技能说明；运行角色与空间切换测试，以成员身份确认目录可读、管理 API 拒绝。构建前端后通过发布脚本更新固定摘要，保留上一个镜像。数据源配置仍在数据库，升级应用后需核对 `active`、六字段 cron、唯一目标空间及实际计划同步日志；不要重启本机同步作为云端验证手段。

## 私有仓库节点整理

日期：2026-10-12。分支：`feat/checkpoint-20261012`。合并云端技能同步与 RustDesk 配套分支，保留两个父分支历史，未改变运行中的服务。纳入原有 `AGENTS.md`、仓库分析与业务规格；新增 `README.BoyanKB.md`、`docs/17-checkpoint-20261012.md`、`docs/18-upstream-contributions.md`，在原 README 添加定制版入口。`.gitignore` 补充私有备份与常见私钥文件名保护。

私有备份远程为 `origin`，原公开 Fork 改名 `fork`，官方 `upstream` 保留。代码节点、运行数据备份及上游贡献各自说明范围；镜像和数据库没有因 Git 节点整理而重新构建、迁移或发布。上游贡献应从最新官方分支提取最小补丁，不能公开本企业定制历史。

## 上游知识图谱状态修复准备

日期：2026-10-12。私有记录分支：`feat/upstream-graph-pr-notes`。新增 `docs/19-upstream-graph-pr.md`，登记独立官方贡献分支 `feat/graph-availability-state` 的修复、测试、截图与 PR 预填材料。通用修复只包含图谱状态组件、组件回归测试和六语言提示，未改企业定制运行代码。

贡献提交 `0400fe0c230dc8fadb2dacd5a1fb9fe547f35690` 直接基于官方 `e5f9f9deab36898676fe85a9fda6b3e6f00288fa`，不携带 BoyanKB 历史、品牌或企业配置。验证命令及已执行检查见上述文档；用户确认后已提交 [官方 PR #4172](https://github.com/Tencent/WeKnora/pull/4172)，前端 CI 等待维护者批准运行。后续升级优先采用官方合入版本，再删除或调整定制版对应状态逻辑，避免重复实现。

2026-10-16 补充：原提交的官方检查随后已全部成功。因上游 #4193 重做图谱提示样式，原贡献分支合入最新主干并将四态逻辑接到新卡片，调整同一组件测试；改动仍限公开 PR 的八个原文件。冲突处理、验证与远端状态见 [上游 PR 记录](19-upstream-graph-pr.md#2026-10-16-主干冲突处理)。未变更企业运行服务。

## 快速问答故障提示与结束

日期：2026-10-15。分支：`feat/qa-failure-completion`。原因、验证和发布记录见 [异常结束处理](21-qa-failure-completion.md)。本问题涉及服务生命周期，无法仅靠配置或品牌覆盖文件修复，因此采用必要核心补丁。

| 文件 | 改动 |
| --- | --- |
| `internal/application/service/chat_pipeline/{chat_completion_stream,query_understand,progress}.go` 及测试 | 流同步返回故障、正常结束释放连接；同主模型改写 429／5xx 立即结束；步骤错误不回传供应商原文 |
| `internal/application/service/session_knowledge_qa.go` 及 fallback 测试 | 模型兜底同样遵守同步终止、取消、异常 EOF 契约 |
| `internal/handler/session/{qa,quick_answer_failure,agent_stream_handler,stream,helpers}.go` 及测试 | 快速问答 120 秒总期限；统一持久化失败、脱敏提示、SSE 结束；避免失败／停止触发问答索引与推荐 |
| `internal/types/agent.go` | 既有 AgentStep JSON 增加可选 error_code／error_message，不新增 SQL 迁移 |
| `frontend/src/composables/useChatStreamHandler.ts`、`frontend/src/views/chat/{index.vue,components/RagPipelineProgress.vue,components/AgentStreamDisplay.vue}` 及测试 | 错误横幅、停止等待、部分内容保留、刷新恢复、失败不请求追问建议 |
| `frontend/src/i18n/{locales,embed.ts}` 及测试 | 现有五语言和嵌入组件固定故障文案 |
| `scripts/tencent-update-frontend.py` 及测试 | 兼容原前端发布，新增可选 app 镜像联合备份、校验及回滚 |
| `docker-compose.tencent.yml`、`deployment/tencent/images.lock.json` | 锁定已验证的配套后端、前端镜像摘要 |
| `deployment/tencent/README.md`、`docs/{13-tencent-deployment,21-qa-failure-completion}.md` | 记录联合更新、回滚、故障回归及真实云端验证 |

升级时优先对照上游相同修复，重新执行普通流、模型兜底、503、超时、异常 EOF、正常完成和主动停止的回归，再构建发布。LICENSE、版权、RBAC、模型凭据和数据源范围不变。

## 2026-11-18 企业资料与快速问答

`scripts/tencent-feishu-spaces.py` 将指定的飞书 Wiki 空间逐一预检、绑定同一总部知识库并核对导入结果。`branding/prompt_templates/system_prompt.yaml` 和 `fallback.yaml` 为快速问答补充证据不全时的条件推理、公司事实边界和负责人确认提示。升级时检查单空间保护、三个来源的增量游标、模板占位符和线上智能体的独立配置，并复跑有依据、部分有依据与无依据三类问题。同步命令、读写范围、现网回读、备份以及资料缺口见[三空间知识与问答核验](22-feishu-knowledge-review.md)。

### 飞书原生幻灯片和上传的 PowerPoint

| 文件 | 改动原因和升级核验 |
| --- | --- |
| `internal/datasource/connector/feishu/core/{client,slides}.go`、`wiki/{connector,slides}.go` 及同目录测试 | v0.8.2 跳过原生演示文稿。对已选 Wiki 空间中的原始节点校验阅读与导出权限，调用飞书只读接口，按幻灯片页序同步可选文字。升级时核对 Wiki 来源、存量游标首次补入、新增修改、权限拒绝、无可选文字和源删保留。 |
| `internal/infrastructure/docparser/anydoc/{backend_cgo,pptx_text}.go` 及同目录测试 | 含大量媒体的 PPTX 可能因嵌入资源总量超限而失去可检索的文字。在指定限额错误下从 OOXML 提取文字并保留页序。升级时复跑正常演示稿、超限、无可选文字和损坏文件的用例。 |
| `scripts/tencent-feishu-spaces.py`、`scripts/test_tencent_feishu_spaces.py` | 对账逐项核对来源节点类型、权限和云端解析结果。遇到其他不支持的格式时列出节点，同一空间的受支持文档可以继续处理。 |
| `internal/datasource/connector/feishu/wiki/{slides,connector}.go`、`slides_test.go`、`connector_test.go` | 原生稿整份只有空白页时，记录来源链接和 `source_empty=true`。升级后验证空白、无修改、同一节点新增文字这三个连续状态。图片稿与损坏文件仍须分别处理。 |
| `internal/infrastructure/docparser/anydoc/{backend_cgo,docx_text}.go`、`docx_text_test.go`、`convert_linked_test.go` | 大体积 Word 教学案例的内嵌图片触发 `max_asset_total_bytes`。遇到该错误时在内存中保留必要的 OOXML 文本部件，交给已有的 Word 解析器。单部件限 8 MiB，总量限 32 MiB，原件仍可下载。升级时用链接 Rust 的 AnyDoc 测试和真实案例中的课程短句核验。 |
| `docker-compose.tencent.yml`、`deployment/tencent/images.lock.json` | 2026-11-24 按生产发布报告锁定经验证的后端镜像 `v0.8.2-feishu-slides-371e9ce15`。镜像摘要、回滚备份和三空间逐项对账结果见[云端发布记录](22-feishu-knowledge-review.md)。 |
