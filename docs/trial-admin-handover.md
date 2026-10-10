# 本机试用管理员切换

`scripts/prepare-trial-admin.py` 只处理已审阅的本机 `weknora-local` 清单。默认只读核验并生成计划；2026-10-10 已通过加密备份、隔离恢复及真实账号切换。它不创建合作伙伴、不安装预设、不启动隧道，也不修改生产配置。

## 保留与退役范围

保留总部空间 `10000`、飞书知识库 `d9285ed1-0760-474b-ba82-a13347598176`、该库现有 19 篇已解析文档（含授权探针）、相关切片和向量、数据源、总部模型、本地存储、模板及总部禁用脚本策略。知识库创建者变更为新管理员；租户与存储表没有独立用户所有者字段，总部所有权通过 active Owner 成员关系承接。

按 `temp/partner-delivery/audit-headquarters-ready.json` 中的确切 ID 退役原 3 个账号、4 个验收空间、11 个 Agent、4 个非保留知识库及其 3 篇文档、旧组织及分享关系、嵌入入口、旧技能目录和验收空间的存储/沙箱配置。账号与上述资源软删除，原账号停用、系统管理员与跨空间标志清除，所有原账号 `auth_tokens` 撤销。仅已确认的 3 条开通记录、50 条文档工作流记录及旧组织成员关系硬删除，必须先通过加密备份和隔离恢复核验。

旧会话、消息、物理文件及非保留知识库的历史切片/向量不转移、不清除；它们会列为残余历史，不代表物理清空数据库。源知识库的全部行（包括历史行）、切片、向量、数据源、总部模型/存储以及全部资源文件元数据在事务内做前后完整对照；只有源知识库 `creator_id` 和对应更新时间允许变化。脚本不改模板文件。

发现新增账号、空间、会话、资源、API Key、原生技能安装或任何审计对象变化时停止，不能自动扩展清单。软删除的邮箱/用户名仍受原数据库唯一约束，旧名称不保证可以重新注册。

## 执行前置

1. 以旧管理员设置并回读持久化系统设置：`auth.registration_mode=invite_only`、`auth.default_tenant_mode=tenantless`、`tenant.self_service_creation_enabled=false`。脚本不代改这些设置。备份、隔离恢复与账号交接必须使用同一组源容器；新镜像和试用网关在交接通过后部署。
2. 将新管理员邮箱、姓名和密码保存于 `temp/enterprise-trial/.env` 的 `TRIAL_ADMIN_EMAIL`、`TRIAL_ADMIN_NAME`、`TRIAL_ADMIN_PASSWORD`。脚本只在内存读取这三项，不输出其值；密码需 12–32 字符，含大小写、数字及特殊字符。无需公开注册入口，也不授予 `can_access_all_tenants`。
3. 执行 [本机备份与隔离恢复](trial-backup.md)。两份报告必须在 24 小时内，同一归档 SHA-256/大小、同一源项目与七个容器 ID，全部恢复组件核验通过，隔离恢复应用未启动。操作者分别核对并传入两份报告的 SHA-256；报告是实际恢复的审阅证据，不是数字签名。
4. 在维护窗口停止人工操作与同步/生成任务，保持容器不重建。脚本会锁定已验证的本地 Docker context，拒绝 `DOCKER_HOST` 和远程 daemon。只连接 `http://127.0.0.1:8080`，禁用代理和重定向；应用容器必须发布 8080，七个容器均属于 `weknora-local` 且运行中。

旧管理员令牌通过 `--operator-token-stdin` 的标准输入传入，不放在命令参数、环境文件或日志中。由本机受控调用程序向子进程的 stdin 写入令牌一行，再读取安全结果。不要把 JWT 写进命令历史或用输出命令回显。

只读计划的命令参数如下；仍需要上述安全 stdin 输入：

```text
python -B scripts/prepare-trial-admin.py --operator-token-stdin --baseline-sha256 <已核对的审计文件SHA256> --plan temp/enterprise-trial/admin-plan.json
```

计划包含保留/退役 ID、源容器 ID、残余历史数量与新身份摘要，不包含密码、令牌或正文。审阅文件及输出的 `plan_sha256` 后，才执行：

