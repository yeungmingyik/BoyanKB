# 生产认证与空间隔离验收

本次采用邀请制，不接入 OIDC。生产入口需要拒绝 `POST /api/v1/auth/register`，同时保留登录、邀请查询及邀请注册接口。反向代理规则与应用的有效 `invite_only` 配置需要同时验收，不能用其中一项代替另一项。

当前脚本为 [scripts/acceptance-prod.py](../scripts/acceptance-prod.py)，只使用 Python 标准库。2026-10-09 独立 HTTPS 栈的 run8 使用默认全新创建流程，340 项自动检查全部通过，退出码 0；仍有 10 项人工验收，不能据此宣称完整生产验收通过。16 项离线边界测试也已通过。仅在专用 staging 栈创建合成数据，没有改动现有业务用户、真实业务 `.env` 或上游认证代码。

## 本次实测证据

| 项目 | 结果 |
| --- | --- |
| 部署 | `boyankb-staging-20261009`，`https://localhost:8443`，使用指定 CA 校验证书 |
| 完整自动报告 | `temp/production-acceptance/run8.json`：340 PASS、0 FAIL，10 项人工检查 |
| 资源清单 | `temp/production-acceptance/run8.fixtures.json`，不包含密码、Token、邀请凭据或邮箱 |
| 测试空间 | A=`10009`，B=`10010`；本轮重新创建两个空间、随机身份及内容，没有使用 `--reuse-workspaces` 或运行时补丁 |
| 空间创建窗口 | run8 同轮完成临时开启、创建两个空间、finally 关闭及 GET 读回；五个普通测试身份建空间均被拒绝 |
| 角色与邀请 | 共享链接创建/注册/撤销，撤销后注册被拒绝；绑定邮箱邀请的拒绝、接受、错误用户拒绝均通过；移除成员后旧令牌返回 401，重新登录成功 |
| 隔离 | A↔B 列表、KB、文档、分块、会话、消息、目标空间请求头、跨 KB 检索参数均通过；使用真实存在的资源作为负向对照 |
| Key | 范围检索 Key、full-access 空间 Key 与撤销 Key 的能力、KB 范围及空间边界均通过 |
| 检索与问答 | A/B 各自关键词、向量及混合检索成功；DeepSeek 合成答案包含正确 canary 和知识引用；一个越权注入样本未泄露 B canary；空 KB 返回固定拒答 |
| 上传文件 | A 的合成 Markdown 上传、解析及 marker 分块检查通过；文件知识 ID=`0d819b33-63f9-4278-bcfc-25f146c2e90b`，文件名=`prodaccept-eb8d0c63e7-upload.md`，可用于相应时间点备份的文件卷验证 |
| 配额恢复 | `temp/production-acceptance/run8-postconditions.json`：有效配额恢复 10，自助创建 false，管理员 `can_create_tenant=false`；原测试数据保留，管理员现拥有 11 个空间 |

run8 开始前实查该隔离管理员已拥有 9 个空间，有效上限为 10，不能再一次创建两个空间。经授权，仅在该隔离栈将配额临时设为 20，记录原值，在外层 `finally` 恢复 10，并通过 GET 复核。脚本本身没有自动修改配额的逻辑；新增的只读前置检查会在配额不足时以退出码 2 停止。普通生产账号仍受生产设置约束，不应照搬测试中的临时配额调整。

完成配额准备后，run8 使用的标准命令如下：

```powershell
python scripts/acceptance-prod.py --base-url https://localhost:8443 --env-file temp/production-staging/.env --ca-file temp/production-staging/ca.crt --isolated --create-fixtures --random-identities --allow-llm --output temp/production-acceptance/run8.json
```

该报告已存在，复跑需新输出路径且有足够配额。上一轮 `run7.json` 也曾完成 332 项自动检查，使用空间 A=`10007`、B=`10008`；其文件知识 ID=`9f87723f-97da-4b23-863a-e6336b514f93`。备份发生在 run8 之前时，应使用 run7 manifest 校验恢复，不应期待其中出现 run8 的新资源。

此前未完成轮次保留在同一临时目录。脚本兼容修正包括：生成密码改为符合上游限制的 28 字符，手工文档创建接受实际 HTTP 200，成员移除后验证令牌撤销再登录，以及邀请接口 HTTP 429 时有上限地等待重试。这些失败轮次不作为通过证据。

## 配置优先级与生产要求

