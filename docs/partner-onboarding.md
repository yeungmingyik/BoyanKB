# 合作伙伴空间开通

给已有空间开通时设置 `target_tenant_id`，执行者还必须是目标空间的 active Owner。总部设置 `target_tenant_id == hq_tenant_id`、`members: []`，不传 `contributor_email`，不新建账号、组织或共享；示例见 `presets/workspaces/headquarters.example.json`。已有空间必须事先禁用代码沙箱；脚本只验证现有策略，不覆盖它。已有智能体配置有差异时停止，不静默重置。总部开通实例及验证见 [合作伙伴交付审查](07-partner-delivery.md)。

本流程为每个合作伙伴创建独立工作空间，由总部操作员保留 Owner；伙伴账号仅分配 Viewer 或 Contributor。总部与每个伙伴分别建立一个只有两个成员空间的组织，总部知识库以 viewer 权限共享，其他伙伴名称和资料不进入这个组织。

执行者必须同时持有有效用户 JWT、SystemAdmin 身份和总部空间的 active Owner 成员资格。接口直接复核数据库权限，不依赖 RBAC 是否开启。普通管理员、Contributor、Viewer、空间 API Key 和平台 API Key 均不能调用开通接口。

## 前置条件

- 部署包含 `000111_partner_provisioning` 迁移、受限开通接口、`internal/boyanskills` 和固定文档工作流。
- 操作员已有总部 Owner 与 SystemAdmin 权限，`can_access_all_tenants=false`。
- 保持关闭公开注册、自助创建空间及通用脚本执行；本脚本不调整这些全局配置。
- 伙伴成员为已经注册的普通账号。开通脚本不会生成账号密码，不会重置密码或提升系统权限。
- 总部知识库 ID 必须明确指定且属于总部空间；本项目已授权的飞书知识库为 `d9285ed1-0760-474b-ba82-a13347598176`，总部 tenant 为 `10000`。实际执行前仍通过 API 核实。
- 总部与伙伴资料库使用清单中相同的 embedding 模型；默认 `local-embedding`，问答模型为 `local-deepseek-chat`。
- 问答、embedding 及非空 rerank ID 必须在总部与目标空间均可用；只检查真实模型类型和 active 状态，不覆盖已有模型名称、服务地址或凭据。
- 远程部署必须使用 HTTPS，本地 CA 通过 `--ca-file` 传入，不提供跳过证书验证选项。本机明文仅允许 `http://127.0.0.1:<port>`，不接受其他 HTTP 主机；所有请求禁用环境代理和重定向。

新账号需要先完成受控开户和身份交付。现有 `POST /tenants/:id/invitations` 会按邮箱绑定已注册用户；`POST /tenants/:id/invite-links` 与 `/auth/register-by-invite` 是不绑定邮箱、可重复使用的持有链接即可加入流程。该流程不能替代邮箱白名单或一次性邮箱验证，本开通脚本不使用它。

## 配置清单

以下邮箱为示例，实际使用前替换为已有伙伴账号。A、B 等伙伴验收必须使用不同账号，不能拿同时拥有两个空间的总部操作员代替伙伴进行越权测试。

```json
{
  "schema_version": 1,
  "partner_key": "partner-example",
  "name": "示例合作伙伴",
  "description": "合作伙伴业务工作空间",
  "hq_tenant_id": 10000,
  "hq_knowledge_base_ids": [
    "d9285ed1-0760-474b-ba82-a13347598176"
  ],
  "preset": "presets/boyan/v1/manifest.json",
  "models": {
    "chat": "local-deepseek-chat",
    "embedding": "local-embedding",
    "rerank": ""
  },
  "members": [
    {"email": "contributor@example.test", "role": "contributor"},
    {"email": "viewer@example.test", "role": "viewer"}
  ],
  "contributor_email": "contributor@example.test"
}
```

`partner_key` 为 3–64 位小写字母、数字、下划线或短横线，首字符为字母或数字，作为全局唯一业务键。`contributor_email` 必须是成员列表中的 Contributor；资料库创建后会通过受控 action 将 `creator_id` 赋给这个已激活成员。它不会修改其他知识库的归属。其他 Contributor 可创建自己的知识库；需要纳入既有 Agent 的检索范围时，由管理员明确调整。

