# 企业知识库与合作伙伴试用

## 运行方式

本机 Docker 运行 BoyanKB，专用 Cloudflare Tunnel 将 `kb.boyantech.xyz` 转发到 `127.0.0.1:8090`。域名仍由既有 Cloudflare DNS 管理；不需要先购买 ECS。本机、Docker Desktop 和隧道进程必须持续运行。Windows 休眠、断网或退出 Docker 都会中断访问。

公网入口是 HTTPS。应用、前端和网关的宿主端口仅监听 loopback；数据库、Redis、模型服务和文档渲染器不发布宿主端口。公网禁止公开注册、系统管理接口、原生脚本技能管理、沙箱执行和调试入口。本机管理员通过 `127.0.0.1:8088` 管理平台，合作伙伴使用域名。

## 登录入口

| 账号 | 入口 | 空间与身份 |
| --- | --- | --- |
| `aldream.lau@gmail.com`（刘伟健） | `http://127.0.0.1:8088/login` | 博研总部 Owner、系统管理员 |
| `testpartner001@gmail.com`（卡卡） | `https://kb.boyantech.xyz/login` | 星辰教育有限公司 Viewer、指定团队负责人 |

两者使用本人已在本机私密配置中填写的密码。伙伴负责人登录后从左侧“团队邀请”创建员工链接；员工在链接页面自行设置密码，不使用负责人的密码。当前没有配置自动发送邀请邮件。

## 资料与使用权限

只保留一份飞书企业资料，知识库 ID 为 `d9285ed1-0760-474b-ba82-a13347598176`。总部维护资料和同步；伙伴工作空间以 `shared_enterprise` 模式获得只读共享，不复制资料、不建立额外工作知识库。

| 身份 | 问答、技能、生成与下载自己的文件 | 修改企业资料或同步 | 邀请员工 | 系统管理 |
| --- | --- | --- | --- | --- |
| 总部管理员 | 可以 | 可以 | 由管理员开通伙伴 | 本机管理入口 |
| 已指定的伙伴负责人 | 可以 | 不可以 | 仅本空间 Viewer | 不可以 |
| 普通 Viewer 员工 | 可以 | 不可以 | 不可以 | 不可以 |

伙伴负责人仍是 Viewer，邀请能力由服务端的开通记录单独授权。前端显示按钮不是授权依据。创建和接受邀请时都重新检查负责人资格、空间和角色，禁止使用 API Key 调用团队邀请接口。

新员工通过邀请页面设置自己的密码；已有账号可以接受定向邀请。新账号邀请链接有效期 24 小时，持有者可使用，应私下交给目标员工，并在使用后撤销。链接只在创建时显示，团队列表不会再次返回令牌。每位负责人最多保留 20 条未过期的待处理邀请。

管理员密码放在 `temp/enterprise-trial/.env` 的 `TRIAL_ADMIN_PASSWORD`；本次模拟伙伴密码放在同文件的 `TRIAL_PARTNER_PASSWORD`。不得将密码、邀请链接、JWT 或 Cloudflare 令牌复制进文档、命令参数或日志。

## 交接顺序

1. 关闭注册和自建空间，核对原有资源清单。
2. 使用 [本机备份与恢复](trial-backup.md) 生成加密归档，并恢复到独立的内部网络和新卷。
3. 使用 [管理员交接](trial-admin-handover.md) 的默认计划核对保留和退役范围；备份、恢复及计划摘要通过后才执行账号切换。
4. 构建并锁定定制 app、frontend 镜像，叠加 `docker-compose.trial.yml`。必须使用同项目原有基础、local、branding、presets、delivery-local 配置，禁止另起空白业务数据库。
5. 以新的 `partner_key` 安装总部 `shared_enterprise` 预设，保留源知识库。
6. 用最终伙伴 manifest 的摘要创建开通记录与公司空间，创建 Viewer 邀请，通过邀请注册，再撤销链接；使用同一 manifest 继续执行 `scripts/onboard-partner.py`，安装三位智能体与九项技能。
7. 通过本机权限和日志检查后，创建专用隧道、启动连接器、确认连接健康，再切换单个 `kb` DNS 记录。

隧道操作见 [本机 HTTPS 隧道](trial-tunnel.md)，员工入口见 [团队邀请](trial-team.md)，可重复检查见 [试用验收](trial-acceptance.md)。

## 已执行记录

2026-10-10 已完成约 1.21 GB 的 age 加密备份，归档 SHA-256 为 `18ce38c3e81eec7f05a7d2b43074d32e8b09ec6a5dbf6a30e32a26cc1207366f`。`boyankb-restore-trial-rehearsal01` 仅启动隔离 Postgres 和 Redis，数据库、对象文件、Redis、环境配置、镜像运行资源和模型缓存核对通过。未在恢复环境启动应用或进行问答，不能将这项演练视为应用恢复全流程证明。