```text
python -B scripts/prepare-trial-admin.py --apply --operator-token-stdin --baseline-sha256 <审计SHA256> --plan temp/enterprise-trial/admin-plan.json --plan-sha256 <计划SHA256> --backup-receipt <备份收据.json> --backup-receipt-sha256 <收据SHA256> --restore-report <隔离恢复报告.json> --restore-report-sha256 <恢复报告SHA256> --archive <备份.age> --backup-identity <本机age私钥文件> --age-binary <age程序路径> --report temp/enterprise-trial/admin-result.json
```

`--apply` 会先调用 `verify-trial.py` 重新解密核验归档内容，随后通过以下接口建立新身份：

- `POST /api/v1/system/admin/users/create`：发送指定邮箱、姓名和密码；tenantless 前置避免另建个人空间。
- `POST /api/v1/system/admin/promote`：仅授予系统管理员。
- `POST /api/v1/tenants/10000/members` 或对应成员 `PUT`：增加/维护总部 Owner。
- `POST /api/v1/auth/login`、`GET /api/v1/auth/me`、`GET /api/v1/system/admin/list`：以新密码取得独立令牌并验证真实系统管理员与总部 Owner。

确认新身份后，脚本再次核对资源清单和容器 ID，再以一个数据库事务转移源知识库创建者、撤销旧令牌并退役已批准对象。事务内表锁、政策核验、精确清单核验和保留数据比较任一失败即回滚。成功后用新令牌再次验证 Owner 与源库读取，并要求旧操作令牌 `/auth/me` 返回 401。数据库事务不包含此前的 API 创建/提权，因此中断时新账号可能已存在，而旧账号仍保持可用。

## 中断与恢复

每次执行使用新的 `--report` 路径。新账号建立及验证后分别保存 `.created.json`、`.new_admin_verified.json`，只记录新用户 ID、阶段和已审阅文件摘要。若数据库事务尚未提交，可经人工确认这两份进度文件后，用同一计划和备份添加 `--resume-new-user-id <记录的UUID>` 重试。创建用户 API 对同邮箱和同用户名幂等返回原用户；密码保持原值，脚本仍须重新登录并验证 Owner。指定 UUID 不符时停止。

若错误阶段是 `retirement_transaction_outcome_unknown`，不能假定事务已回滚，应先核对数据库或新管理员登录；若是 `committed_post_verification`，旧账号已退役，不能再用原操作令牌重试。使用新管理员完成只读检查，必要时按已验证备份做单独恢复。脚本不会自动回滚到旧账号，也不会覆盖任何既有结果文件。

完成切换后，以新管理员和新的开通键安装总部 `shared_enterprise` 预设；不要复用已删除旧任务的请求参数或把私有工作库映射为源库。合作伙伴邀请和安装由单独开通流程处理。

## 邀请与会话边界

直接按邮箱邀请要求该邮箱已注册；共享邀请链接支持 `POST /auth/register-by-invite`，在 `invite_only` 下仍可创建新账号。共享链接是可复用的持有者令牌，不绑定邮箱，使用后应撤销；需要确定接收人身份时，先由管理员创建账号，再发送绑定既有账号的邀请。不得向合作伙伴发送总部 Owner 链接。

仅设置 `is_active=false` 不足以撤销本版本既有 JWT，因此脚本同时撤销存储的 access/refresh tokens 并软删除用户。API Key 独立于账号生命周期，审计时任何 API Key 出现均阻断本次流程，必须另行评审。历史聊天不归属新管理员。

## 验证

```text
python -B -m unittest discover -s scripts -p test_prepare_trial_admin.py -v
```

21 项离线测试通过，覆盖固定源数据保护、未知对象拒绝、新登录失败不进入退役、令牌/口令不进入计划与错误信息、幂等创建序列、仅本地 API/Docker、备份来源与恢复证据绑定、过期或篡改归档、SQL 输入边界及事务门禁。

真实切换报告为 `temp/enterprise-trial/admin-result.json`：新用户 `7a9e6424-f6e3-406c-a485-2e94ecaee5a0` 已独立登录并验证系统管理员、总部 Owner 和源知识库读取；原操作者令牌返回 401，源知识库保护校验通过。新身份没有跨空间全局访问标志。保留的历史为 76 个会话、48 项资源元数据和 21 项绑定，不能据此声称物理清空。伙伴开通与界面验证另见 `docs/08-enterprise-trial.md`。