预设清单路径从仓库根目录解析，Agent JSON、提示词和 Skill 路径从预设清单所在目录解析。加载器检查路径、9 个固定 Skill 名称、文件摘要、模板摘要及工具白名单。Agent 的 `@hq`、`@partner`、模型占位符在目标空间展开。

## 执行

默认命令只读取本地清单、预设和模板，计算计划摘要，不读取凭据文件、不登录、不调用 API。

```powershell
python -B scripts/onboard-partner.py --manifest temp/partner-example.json
```

凭据从当前进程环境或 `--env-file` 指定文件读取，仅识别 `ONBOARD_ADMIN_EMAIL`、`ONBOARD_ADMIN_PASSWORD`、`ONBOARD_ADMIN_TOKEN`；环境变量优先。提供 Token 时不进行密码登录。不要将实际值写入命令行、版本库、清单或报告。

```powershell
python -B scripts/onboard-partner.py --manifest temp/partner-example.json --apply --base-url https://localhost:8443 --ca-file temp/production-staging/ca.crt --env-file temp/onboard.env --report temp/partner-example-result.json
```

本机直接访问应用时使用 `--base-url http://127.0.0.1:8080` 并省略 `--ca-file`。不要把此明文入口暴露到外网。

Linux 可以使用 `bash scripts/onboard-partner.sh` 传入相同参数；Windows 使用 PowerShell 7 执行 Python 命令。

加上 `--apply` 后依次执行：

1. 复核操作员身份、总部 Owner、总部 KB 归属与 embedding 绑定。
2. 事务内创建 tenant、调用者 Owner、默认存储、禁用通用脚本策略及开通任务记录；任一步失败全部回滚。
3. 添加已有 Viewer/Contributor 账号；已有成员角色不符时停止，不自动降级或提权。
4. 创建仅总部与当前伙伴参与的组织，关闭发现、要求加入审批、限制两个成员空间；以 viewer 分享总部 KB。
5. 创建专用资料库，关闭远程摘要、问题生成、Wiki 和图谱，指定 Contributor 为创建者。
6. 注册九个标准 Skill ZIP 包到当前空间 catalog，核实固定指南版本与正文，登记受限安装索引。
7. 创建或复用业务导师、落地方案官、内容审校官，映射本空间模型及授权 KB；不绑定 sandbox 或 MCP。
8. 读回资料库归属、共享权限、Agent 配置与脚本策略，等待固定渲染器和模板 readiness，再标记任务 ready。

`--timeout` 默认 300 秒，范围 0–3600，控制渲染 readiness 等待。成功退出 0，配置、权限、资源状态或连接失败退出 2。输出与可选报告仅含非秘密 ID、摘要和状态，不输出密码、JWT、完整 API 错误正文或邀请链接。

## 幂等与恢复

服务端对 `partner_key` 唯一约束，并保存规范化请求摘要。相同键与摘要返回既有空间；同键不同定义返回 409。客户端摘要覆盖开通清单、Agent JSON、提示词、Skill 包、模板清单和模板文件，避免版本变化被误当成相同请求。

`partner_provisioning_jobs` 持久化租户、总部 Owner、组织、资料库、Agent、catalog 及受限 Skill 索引。资源 ID 绑定后不可随意替换。五分钟租约与 revision 检查阻止两个配置进程同时写同一任务，脚本运行中续租。异常退出后可等待租约到期，再用完全相同的清单重跑。

资料库使用由 `partner_key` 派生的稳定 UUID。Agent 创建后若响应丢失，重跑会在本空间根据固定名称及总部创建者复核已有 Agent；出现多个匹配项时停止，避免误改其他资源。组织使用固定名称并核实总部归属；组织已经包含其他伙伴时停止。Skill catalog 根据名称及确定性包摘要复用。

网络写入结果不明时不会盲目重发同一个创建请求。重跑先读取任务和资源，再继续。任务失败标记为 blocked，保留已完成资源用于恢复，不自动删除空间、成员、知识库或文件。升级模板或改变成员定义会触发摘要冲突，需要独立的受控升级流程；不要使用新业务键来掩盖同一伙伴的升级。

## 受限 Skill 与运行就绪