管理员交接随后通过：新管理员已独立登录、取得总部 Owner、读取源知识库；旧令牌返回 401。旧 3 个账号、4 个验收空间、4 个非源知识库及清单内关联对象按计划退役；源 19 篇资料保留。认证设置已通过 API 读回：邀请制、强密码、自建空间关闭、自动接受邀请关闭、Docker 沙箱关闭。

私密执行证据位于 `temp/enterprise-trial/{backup-receipt,admin-plan,admin-result}.json` 和 `temp/enterprise-trial/rehearsal01/restore-report.json`。归档与 age 密钥分别位于 `pre-reset.age` 和 `keys/.env`，不要一起公开或提交。

总部为 `10000`，星辰教育有限公司为 `10005`。两者均已安装三个业务智能体、九项技能，并实际重复开通通过、资源 ID 保持一致。有效企业知识库只有一个。伙伴负责人“卡卡”通过 Viewer 邀请注册，原注册链接已撤销；未发送外部邮件。根 `.env` 的 `SMOKE_ADMIN_EMAIL`、`SMOKE_ADMIN_PASSWORD` 已在本机同步为新管理员凭据，供原飞书 helper 继续登录，真实值不进入日志。

| 检查 | 实际结果 |
| --- | --- |
| 服务 | 8 个试用服务健康；仅 8088、8080、8090 发布到 `127.0.0.1` |
| 镜像 | app `v0.8.2-trial-v1`、frontend `v0.8.2-trial-v1`、gateway `2.11.7-v1`；所有服务固定摘要，详见 `deployment/trial/images.lock.json` |
| 网关 | 本机与公网各 6 项通过；注册 403、系统管理/原生技能管理 404、沙箱 403 |
| 本机账号 | 26 项只读权限检查通过；负责人固定 Viewer、三 Agent、九技能、共享源限定、跨总部拒绝 |
| 公网账号 | `20261010-114004-8730cb57/report.json`：26 项通过，严格 TLS 校验，使用明确的部署检查 User-Agent |
| 邀请 | 伙伴真实邀请注册及撤销成功；负责人实际创建一条 Viewer 员工链接并撤销成功，列表不回显令牌 |
| 真实问答 | 企业资料问答通过；另在公网同一会话验证 SSE 完整结束，证据为 `public-flow.json` |
| Word | 首次只有文字大纲；原会话追问一次后提交正式任务，确认前生成/下载 409，错误哈希 409；确认与审校后发布，补充 35 项检查通过 |
| 下载与打开 | 公网下载与本地文件 SHA-256 一致；Microsoft Word 实际打开并导出 3 页 PDF，原文件未改变 |
| 日志 | 真实成功请求、无效资源短链与独立故障上游的 502 均未记录合成令牌；8 项离线日志检查通过 |
| 本机浏览器 | 42 项通过，含总部入口隔离、伙伴团队页、邀请制登录和 390px 无横向溢出 |
| 公网浏览器 | 28 项通过，含有效 HTTPS、伙伴登录、团队页面和手机布局；使用明确部署检查 User-Agent，测试身份关闭首次引导，详见 `docs/trial-team.md` |
| 飞书保护策略 | 本机只读配置检查通过，源空间限定及删除保留策略不变；现存远程摘要配置使 helper 的 `sync_allowed=false`，尚未恢复后续同步，详见下文 |

Word 文件为 `temp/enterprise-trial/acceptance/word-resume-20261010-01/artifacts/合作伙伴入门指南.docx`，SHA-256 为 `29fe4dfcc4ff84dfdabf4ceefad9d1e56f5a04e0670f0d72706bee4e67bae7a7`。不掩盖第一次仅输出文字大纲的失败记录；新验收脚本最多追加两次工具提交请求，已有审校阻断的任务不会自动重试或绕过。

## 再次启动与检查

在 PowerShell 7 中进入 `D:/BoyanKB/WeKnora`，启动 Docker Desktop 后执行：

```powershell
$trialCompose = @('compose', '-p', 'weknora-local', '--env-file', '.env', '-f', 'docker-compose.yml', '-f', 'docker-compose.local.yml', '-f', 'docker-compose.branding.yml', '-f', 'docker-compose.presets.yml', '-f', 'docker-compose.delivery-local.yml', '-f', 'docker-compose.trial.yml')
docker @trialCompose up -d --no-build --wait --wait-timeout 180
$trialPython = 'D:/CodexData/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $trialPython -B scripts/run-trial-tunnel.py start
& $trialPython -B scripts/check-trial-gateway.py --base-url https://kb.boyantech.xyz
& $trialPython -B scripts/acceptance-trial.py --base-url https://kb.boyantech.xyz
```

