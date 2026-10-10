# 本机 HTTPS 试用隧道

本方案将 `https://kb.boyantech.xyz` 转发至本机 `127.0.0.1:8090` 的试用网关。运行隧道的电脑、Docker 与网关必须持续在线。关闭电脑、休眠或断网会影响外部访问；此脚本不安装 Windows 服务，也不设置开机启动。

## 文件与访问边界

| 文件或地址 | 用途 |
| --- | --- |
| `scripts/trial-cloudflare.py` | 检查 Cloudflare 现状、准备专用隧道、发布指定主机的 DNS |
| `scripts/run-trial-tunnel.py` | 仅管理本次试用的本机 cloudflared 进程 |
| `temp/enterprise-trial/.env` | 保存 `TRIAL_PUBLIC_HOST`、`TRIAL_CLOUDFLARE_API_TOKEN`、`TRIAL_TUNNEL_TOKEN`，不得提交或分享 |
| `temp/enterprise-trial/tunnel-state.json` | 保存本次隧道标识、准备状态及发布前 DNS 记录 |
| `temp/enterprise-trial/tunnel-process.json` | 保存专用进程 PID、程序绝对路径、Windows 创建时间及隧道标识，不含令牌 |
| `temp/enterprise-trial/cloudflared-runner.yml` | 固定空配置，阻止自动加载其他本机隧道配置 |
| `http://127.0.0.1:49312/ready` | 本机隧道就绪检查，不对外监听 |

程序固定使用 `C:/Users/Yeung/scoop/apps/cloudflared/current/cloudflared.exe`。它不枚举或接管其他 cloudflared 进程，不接受替换程序、端口或令牌的命令行参数。

`TRIAL_TUNNEL_TOKEN` 只在启动时由脚本进程读取，并通过子进程的 `TUNNEL_TOKEN` 环境变量传入。命令行、状态文件和脚本输出均不包含令牌。子进程标准输入、输出、错误流均连接至空设备；启动不显示窗口、不写应用日志文件。只继承必要 Windows 系统环境，不继承其他代理、Cloudflare 凭据或 `TUNNEL_*` 配置。本机有权检查进程环境的管理员仍应被视为可信人员。

