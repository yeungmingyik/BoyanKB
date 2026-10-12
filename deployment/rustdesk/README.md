# RustDesk 原生服务

适用 Ubuntu 24.04 和 RustDesk Server OSS 1.1.16。程序、配置、数据集中在 `/opt/rustdesk`，日志使用独立的 journald 命名空间。服务使用专用系统账户 `rustdesk`，登录 shell 为 `/usr/sbin/nologin`。

| 路径 | 内容与权限 |
| --- | --- |
| `/opt/rustdesk/releases/1.1.16` | 官方发布的 `hbbs`、`hbbr`，由 root 管理 |
| `/opt/rustdesk/current` | 指向当前版本目录的符号链接 |
| `/opt/rustdesk/config/.env` | 服务环境变量，root 所有，权限 `0600` |
| `/opt/rustdesk/data` | 服务运行目录，rustdesk 所有，权限 `0700` |
| `/opt/rustdesk/config/journald.conf` | 独立日志命名空间的配额与转发配置，由 root 管理 |
| `/opt/rustdesk/config/firewall.nft` | 仅限制 RustDesk WebSocket 端口的独立 nftables 表 |
| `/opt/rustdesk/systemd` | 本目录的四个 systemd 单位文件，由 root 管理 |
| `/etc/systemd/system/rustdesk*` | 指向单位文件的符号链接及开机启动链接 |
| `/etc/systemd/journald@rustdesk.conf` | 指向 `/opt/rustdesk/config/journald.conf` 的符号链接 |
| `/var/log/journal/<machine-id>.rustdesk` | systemd 管理的 RustDesk 专用日志目录 |

`.env` 必须包含 `RUSTDESK_RELAY`，格式为客户端可访问的服务器地址加 `:21117`，并设置 `RUST_LOG=warn`。`KEY` 存放 64 字节 Ed25519 私钥的 Base64 编码，由两个服务共同使用，无需单独的 `id_ed25519` 文件。文件由 systemd 读取；无需授予服务账户读取权限。真实密钥仅保存在服务器的 `.env` 中，不得复制到仓库、命令参数或终端日志；不要运行打印服务环境变量的命令。

`rustdesk-hbbs.service` 运行连接协调服务；`rustdesk-hbbr.service` 运行中继服务。`rustdesk.slice` 将两者的 CPU 总配额限制为单核的 50%，内存软阈值为 192 MiB、硬上限为 256 MiB，禁止使用交换内存，总任务数上限为 128。CPU 和磁盘 I/O 权重均为 10。中继带宽由 RustDesk 环境变量单独控制；参数 `0.9` 换算约为 `0.944 Mbps`，需要通过实际中继会话验证。

服务仅允许持久写入 `/opt/rustdesk/data`，无法访问 `/opt/boyankb` 和用户主目录。专用临时目录由 systemd 管理。标准输出与错误输出写入 `rustdesk` 日志命名空间，与 BoyanKB 的日志分开。持久日志配额为 16 MiB，运行时日志配额为 8 MiB，禁止向系统日志、内核、控制台和在线用户转发。journald 进程由系统单独管理，不计入应用服务的 256 MiB 配额。

`rustdesk-firewall.service` 维护独立的 `inet rustdesk` 表，仅丢弃 TCP `21118`、`21119` 的 IPv4 和 IPv6 入站连接，不清空现有规则。两个应用服务必须在这项规则生效后启动；停用防火墙服务会连带停止依赖它的应用服务。

## 安装文件与预检