| 项目 | 解析规则与要求 | 代码依据 |
| --- | --- | --- |
| 公开注册 | 显式 DB `auth.registration_mode` 覆盖启动配置；`DISABLE_REGISTRATION=true` 将启动默认改为 `invite_only`。单改环境变量不能覆盖已有管理员保存的 `self_serve` | `internal/handler/auth.go:94`、`:190`；`internal/config/config.go:853` |
| 默认开户空间 | DB `auth.default_tenant_mode` → `WEKNORA_AUTH_DEFAULT_TENANT_MODE` →启动配置→`create_personal`。生产选择 `tenantless`；邀请注册另行绑定被邀请空间 | `internal/handler/auth.go:122`；`internal/handler/auth_register_by_invite.go:125` |
| DB 默认占位记录 | 旧初始化记录若 `last_modified_by` 为空且值等于注册默认值，会退回环境/配置。API 可返回虚拟有效行；`id` 非零不等于该行一定在覆盖环境变量 | `internal/application/service/system_setting.go:599`、`:795`、`:889` |
| OIDC | `OIDC_AUTH_ENABLE` 显式覆盖启动配置；公开 `/auth/oidc/config` 返回顶层 `enabled`。没有通过系统设置表动态开关 OIDC 的实现。本次必须 `false` | `internal/config/config.go:702`；`internal/handler/auth.go:395`；`internal/application/service/user.go:1519` |
| RBAC | `WEKNORA_TENANT_ENABLE_RBAC=true`；未指定时默认启用。禁止依赖仅前端隐藏按钮 | `internal/config/config.go:881`；`internal/middleware/rbac.go:68` |
| 跨空间管理员访问 | `WEKNORA_TENANT_ENABLE_CROSS_TENANT_ACCESS=false`，所有用户的原始 `can_access_all_tenants` 也保持 false。当前部分签发/角色路径仅看原始标志，不能把全局 false 当成其可靠限制 | `internal/config/config.go:898`；`internal/application/service/user.go:1143`；`internal/middleware/auth.go:788` |
| 自助创建空间 | 常态 `tenant.self_service_creation_enabled=false`；只在受控维护窗口临时启用，创建后立即关闭并读回 | `internal/handler/tenant.go:242`；`internal/application/service/system_setting.go:179` |
| 既有账号的邮箱邀请 | `tenant.auto_accept_invitation=false` 才经过接受/拒绝流程。DB 值优先于 `WEKNORA_TENANT_AUTO_ACCEPT_INVITATION` | `internal/handler/tenant_invitation.go:265`；`internal/application/service/system_setting.go:221` |
| Docker 沙箱 | `sandbox.docker_enabled=false` 的 DB 有效值与 `WEKNORA_SANDBOX_DOCKER_ENABLED=false` 均须核验 | `internal/application/service/system_setting.go:130`；`internal/sandbox/docker_enabled.go:19` |
| 全空间脚本策略 | 每个空间单独 `scripts_disabled=true`。该设置不由关闭 Docker 开关自动覆盖；新建空间也必须配置 | `internal/handler/sandbox_config.go:227`、`:256` |

系统设置 API 位于 `/api/v1/system/admin/settings/{key}`，需要系统管理员。`GET` 返回顶层设置对象；`PUT` 请求体为 `{"value": "invite_only"}` 或相应布尔值，返回同样的顶层对象。不是 `data.value`。

脚本的隔离创建模式只写入以下平台设置，并读回有效值：

```json
{
  "auth.registration_mode": "invite_only",
  "auth.default_tenant_mode": "tenantless",
  "sandbox.docker_enabled": false,
  "tenant.self_service_creation_enabled": false
}
```

只读模式不会应用这些设置。它检查 `GET /api/v1/auth/config` 的顶层 `registration_mode`、OIDC 开关及管理 API 的有效值。注册入口的有效拒绝使用完整合法注册参数，可能开户，因此仅在显式隔离创建模式执行，要求 HTTP 403。

工作空间脚本策略的读失败分支会返回未禁用，而不是拒绝运行；这需要基础设施层共同防护。必须另外验证容器无 Docker socket、无沙箱后端凭据、无可达运行时。脚本只通过 API 检查 `GET /api/v1/sandbox-configs` 的顶层 `workspace_scripts_disabled=true` 且 `data=[]`，不能证明网络与宿主机挂载隔离。依据：`internal/application/service/tenant_sandbox_resolve.go:102`。

