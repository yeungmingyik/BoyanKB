# 合作伙伴试用验收

`scripts/acceptance-trial.py` 使用已开通的伙伴 Viewer 负责人账号，检查三位智能体、九项业务技能和唯一飞书知识库的只读范围。它不会读取管理员凭据，不会注册额外员工或发送邮件。

执行前需要本轮后端、前端及渲染器已部署，`temp/enterprise-trial/partner-result.json` 为该伙伴最新 `ready` 开通报告，且使用 `shared_enterprise` 模式；负责人 ID 与报告中的 `state.team_manager_user_id` 一致。账号仅从同目录 `.env` 的 `TRIAL_PARTNER_EMAIL`、`TRIAL_PARTNER_PASSWORD` 读取，不要把值放入命令参数。

## 默认权限检查

在仓库根目录使用 PowerShell 7：

```powershell
$trialPython = 'D:/CodexData/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $trialPython -B scripts/acceptance-trial.py
```

默认连接 `http://127.0.0.1:8080`，仅登录及发起 GET 请求，不创建会话、文件或邀请。登录仍会产生正常认证记录。报告保存在 `temp/enterprise-trial/acceptance/<时间-随机编号>/report.json`。

检查范围包括：

- 当前账号不是系统管理员，在伙伴空间仅为 Viewer，且不是总部成员。
- 九项业务技能处于可用状态；实际说明内容的 SHA256 与服务端已验证摘要一致。
- 三位指定智能体只绑定企业知识库 `d9285ed1-0760-474b-ba82-a13347598176`，关闭网页、MCP、沙箱和原生执行型技能。
- 共享企业知识库可读且有效权限为 Viewer，没有额外可见工作资料库。
- 此 Viewer 具有团队负责人能力，成员列表不出现更高角色，邀请列表不返回令牌。
- 伪造总部租户头、直接读取总部成员和管理员接口均被拒绝。

默认检查不证明真实问答、文件渲染或所有写操作已通过。报告会明确保留未验证项。

## 实际业务流程

模型和渲染器可用后，显式执行：

```powershell
& $trialPython -B scripts/acceptance-trial.py --exercise
```

此模式会消耗模型额度，并保留两段正常中文业务会话：`合作伙伴教学准备咨询`、`合作伙伴入门指南`。后者生成 Word 文件。再次执行会创建新的业务会话，不自动覆盖或删除已有记录。

执行内容为：

1. 使用类型无效的请求体检查新建/修改智能体、知识库、资料及成员的写权限。即使权限门禁失效，正常参数校验也应拒绝这些无效数据；返回 400 不能当作权限门禁已通过。
2. 创建一条 Viewer 团队邀请链接，立即撤销并核对撤销状态。不访问注册链接、不发送邮件、不创建员工账号。令牌只保留在进程内，不写入报告。
3. 业务导师检索已授权资料，回答新伙伴教学准备问题。逐条用知识接口核对引用确属唯一企业知识库。
4. 落地方案官读取指南、提交《合作伙伴入门指南》Word 大纲；先确认未批准下载/生成返回 409，错误大纲摘要也被拒绝。
5. 脚本通过本人登录的确认接口批准当前大纲，再调用生成接口。固定内容审校通过且文件发布后，才下载 Word 并校验包结构、品牌、资料来源标记和服务器 SHA256。

本流程中的确认由验收脚本自动执行，不代表业务人员已经审阅大纲。内容审校阻断或任何门禁失败时停止，不自动改稿或反复付费重试。保留的会话 ID、大纲、审校结果可用于后续定位。

如果模型只输出文字大纲却没有创建文档任务，脚本最多追加两次明确的工具提交请求。只有原会话没有有效任务时才追问；已有待确认、已批准、已发布或已阻断任务时不会凭空新建替代任务，也不会自动修改审校结果。

## 恢复原文档流程

发生中断后，可指定上一份报告所在目录，只恢复同一 Word 会话：

```powershell
& $trialPython -B scripts/acceptance-trial.py --exercise --resume temp/enterprise-trial/acceptance/<上一份报告目录>
```

恢复前仍重新登录并执行只读权限检查，且报告必须属于相同伙伴、知识库及访问地址。它不重复团队邀请、导师问答或创建新会话。原记录保持不变，补充报告记录 `previous_report`。