九个 Skill 的 catalog 记录用于标准包登记与核对。原生 sandbox 安装接口不会被调用，也不会写入伪造的 `tenant_skills.ready` 或镜像 snapshot。受限安装 action 校验镜像内固定名称、版本和指南正文，保存 `version`、指南 `sha256`、`catalog_id`、ZIP `catalog_sha256` 与 `enabled`。

Agent 使用 `skills_selection_mode:none` 和空 `sandbox_config_id`。落地方案官通过固定 `boyan_document` 工具的 `read_skill` 读取已登记的指南，生成文件走受控 renderer；不开放 shell、任意 Python、任意路径读取、MCP 或网页抓取。

三个受管 Agent 显式设置 `boyan_hybrid_search_without_rerank:true`。`models.rerank` 可以为空，使用混合检索的 RRF 融合与 MMR；仅任务 ready 且租户、创建者和 Agent 绑定均通过服务端校验时生效。其他 Agent 的重排要求不变。配置非空 rerank 时仍使用实际重排模型，不能填写虚构 ID 绕过预检。

`GET /api/v1/business-workflow/readiness` 必须报告模板版本 `boyan-v1`、模板完整性通过、九个固定指南可用，以及 docx/pptx/xlsx/md 四种格式。ready 表示开通资源与依赖检查完成，不证明所有回答正确或文件排版完美。

## API 与权限

所有开通 API 使用总部空间 `X-Tenant-ID` 和操作员 JWT。其余资源 API 使用总部或伙伴空间的对应上下文。

| 方法与路径 | 用途 |
|---|---|
| `POST /api/v1/system/admin/partner-provisioning` | 幂等事务创建基础资源 |
| `GET /api/v1/system/admin/partner-provisioning/:key` | 查询进度及非秘密资源映射 |
| `POST /api/v1/system/admin/partner-provisioning/:key/lease` | 获取、续期或释放配置租约 |
| `PATCH /api/v1/system/admin/partner-provisioning/:key` | 带 revision 保存已验证资源映射与状态 |
| `POST /api/v1/system/admin/partner-provisioning/:key/knowledge-creator` | 将本任务专用资料库归属给当前空间已激活 Contributor |
| `POST /api/v1/system/admin/partner-provisioning/:key/skills/install` | 校验并登记九个受限指南 |

总部 KB 的共享权限和接收空间组织角色均为 viewer。伙伴本地 Agent 可以直接引用共享总部 KB 的 ID；检索服务会按原 KB 所属空间解析 embedding 并重新检查共享授权。总部共享 Agent 的资源范围仍属于总部，不能替代需要伙伴私有 KB 的本地 Agent。

## 验证

```powershell
python -B scripts/test_onboard_partner.py
docker run --rm -v D:/BoyanKB/WeKnora:/app -w /app -v boyan-presets-go-mod:/go/pkg/mod -v boyan-presets-go-build:/root/.cache/go-build -e GOMAXPROCS=3 -e GOFLAGS=-p=3 golang:1.26-bookworm go test -ldflags='-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn' ./internal/application/service ./internal/handler -run '^TestPartnerProvisioning' -count=1
```

自动测试覆盖无凭据计划、重跑资源 ID 保持、部分失败恢复、渲染依赖失败不就绪、成员与组织越权拒绝、JWT/API Key 边界、原子回滚、租约与 revision 冲突、Contributor 归属限制及错误脱敏。真实部署验收结果由执行记录补充；本文件不将离线 mock 测试当作真实租户隔离证明。

2026-10-09 离线验证通过：Python 14 项，Go provisioning service 8 个及 handler 3 个顶层测试。回环 HTTP 边界、环境代理禁用、跨站重定向拒绝、可选重排真实模型校验和受管 Agent 标识均已覆盖。本机 PostgreSQL 已应用迁移 111、112；创建模拟伙伴 A、B（空间 10003、10004）并重复执行开户，资源 ID 与报告保持相同。真实 API 隔离检查 74 项通过，证据见 [预设验收记录](04-presets.md)。云端部署继续暂停。

需要人工验证：账号交付对象、伙伴间独立身份、撤销共享后的真实检索、普通用户无法改总部预设、真实业务事实与引用准确性，以及 Word/PPT/Excel 的分页、表格和品牌排版。删除伙伴、删除来源文档及移除既有业务配置不属于开通脚本。