## Bootstrap 与新增空间维护

`is_system_admin` 管理系统设置，并不自动绕过自助建空间开关。`POST /api/v1/system/admin/promote` 仅设置该标志，不会设置 `can_access_all_tenants`。`CreateTenant` 使用原始 `can_access_all_tenants` 作为另一种建空间权限，因此只提系统管理员且关闭自助创建时，建空间仍会返回 403。依据：`internal/handler/system.go:1349`、`:1361`；`internal/handler/tenant.go:242`。

本部署不永久授予原始跨空间标志。源码中 `SwitchTenant` 在该标志为 true 时跳过成员检查，签发目标空间 JWT；没有 `X-Tenant-ID` 的后续请求使用 JWT 中的空间，角色解析也存在仅检查该标志的分支。上述路径未完整遵循全局跨空间开关，不能宣称 `WEKNORA_TENANT_ENABLE_CROSS_TENANT_ACCESS=false` 足以约束原始标志为 true 的操作员。该结论来自代码追踪，尚未运行绕过验证；生产选择不授予该标志。依据：`internal/application/service/user.go:1143`；`internal/middleware/auth.go:334`、`:788`。

新增生产空间采用受控维护窗口，步骤为：

1. 确认管理员 `is_system_admin=true`、原始 `can_access_all_tenants=false`。限制维护期间的访问；开启自助创建是全局设置，并非仅向当前管理员授权。空库初始化时仅保留指定 bootstrap 用户。
2. 使用管理员调用 `PUT /api/v1/system/admin/settings/tenant.self_service_creation_enabled`，请求 `{"value":true}`。
3. 使用同一管理员调用 `POST /api/v1/tenants`，只传 `name`、`description`。成功后该管理员自动成为新空间 Owner。
4. 无论创建成功与否，立即将上述系统设置改回 `{"value":false}`，再 `GET` 验证有效值为 false；异常无法关闭时停止交付并人工关闭。
5. 新空间设置 `PUT /api/v1/sandbox-configs/workspace-policy` 的 `{"scripts_disabled":true}`，用 `X-Tenant-ID` 指向该空间并读回，然后才发出伙伴邀请。

隔离脚本只在两个测试空间的创建阶段开启此窗口，`finally` 负责关闭；关闭后才创建普通测试用户。脚本通过 `/auth/me` 的 `capabilities.can_create_tenant=false` 和实际拒绝普通用户创建空间，验证常态策略。若维护期间原始跨空间标志仍为 true，该能力检查会失败，不会把遮蔽后的 `user.can_access_all_tenants=false` 当作足够证据。

长期自动化可另行评估带 `system_tenants_manage` 的平台 Key，但该建空间路径不自动添加人类 Owner，需要补齐授权成员的设计与测试；当前不能直接替换上述流程，更不能用平台 Key 的结果证明普通用户空间隔离。

## OIDC 边界

`LoginWithOIDC` 根据邮箱查找本地账号；没有账号时直接进入 `provisionOIDCUser`，不检查 `registration_mode`。`invite_only` 不能阻止被 IdP 接受的首次 OIDC 用户自动开户。代码位置为 `internal/application/service/user.go:485`、`:515`、`:1730`。因此，当前部署保持 `OIDC_AUTH_ENABLE=false`，不能在邀请制验收后直接打开 OIDC 并沿用结论。

后续确需 OIDC 时，需要人工验证 IdP 的准入范围、账户生命周期、邮箱映射、回调域名、退出与撤销、已存在本地账号匹配，以及首次用户是否允许开户。若要求“必须先收到本应用邀请才能通过 OIDC 开户”，当前实现不满足该要求，需要单独确定改动方案。启用 OIDC 也不等于全局关闭密码登录。

## 角色、邀请与 API Key