连接器没有安装为 Windows 服务，也未设置开机启动。默认验收只登录并读取；需要重新产生业务会话和 Word 时才加 `--exercise`。存在人工验收项时脚本退出 2，应查看报告的 `automated_result`，不能仅用终端非零状态判断自动检查失败。

构建方式保持 `scripts/build-image.sh --with-app`，使用环境变量指定新 app/frontend 标签；网关使用 `docker build -f deployment/trial/Dockerfile.gateway -t <新网关标签> deployment/trial`。重建后的摘要可能变化，必须在本机试用配置和锁文件中同步登记并验证，不原地改写旧镜像。

## 本次排障

- 原定内部网段与既有 `172.30.0.0/16` 网络重叠，改为专用 `10.239.70.0/24`。换机器前仍应检查网段冲突。
- Caddy 二进制自带低端口文件 capability，在全部 capability 移除、非 root 模式下不能执行；派生镜像移除该文件 capability，继续使用非 root、只读文件系统与 `no-new-privileges`，监听 8090。
- Docker Desktop 仅连接 internal 网络时没有实际发布网关端口；增加只连接网关的独立 edge 网络，宿主仍仅监听 loopback。
- 公网默认 Python User-Agent 曾收到 403；明确使用 `BoyanKB-DeploymentCheck/1.0` 后通过。未关闭 TLS 校验或修改 Cloudflare 安全规则。
- Nginx 的 502 错误页内部转发会将日志路径变为 `/50x.html`；日志探针已兼容这条固定安全路径，并继续检查状态码及合成令牌不可见。

## 飞书后续同步

交接后首次复查实际枚举到源 19 个节点；本地 19 篇仍为解析完成。知识库已有 `summary_model_id=local-deepseek-chat`，对应远程模型，与原 helper 要求导入时不使用远程摘要的保护规则不一致。管理员交接未改动此字段，不能推断配置发生的时间或操作者。

helper 现在允许 `verify/snapshot` 只读报告这一差异，并明确记录 `summary_model_configured=true`、`sync_allowed=false`。`sync` 仍在调用数据源同步前拒绝；本轮没有清空摘要设置、触发同步或删除资料。29 项模拟回归及本机实际知识库/数据源只读检查通过。后续导入需要先确认摘要处理策略，不能将本次账号和问答验收理解为已恢复同步。处理方法见 [飞书同步](02-feishu.md)。

修复后完整 `snapshot` 三次遇到接口连接失败，没有生成新快照。单次独立目录诊断的 7 个 GET 均为 200，并再次读到 19 个节点；本机配置检查另行通过，但两项分开检查不等于完整快照命令已通过。失败日志为 `temp/enterprise-trial/feishu-after-handover*.log`。完整快照结果为**未验证**，待连接稳定后执行 `python -B scripts/feishu-space-sync.py snapshot --output temp/enterprise-trial/feishu-after-handover.json` 核对，不能自动重试有写入的同步操作来规避此问题。

## 清理边界

旧账号停用并撤销令牌，旧验收空间及非源知识库软删除。旧会话、文件字节和历史切片仍保留在不可由新账号访问的历史数据及加密备份中；此操作不等于物理擦除。恢复归档及密钥需要分别妥善保存。

飞书源的现有 19 篇资料中仍含专用同步探针“测试文档”。2026-10-10 实际问答检索确认它仍可被检索，用户选择暂时保留，因此本轮不删除、不禁用、不排除该节点。其余 18 篇为业务资料；不能声称当前知识库完全没有测试内容。后续移除需另行处理源节点及同步规则，并核对检索结果。

## 验证

自动检查应覆盖邀请制生效、旧账号失效、源资料保留、唯一有效企业知识库、负责人限定 Viewer 邀请、普通员工拒绝邀请、跨空间拒绝访问、技能可用、实际问答、文件生成、日志令牌脱敏、HTTPS 及隧道健康。

本轮尚未创建第二个普通员工账号；员工无法读取负责人的聊天、文件，以及员工不能继续邀请的运行实测，需要在创建独立员工后完成。后端拒绝用例不替代这项真实身份验收。

需要人工验证：使用独立浏览器接受邀请并设置密码；确认公司名称、中文界面及实际教学/商务产出；用 Office 打开下载文件；在不关闭 Docker 的情况下测试本机锁屏持续访问，以及重启后恢复服务。检查邮件地址的真实控制权和人员身份属于交付步骤，本次模拟账号不会自动发送邮件。