安装包固定为官方 1.1.16 的 `rustdesk-server-linux-amd64.zip`，SHA256 为 `0565c41affe6c3f409b0bddfaf5a24ccbf3f64f5f8e3fec250b69a0d5f6bdbcf`。本机将包保存为 `temp/rustdesk-native/rustdesk-server-linux-amd64-1.1.16.zip`，同目录保存对应版本上游 `LICENSE`。仓库不保存二进制或真实密钥。[官方发布](https://github.com/rustdesk/rustdesk-server/releases/tag/1.1.16)

在本机仓库目录执行以下 Python 命令，使用已经核对主机指纹的腾讯云 SSH 连接。`stage` 仅允许全新 `/opt/rustdesk` 目录，会安装文件并生成环境文件；不会创建系统账户、链接系统配置、启用服务或改变防火墙。目标已存在时拒绝覆盖。

```text
python -B scripts/rustdesk-native.py stage
python -B scripts/rustdesk-native.py check
python -B scripts/rustdesk-native.py validate
```

`check` 校验安装文件摘要、版本、systemd 单位和 nftables 语法。`validate` 在独立网络命名空间启动临时进程，运行目录为 `/opt/rustdesk/validation`，验证结束回收进程；不会让测试端口或规则进入宿主网络。临时数据库保留在验证目录，不与正式 `data` 混用。

也可以在服务器单独执行静态检查：

```bash
sudo systemd-analyze verify /opt/rustdesk/systemd/rustdesk.slice /opt/rustdesk/systemd/rustdesk-firewall.service /opt/rustdesk/systemd/rustdesk-hbbs.service /opt/rustdesk/systemd/rustdesk-hbbr.service
sudo nft --check --file /opt/rustdesk/config/firewall.nft
```

## 启用

经服务器管理员确认后，从本机执行：

```text
python -B scripts/rustdesk-native.py activate
python -B scripts/rustdesk-native.py status
```

`activate` 先重新核对文件与端口，再创建 `rustdesk` 专用账户、系统配置符号链接，启用两个应用服务。它们的依赖关系会启动专属资源 slice、防火墙服务和日志命名空间。已有同名账户、系统单位、端口或防火墙表时停止，不覆盖。它不改变腾讯云安全组；安全组需另外放行 TCP `21115–21117` 和 UDP `21116`。不要重复执行 `activate` 作为重启命令。

启动或初始状态检查失败时，安装程序尝试停止并禁用两个 RustDesk 应用服务，再停止其专用防火墙服务。保留账户、系统链接、程序和环境文件供检查；不自动删除。恢复前核对服务确实停止、没有残留防火墙表，再针对故障修复；不能直接重跑安装覆盖现场。

## 管理与验证

```bash
sudo systemctl is-enabled rustdesk-hbbs.service rustdesk-hbbr.service
sudo systemctl is-active rustdesk-firewall.service rustdesk-hbbs.service rustdesk-hbbr.service
sudo nft list table inet rustdesk
sudo systemctl show rustdesk.slice -p CPUQuotaPerSecUSec -p CPUWeight -p MemoryHigh -p MemoryMax -p MemorySwapMax -p MemoryCurrent -p TasksMax -p TasksCurrent -p IOWeight
sudo ss -lntup
sudo stat -c '%a %U:%G %n' /opt/rustdesk/config/.env /opt/rustdesk/data
sudo journalctl --namespace=rustdesk --disk-usage
sudo systemd-analyze security rustdesk-hbbs.service rustdesk-hbbr.service
```

使用原生客户端时，仅开放 TCP `21115`、`21116`、`21117` 与 UDP `21116`。程序也会监听 WebSocket 端口 TCP `21118`、`21119`，防火墙无需放行。客户端填写连接协调服务器地址和服务器公钥。分别验证正常直连与强制中继，观察 `rustdesk.slice` 的内存占用、CPU 用量、出口带宽及 BoyanKB 响应时间。

```bash
sudo systemctl restart rustdesk-hbbs.service rustdesk-hbbr.service
sudo systemctl stop rustdesk-hbbs.service rustdesk-hbbr.service
sudo systemctl disable rustdesk-hbbs.service rustdesk-hbbr.service
```

停止和禁用命令仅作用于 RustDesk，不会删除程序、配置或数据。