| 身份 | 当前边界 | 自动验收内容 |
| --- | --- | --- |
| Owner | 管理空间成员、邀请、空间 API Key；普通 Owner 不具有系统管理员或跨空间权限 | A/B 各一位普通 Owner；核验身份、成员关系及跨空间拒绝 |
| Admin | 管理空间内容/基础设施；当前邀请创建和 API Key 管理由 Owner 路由约束 | 可以在 Owner 的 KB 创建草稿；不能创建 Owner 邀请或 Key |
| Contributor | 可创建 KB；修改内容还要求 KB 创建者或 Admin 权限 | 新建自己的空 KB 成功；写 Owner KB 被拒绝 |
| Viewer | 可读本空间 KB；聊天会话仍有用户归属检查 | 可读 KB；创建 KB、写内容、创建邀请/Key 被拒绝 |
| `retrieve` 空间 Key | 需要显式能力，且受 `knowledge_base_ids` 限制；不是用户角色的替代名称 | 指定 KB 检索成功；同空间另一 KB、B 空间、跨空间请求头和 Key 管理被拒绝 |
| `full_access` 空间 Key | 覆盖本空间允许 Key 调用的接口；不能变为平台 Key 或管理自己的 Key | 可读同空间另一 KB；B 空间和 Key 管理仍被拒绝 |
| 平台 Key | 有独立平台能力和目标空间选择规则 | 需要人工验证；本脚本不创建平台 Key |

依据：`internal/types/tenant_member.go:19`、`internal/router/routes_auth_tenant.go:74`、`:98`、`:144`、`internal/router/routes_knowledge.go:65`、`internal/middleware/auth.go:425`。Owner/Admin 等级并不意味着能读取其他用户的普通网页会话；管理员读取 API、IM、嵌入渠道会话有单独规则，不能套用到所有会话。依据：`internal/application/service/session.go:62`。

邀请的准确调用方式：

| 操作 | API 与请求体 | 行为 |
| --- | --- | --- |
| 创建共享链接 | `POST /api/v1/tenants/{id}/invite-links`，`{"role":"viewer","message":"..."}` | Owner；201 `data.id`、`data.invite_url`。链接查询参数包含凭据，只在内存解析 |
| 公开查询邀请 | `POST /api/v1/auth/invitations/lookup`，`{"token":"..."}` | 有效返回 200；未知、过期或撤销返回 410 |
| 通过邀请开户 | `POST /api/v1/auth/register-by-invite`，`{"token":"...","email":"...","username":"...","password":"..."}` | 201；顶层 `token`、`user`、`active_tenant`、`memberships`，不套 `data` |
| 撤销邀请 | `DELETE /api/v1/tenants/{id}/invitations/{inv_id}` | Owner；已加入成员不会因撤销链接而退出 |
| 邀请已有账号 | `POST /api/v1/tenants/{id}/invitations`，`{"email":"...","role":"viewer","message":"..."}` | 对应邮箱必须已注册；关闭自动接受后生成 pending 邀请 |
| 接受/拒绝 | `POST /api/v1/me/invitations/{inv_id}/accept` 或 `/decline` | 只允许被邀请人操作 |
| 创建 Key | `POST /api/v1/tenants/{id}/api-keys`，`{"name":"...","full_access":false,"knowledge_base_ids":["..."],"capabilities":["retrieve"],"expires_at_unix":整数}` | Owner JWT；201 `data.token`，创建时仅在内存使用 |
| 撤销 Key | `DELETE /api/v1/tenants/{id}/api-keys/{key_id}` | 已撤销 Key 再调用应返回 401 |

共享邀请链接是可重复使用的持有者凭据，不绑定收件邮箱，也不是一次性链接。Owner 可以创建 Owner 角色链接；不要将它公开发布。当前普通成员读取邀请列表时不返回 `invite_url`。脚本创建的共享链接在注册完成后立即撤销；异常退出会尽力撤销尚未消费完的共享链接。自然过期、密码重置和刷新令牌另行人工验证。

脚本还测试绑定邮箱的邀请：A 邀请 B 的既有账号，A 不能代接受，B 可以拒绝或接受；接受后只赋予 Viewer，再移除这条测试成员关系。上游移除成员会撤销该用户的全部认证令牌，脚本先验证旧令牌返回 401，再用仅存内存的密码重新登录并核验 B 只属于自己的空间。不会删除工作空间或业务内容。

## 执行方式

默认只检查 API 有效配置和未认证访问。提供管理 Token 时不执行登录；提供邮箱/密码时会执行登录并生成认证令牌，但不创建业务资源。检索复核使用只读语义的 POST，服务仍可能写访问记录、审计或 Key 最近使用时间。

准备一个仅用于验收的环境文件，使用以下变量名。值由部署负责人填写；不能把真实值提交仓库、粘贴进报告或放在命令行参数中。

```dotenv
ACCEPTANCE_ISOLATED_ORIGIN=https://localhost:8443
ACCEPTANCE_ADMIN_EMAIL=<隔离部署管理员邮箱>
ACCEPTANCE_ADMIN_PASSWORD=<隔离部署管理员密码>
```

