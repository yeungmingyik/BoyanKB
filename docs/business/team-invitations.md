# 合作伙伴团队邀请

合作伙伴负责人仍是本空间 Viewer。管理员通过开通清单指定负责人后，其可邀请只读员工；员工保留问答、业务技能、本人会话和本人文档工作流权限，不获得知识库编辑、模型管理、成员角色变更或 Owner 权限。

授权条件同时成立才可操作：Web 用户 JWT、当前空间 active Viewer、账号启用、空间有效、该空间开通任务为 `ready`、知识模式为 `shared_enterprise`，且 `state.team_manager_user_id` 等于当前用户。每次请求从数据库重新核验；API Key、嵌入身份及其他空间的负责人均不符合。

## 管理员开通

合作伙伴 manifest 可增加 `team_manager_email`，必须匹配 `members` 内一个 Viewer 邮箱，且仅用于非总部的 `shared_enterprise` 开通：

```json
{
  "knowledge_mode": "shared_enterprise",
  "members": [{"email": "manager@example.invalid", "role": "viewer"}],
  "team_manager_email": "manager@example.invalid"
}
```

这是完整 manifest 的相关字段片段。`onboard-partner.py` 完成成员核验后将其真实用户 ID 写入开通任务，服务端再次验证同空间 active Viewer。遗漏该字段的旧 manifest、请求哈希和空状态序列化保持兼容；增加字段会改变清单的 `spec_hash`，不能把已开通任务的旧清单直接换成新清单重跑。

需要先真实注册再安装时，以最终清单计算 `spec_hash`，先调用管理员 provisioning API 建立空间和任务。总部 Owner 为新空间创建 Viewer 邀请链接；负责人通过 `POST /api/v1/auth/register-by-invite` 注册，再由 Owner 撤销该链接。此后使用同一完整清单运行开通脚本，复用任务并完成安装。不要先用测试管理员账号冒充负责人。

管理员可使用现有开通任务 lease/revision 机制，将 `state.team_manager_user_id` 清空以撤销团队邀请能力；指定另一个用户时仍须通过同空间 active Viewer 检查。修改此字段不修改用户角色、既有员工成员关系、源知识库或其他空间授权。

## API

所有路径前缀为 `/api/v1/business-workflow`，均要求 Web JWT 和当前空间成员身份，响应 `Cache-Control: no-store`。不接受查询参数来指定其他空间。

| 方法与路径 | 请求 | 成功响应中的 `data` |
|---|---|---|
| `GET /team` | 无 | `{available,reason,members?}` |
| `GET /team/invitations` | 无 | `{items,total}`，本人创建的最新 100 条 |
| `POST /team/invitations` | `{kind:"link"}` 或 `{kind:"email",email:"..."}` | 新邀请记录 |
| `DELETE /team/invitations/:id` | 无 | `{success:true}`，无 `data` |

非管理者的能力接口返回 `available:false`，不附成员列表；创建、列表和撤销邀请返回拒绝。成员仅展示当前空间 active Viewer 的 `user_id`、`username`、`role`，最多 1000 人；不返回邮箱、总部 Owner 或其他空间成员。

邀请记录字段：`id`、`kind`、`status`、`expires_at`、`created_at`、`accepted_count`，邮箱邀请可有 `invitee_name`。只有新建链接的响应包含 `token` 与相对路径 `invite_url=/register?token=...`；列表不包含令牌或链接。服务端固定 Viewer、当前空间和当前邀请人，传入 `role`、`tenant_id`、`invited_by` 等额外字段会被拒绝。

邮箱邀请只支持已注册、启用且尚未加入本空间的账号，不直接写成员关系，不触发自动同意；员工通过现有邀请收件箱接受。新员工使用注册链接。每位负责人最多保有 20 条未过期的待处理邀请，每条有效期 24 小时。

链接沿用上游可复用的持有者令牌语义，不绑定收件人邮箱。妥善发送给预期员工，使用完成后撤销；撤销链接不移除已经加入的员工。负责人只能撤销自己在当前空间发出的待处理邀请，不能撤销 Owner 或其他负责人创建的邀请，也不能管理员工角色或移除员工。

## 撤权与兼容

迁移 `000113_team_invitations` 为邀请增加 `team_manager_user_id`，默认空。旧 Owner 邀请保留原来的流程；受限团队邀请在链接查询和邮箱邀请接受时重新验证负责人资格。负责人失去指定关系、Viewer 身份、active 成员/账号状态，或者任务离开 ready/shared 模式后，旧邀请不能继续授予成员资格。

新建链接只回显一次。团队链接也不会通过旧 Owner 邀请列表重新回显；请求日志对 `token`、`invite_url` 脱敏，生成和按令牌查找时不记录含令牌的 SQL。普通响应和审计记录只包含邀请 ID、固定 Viewer 角色及操作类型。

本次不修改已有 Viewer 的聊天、技能读取、文档确认/生成/下载路由。这些功能仍分别执行知识库共享授权、会话用户隔离和文件人工确认等既有检查；邀请功能不能绕过这些检查。

## 验证状态

开通脚本的 30 项 Python 测试已通过。Go 定向测试已通过，覆盖开通清单的 opt-in 与哈希、同空间 active Viewer 指定、跨空间和非 Web 身份拒绝、未知角色/空间字段拒绝、链接仅创建响应可见、邮箱邀请不直接增加成员、过期邮箱邀请重建、邀请上限、负责人撤权后旧链接和邮箱邀请失效、本人邀请撤销及旧 Owner 邀请兼容。SQLite 迁移的升级/回退和旧邀请保留测试、邀请 URL/token 与资源能力路径的日志脱敏测试也已通过。

复验命令：

```text
python -B -m unittest discover -s scripts -p test_onboard_partner.py
go test -ldflags="-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn" -run "Test(Team|RevokedManager|BusinessTeam|OwnerListCannot|Partner|DelegatedEmail|InvitationService|SanitizeBody|LoggerResourceCapabilityPathRedaction|SQLiteTeam)" ./internal/teaminvitation ./internal/businessworkflow ./internal/application/service ./internal/application/repository ./internal/handler ./internal/middleware
```

本次 Go 测试使用 `golang:1.26-bookworm`，以 `GOLANG_PROTOBUF_REGISTRATION_CONFLICT=warn` 处理上游 protobuf 重名注册。以上为定向测试，不代表整个 service 包的全部测试已经运行通过。

真实部署迁移、负责人注册、员工注册/接受邀请以及员工问答/文档流程尚待独立运行验收；本轮未执行这些在线操作。