启动前校验令牌对应的账户和隧道与已准备状态一致。令牌字段依据 [cloudflared 官方 TunnelToken 定义](https://github.com/cloudflare/cloudflared/blob/master/connection/connection.go)；格式或自定义 Endpoint 不符合当前受控流程时拒绝启动。

## 启动与发布

以下命令在仓库根目录以 PowerShell 7 执行。可将 `$trialPython` 改为本机已安装的 Python 3 路径；不需要安装第三方 Python 依赖。

```powershell
$trialPython = 'D:/CodexData/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $trialPython -B scripts/run-trial-tunnel.py status
```

省略动作也只查询状态。`status` 和 `stop` 不读取 `.env`。

1. 先完成本机试用网关配置。网关 `127.0.0.1:8090` 的认证配置必须为邀请制，且强密码策略已开启。尚未满足时脚本拒绝启动。
2. 在本机 `temp/enterprise-trial/.env` 中设置 `TRIAL_PUBLIC_HOST=kb.boyantech.xyz` 和 Cloudflare API 令牌。不要把令牌复制到终端命令、聊天或文档。`TRIAL_TUNNEL_TOKEN` 由准备步骤写入。
3. 只读查看当前 DNS 和专用隧道。

```powershell
& $trialPython -B scripts/trial-cloudflare.py --action inspect
```

4. 审核输出仅涉及 `kb.boyantech.xyz`，保存其中的 `dns_fingerprint`。将下面参数替换为该指纹再执行。此步骤会创建或配置专用 Cloudflare 隧道，并把令牌写入本机 `.env`，不会切换 DNS。

```powershell
& $trialPython -B scripts/trial-cloudflare.py --action prepare --dns-fingerprint '<审核后的 dns_fingerprint>'
```

5. 启动专用进程，并等待最多约 30 秒获取就绪状态。

```powershell
& $trialPython -B scripts/run-trial-tunnel.py start
& $trialPython -B scripts/run-trial-tunnel.py status
```

已有身份匹配的进程时，重复 `start` 只返回其状态。`RUNNING` 且 `ready=true` 表示本机 `/ready` 已通过，尚不等于外部 DNS 与业务功能均已验证。

6. 获得本机就绪结果后发布 DNS。脚本会重新验证网关、Cloudflare connector 为 healthy、固定路由和 DNS 指纹，且只修改目标主机的一条记录。

```powershell
& $trialPython -B scripts/trial-cloudflare.py --action publish
```

7. 在另一台设备打开 `https://kb.boyantech.xyz/login`。确认 HTTPS、邀请制登录、已授权账号访问及应用功能正常。不得通过关闭邀请制或放宽网关限制排障。

`prepare` 和 `publish` 会修改 Cloudflare 状态，应在已授权的发布操作中执行。2026-10-10 已完成本次准备和发布：专用隧道 `bf0e868f-c191-4ea7-a6ac-1c1c11c55db1` 已连接，仅更新 `kb.boyantech.xyz` 的原 CNAME 记录，旧隧道和其他 DNS 记录未改动。原记录保存在 `tunnel-state.json`，可以按下述流程回滚。

本机及公网网关各 6 项检查通过；公网账号 26 项只读检查、真实 SSE 问答、Word 下载摘要校验及 28 项浏览器检查通过。HTTPS 保持严格证书校验。证据路径与浏览器条件见 [企业试用记录](08-enterprise-trial.md) 和 [团队邀请](trial-team.md)。这些结果不代表已安装 Windows 服务或配置自动启动。

## 状态与停止

```powershell
& $trialPython -B scripts/run-trial-tunnel.py status
& $trialPython -B scripts/run-trial-tunnel.py stop
```

| 状态或错误 | 含义与处理 |
| --- | --- |
| `NOT_STARTED` | 没有本脚本管理的进程记录 |
| `RUNNING` | 记录的进程身份一致，且本机 `/ready` 返回 200 |
| `STARTING_OR_DISCONNECTED` | 进程仍在，但尚未连接成功或连接中断；检查本机网络后再次查询，不要另启一份 cloudflared |
| `STOPPED` | 已记录的进程不存在或已退出；重复停止不会影响其他进程 |
| `tunnel_not_prepared` | 先完成同一主机、同一隧道的 prepare |
| `gateway_auth_not_ready` | 网关未启用邀请制或强密码；修复认证配置后重试 |
| `local_service_unavailable` | 网关未就绪；检查 Docker 与网关服务 |
| `metrics_port_in_use` | 专用端口已被占用；人工检查占用者，脚本不会终止未知进程 |
| `process_identity_mismatch` | PID 可能已被复用，或状态记录与实际进程不符；禁止仅按 PID 强制结束 |
| `process_executable_changed` | 记录的程序路径与当前 cloudflared 路径不一致；先人工核实是否发生软件更新 |
| `tunnel_token_scope_mismatch` | 本机令牌不属于已准备的账户/隧道，或包含不允许的 Endpoint；重新核实 prepare 流程 |
| `runner_config_changed` | 固定空配置被改动；先审核差异，不能通过增加其他路由绕过检查 |
| `runner_busy` | 同一仓库已有管理操作运行；等待完成后再查询 |

退出码 `0` 表示操作完成，`2` 表示进程仍在但未就绪，`1` 表示检查失败。启动失败时返回固定错误码，不输出原始异常或令牌。

停止前脚本在同一个 Windows 进程句柄上核对 PID 对应的程序路径和创建时间，匹配后才终止。停止会中断该隧道的现有连接，但不修改 DNS、不删除隧道、不影响其他 cloudflared。状态文件保留，便于追查；不要为绕过身份检查直接删除或编辑它。

## DNS 回滚

回滚前先保留 `tunnel-state.json`，其中 `original_dns` 是 prepare 首次保存的原记录，`original_dns_fingerprint` 是对应指纹。该文件不含令牌。不得用新的 inspect 结果覆盖原记录，也不要回滚整个 DNS 区域。

1. 执行 `scripts/trial-cloudflare.py --action inspect`，检查当前目标主机记录。它必须仍是本次发布的、开启代理的 CNAME，目标为状态文件中 `tunnel_id` 加 `.cfargotunnel.com`；记录 ID 应与状态文件的 `dns_record_id` 一致。若发现其他人修改过目标、类型、代理状态或记录 ID，先停止回滚并人工确认。
2. 在 Cloudflare 控制台选择 `boyantech.xyz` 的 DNS 管理，仅定位名称为 `kb` 的记录。将其类型、内容、代理状态及 TTL 恢复为 `original_dns` 中保存的值。当前脚本最多允许一条原记录；不要修改其他名称的记录。
3. 如果 `original_dns` 为空，回滚意味着删除本次新增的唯一记录。删除前必须确认当前 ID 和目标均与本次发布记录一致，并取得删除操作授权。
4. 重新运行 inspect，核对名称、类型、内容、代理状态、TTL 与原记录一致。原记录通过原地修改恢复且 ID 未改变时，`dns_fingerprint` 应重新等于 `original_dns_fingerprint`；若控制台重建了记录，ID 会改变，此时需逐字段人工复核并记录新 ID，不能把指纹不一致当作自动通过。
5. DNS 回滚确认完成后运行 `run-trial-tunnel.py stop`。不必删除 Cloudflare 隧道、账户或其他 DNS 记录。若目标是立即停止当前试用访问，可先停止专用进程，再做上述 DNS 回滚。

原 DNS 可能本来就指向已停用的旧隧道。恢复原记录仅恢复发布前的配置，不保证旧站点可用。回滚后再次 publish 会重新切到本次试用隧道，不能用它来验证回滚。

## 验证范围

```powershell
& $trialPython -B -m unittest scripts/test_run_trial_tunnel.py scripts/test_trial_cloudflare.py
```

离线用例验证默认只读、prepare 范围、网关策略、最小子进程环境、令牌不进命令行/输出/状态、令牌账户与隧道绑定、重复启动、PID 复用拒绝、同句柄停止、失败脱敏、等待超时保留进程状态、单条 DNS 发布及竞争修改拒绝。Windows 身份读取测试只查询测试进程自身。

本脚本交付时，真实隧道启动、停止、Cloudflare 发布、外部 HTTPS 访问及 DNS 回滚均为**未验证，需要人工验证**：依次执行上述授权操作，保存不含令牌的状态输出，并在外部设备完成登录与业务功能检查。离线测试没有读取真实 `.env`、启动或停止真实隧道，也没有调用 Cloudflare 写接口。