也可使用 `ACCEPTANCE_ADMIN_TOKEN` 代替邮箱和密码。进程环境优先于 `--env-file`。解析器只取 `ACCEPTANCE_` 变量，支持单行无引号或成对引号的值，不执行变量展开、命令替换或转义解释。文件中的其他配置不进入脚本配置对象。

```powershell
python scripts/acceptance-prod.py --self-test
python scripts/acceptance-prod.py --base-url https://localhost:8443 --env-file temp/production-acceptance/staging.env --ca-file temp/production-acceptance/staging-ca.pem --output temp/production-acceptance/read-only.json
```

`--base-url` 必须为 HTTPS origin，不接受用户信息、查询参数或路径。`--ca-file` 用于本地 CA；生产受信任证书可省略。脚本不提供跳过证书验证选项，也不会跟随重定向携带凭据到另一地址。

隔离部署由主流程预先创建管理员。写模式需要同时满足 `--isolated`、`--create-fixtures` 和 `ACCEPTANCE_ISOLATED_ORIGIN` 与目标 origin 精确一致。该声明不能技术证明部署隔离，执行人仍需检查独立 Compose project、数据库、卷、网络、端口及密钥。

```powershell
python scripts/acceptance-prod.py --base-url https://localhost:8443 --env-file temp/production-acceptance/staging.env --ca-file temp/production-acceptance/staging-ca.pem --isolated --create-fixtures --random-identities --allow-llm --fixture-prefix prodaccept-run01 --output temp/production-acceptance/run01.json
```

每次使用新的输出路径及 fixture 前缀。输出文件已存在会拒绝运行，不覆盖旧报告。脚本创建的范围为：

- A/B 两个工作空间及各自普通 Owner，A 内 Viewer、Contributor、Admin 各一人。默认随机强密码和 `.invalid` 邮箱只保留在内存，不保存凭据。
- 每空间一篇带随机合成 canary 的已发布文档及一个普通会话；A 另有空 KB、Contributor 的空 KB、Admin 写入的草稿及一份真实上传的合成 Markdown 文件。
- A 空间的 KB 范围检索 Key、全权限空间 Key、一个立即撤销的测试 Key。未撤销 Key 有 24 小时到期时间，但脚本不会将其值写出。
- 若指定 `--allow-llm`，增加两个受限 Agent 和三个合成问答会话；不连接飞书，不使用现有真实知识库正文。

`--random-identities` 忽略 A/B 和角色用户的预置邮箱/密码，保留管理员认证环境变量，适合从失败轮次重新开始并避免已有邮箱冲突。省略该选项时才使用预置身份；预置密码会在创建资源前检查有效密码策略。部分完成的 manifest 不能作为完整只读复核输入，脚本不会把它当作可恢复凭据。默认流程读取管理员 Owner 成员关系数及有效空间配额，确认还能创建两个空间后才进入建空间窗口；配额不足退出 2，不自动提高限额或删除旧资源。

需要继续在已创建的专用测试空间重跑时，可以加 `--reuse-workspaces <旧manifest路径>`。必须同时开启隔离创建模式和 `--random-identities`；脚本检查 origin、两个不同空间 ID，以及通过管理员 API 读到的 `prodaccept-` 空间名称与原 manifest 一致，再创建本轮新身份及新数据。它不会再次开启全局建空间窗口，也不删除前轮内容。该选项与只读 `--fixtures` 不同，不能用于业务空间。

```powershell
python scripts/acceptance-prod.py --base-url https://localhost:8443 --env-file temp/production-staging/.env --ca-file temp/production-staging/ca.crt --isolated --create-fixtures --random-identities --reuse-workspaces temp/production-acceptance/run6.fixtures.json --allow-llm --output temp/production-acceptance/run7.json
```

以上为已执行命令；原输出已存在，复跑需更换输出文件名。邀请查询和邀请注册共享每 IP 30 次/分钟限制。脚本只对这两个接口的 HTTP 429 每 10 秒等待重试，最多重试 7 次；持续限流仍判失败，不将限流作为通过，也不绕过服务端限制。

使用部署内置 `local-embedding`（local，1024 维）和 `local-deepseek-chat`。可通过 `--embedding-model`、`--chat-model` 选择另一组已配置模型，但本脚本仍要求本地 1024 维 embedding。KB 的 `summary_model_id` 留空，并关闭文档摘要、图谱与问题生成；LLM 仅在显式选项的三个问答中使用。默认不发送正文给远程 LLM。