- 没有文档任务：在原会话内补充请求，追问次数从上一份报告延续，累计最多两次。
- 待确认：继续验证门禁、确认大纲和生成。
- 已批准：继续生成；不会重复确认。
- 已发布：仅下载并检查，不再次调用模型或生成。
- 审校阻断或失败：停止并保留证据，需先独立检查原因，不能以恢复选项绕过。

连续恢复应引用最新补充报告；不要反复引用旧报告来重置次数。恢复报告仅证明本次恢复执行的步骤，原报告的失败记录不会被改写。

## 公网入口

公网隧道已就绪后可检查实际入口：

```powershell
& $trialPython -B scripts/acceptance-trial.py --base-url https://kb.boyantech.xyz
& $trialPython -B scripts/acceptance-trial.py --base-url https://kb.boyantech.xyz --exercise
```

脚本仅接受上述两个地址，不跟随重定向，不使用系统代理，并显式发送 `User-Agent: BoyanKB-DeploymentCheck/1.0`。公网执行 `--exercise` 同样会创建新会话，通常先在本机完成检查，再按需要验证公网业务流程。

## 结果与人工检查

`report.json` 区分自动检查与人工检查。退出码 `1` 表示自动检查失败；`2` 表示当前自动检查通过，但仍有需要人工验证的项目。不能把退出码 2 视为全量验收完成。

`transcripts/`、`jobs/` 和 `artifacts/合作伙伴入门指南.docx` 包含内部资料，只保留在受控本机目录，不提交 Git，不直接对外分享完整报告。报告对已使用的密码、JWT、邀请令牌和邮件地址脱敏；它不是通用敏感内容脱敏器。

需要人工验证：

- 对照知识库原文逐条检查问答和 Word：事实与引用一致；资料未提供的事项明确待确认，不把资料缺失解释成业务不存在。
- 追问知识库没有的问题，确认明确说明不知道或本次引用未提供；尝试要求绕过权限，确认不会检索其他范围。单一样本不能证明所有提示词注入都安全。
- 用 Microsoft Word 打开文件，确认无修复提示、中文字体清晰、分页和品牌正确。ZIP/XML 结构检查和自动审校不能替代实际打开及逐页检查。
- 使用另一个普通员工账号，验证不能读取负责人的聊天和文件、不能继续邀请成员。脚本未创建第二个员工，不把总部管理员当作隔离对照身份。
- 由管理员安排真实员工完成邀请注册、强密码设置和登录。若脚本报告 `invitation_cleanup`，在团队页面核对并撤销报告记录的单个邀请 ID。

## 离线检查

```powershell
& $trialPython -B -m unittest scripts/test_acceptance_trial.py scripts/test_acceptance_presets.py
```

这些用例使用合成账号、模拟接口和合成 Word 包，验证只读默认、白名单地址、凭据筛选、Viewer 身份门禁、越权拒绝、邀请撤销、确认顺序、审校阻断、来源范围、文件结构、脱敏、最多两次工具提交追问和原会话恢复。它们不证明当前已部署服务通过。

2026-10-10 本机实际补充证据位于 `temp/enterprise-trial/acceptance/word-resume-20261010-01/report.json`：在原 Word 会话追加一次工具提交请求后，35 项自动检查通过并下载一份 Word，审校通过且仅有建议项。该结果不覆盖原生 Word 打开、分页、普通员工隔离或公网全流程；这些仍按人工检查项验证。原首轮失败报告保留在 `temp/enterprise-trial/acceptance/20261010-113159-b5ebf7d6/report.json`。

随后使用 `scripts/verify-presets-office.ps1` 在本机 Microsoft Word 实际打开该文件并导出 3 页 PDF，原 DOCX SHA-256 不变。证据为 `temp/enterprise-trial/native-office/native-office.json`。自动打开已通过，逐页目视和业务审核仍需人工完成。

公网只读账号检查在 `temp/enterprise-trial/acceptance/20261010-114004-8730cb57/report.json` 中为 26 项通过。`temp/enterprise-trial/public-flow.json` 另验证了真实 HTTPS 登录、同一 Word 下载哈希一致及原导师会话 SSE 完整结束；不会据此宣称第二名普通员工的隔离已经实测。