基线核验生产内置 Agent 列表只含 `builtin-quick-answer`，且有效配置为固定拒答、关闭网页搜索。合成验收另建自定义 Agent，固定拒答标记为 `ACCEPTANCE_NO_EVIDENCE`，并读回确认固定策略、网页搜索关闭、仅选定测试 KB、MCP/Skills 均未选择。它不依赖生产内置 Agent 的中文拒答文案，也不修改内置配置。

每次产生 `run01.json` 和 `run01.fixtures.json`。后者只包含目标 origin、随机合成 canary、资源 ID 和角色，不含邮箱、密码、邀请链接或访问令牌。它用于对照现存测试对象；不是凭据备份。

若要复用 fixtures 运行只读隔离检查，需自行提供这五个身份的 `ACCEPTANCE_A_TOKEN`、`ACCEPTANCE_B_TOKEN`、`ACCEPTANCE_VIEWER_TOKEN`、`ACCEPTANCE_CONTRIBUTOR_TOKEN`、`ACCEPTANCE_WORKSPACE_ADMIN_TOKEN`，或对应的 `_EMAIL`/`_PASSWORD`；另需 `ACCEPTANCE_RETRIEVE_KEY` 和 `ACCEPTANCE_FULL_KEY`。可在创建阶段预置五组邮箱/密码以便之后登录；默认随机账号不可从 manifest 恢复密码。Key 应由测试空间 Owner 在可信管理界面另行创建并安全注入环境，不应增加明文导出文件。

```powershell
python scripts/acceptance-prod.py --base-url https://localhost:8443 --env-file temp/production-acceptance/recheck.env --ca-file temp/production-acceptance/staging-ca.pem --fixtures temp/production-acceptance/run01.fixtures.json --output temp/production-acceptance/recheck.json
```

复用模式不重新运行需要写入的邀请、角色写权限、Key 撤销或 LLM 项目。要完整重跑，重新使用创建模式和新的命名空间。不存在自动删除空间、文档、卷或用户的功能；结束后是否整体销毁隔离栈由部署负责人按已获授权另行执行。

## 自动判定与人工验收范围

自动隔离检查使用已知存在、拥有者确实能访问的 B 对象作为 A 的负向样本，并对称测试 B→A。随机不存在 ID 的 404 不算空间隔离证据。覆盖列表、KB、文档、分块、会话、消息、伪造 `X-Tenant-ID`、三种检索方式、跨 KB 参数、不同角色及 Key。共享 KB/Agent 授权路径不在该无共享样本的结论内。

开启 LLM 后只执行三个合成样本：有证据回答及引用、要求越权读取 B canary 的提示词、空 KB 的固定拒答。只有第一个样本包含正确答案的正向对照，第二个样本不把 B 的秘密 canary 放进问题，避免回显造成误判。空 KB 固定拒答不能证明非空 KB 对所有知识外问题都会拒答；单个注入样本也不能证明普遍抗注入。

报告的 `automated_result` 仅覆盖本次实际启用的自动项目。`production_readiness` 始终为“需要人工验证”，并列出未完成的基础设施、域名/OIDC、共享路径、平台 Key、配额、Word/PPT、飞书删除及业务问答验收。

| 退出码 | 含义 |
| --- | --- |
| 0 | 本次启用的自动断言全部通过；仍须检查 `manual_checks`，不能据此宣称完整生产验收通过 |
| 1 | 自动断言失败，报告保留检查名和 HTTP 数字状态 |
| 2 | 前置条件、TLS/网络、响应结构或报告写入失败；该轮不完整 |

按用户/空间的真实费用限额、令牌用量限额及速率策略不由上述角色断言证明。源码存在空间存储配额、用户可拥有空间数上限及自助建空间开关，部署必须另行核验有效配置；`tenant.max_owned_per_user=0` 的处理不能误读为必定采用默认上限。依据：`internal/handler/tenant.go:198`、`:303`、`internal/application/service/system_setting.go:171`。

需要人工验证的飞书删除必须使用独立测试空间、明确的探针文档和已确认删除策略；不要对真实合作伙伴空间自动删除。Word/PPT 需要上传自建样本并人工检查排版与引用，不能用本脚本的 Markdown canary 代替。
