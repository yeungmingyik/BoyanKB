# RustDesk 原生安装

目标主机：`193.112.17.218`，Ubuntu 24.04 x86_64。用途：偶尔控制一台电脑。版本固定为 RustDesk Server OSS 1.1.16，使用官方 Linux 二进制，不使用 Docker。

## 文件与运行隔离

| 位置 | 用途 |
| --- | --- |
| `/opt/rustdesk/releases/1.1.16` | 固定版本程序，保留 SHA256 校验 |
| `/opt/rustdesk/config/.env` | 私钥、公钥及运行参数，仅 root 可读写 |
| `/opt/rustdesk/data` | 正式数据库，专用 rustdesk 用户拥有 |
| `/opt/rustdesk/validation` | 隔离测试数据，与正式数据库分开 |
| `/opt/rustdesk/systemd` | 两个应用服务、资源 slice、专用防火墙服务 |
| `/opt/rustdesk/config/journald.conf` | 独立日志命名空间配额 |
| `/opt/rustdesk/LICENSE` | RustDesk Server 上游许可证 |
| `/var/log/journal/<machine-id>.rustdesk` | RustDesk 专用日志，不混用 BoyanKB 日志 |

systemd 和 journald 的接入使用 `/etc/systemd` 下指向上述配置的专用符号链接。服务账户禁止登录；应用运行时无法访问 `/opt/boyankb` 和用户主目录，仅可持久写入自己的 `data`。不复用 BoyanKB 的程序、配置、数据库、卷或账户。

两服务共享 0.5 核 CPU 上限、192MiB 内存软阈值和 256MiB 硬上限，禁止使用 swap；专用日志持久配额 16MiB。中继参数设为 `0.9`，约 0.944Mbps 的双向载荷总量，不包含 TCP/IP 开销；实际会话吞吐量仍需验证。

## 网络

原生客户端需要 TCP `21115`、`21116`、`21117` 和 UDP `21116`。它们与 BoyanKB 的 TCP `443`、本机诊断端口 `18080` 不重叠。腾讯云安全组需单独放行所需端口。

程序原生会额外监听 TCP `21118`、`21119`；专属 nftables 表 `inet rustdesk` 对 IPv4、IPv6 丢弃这两个端口的入站连接。配置不清空宿主规则、不启用 UFW、不更改 Docker 网络。两个应用依赖这项规则，规则未生效时不能启动。

客户端的 ID 服务器填写 `193.112.17.218`，中继服务器填写 `193.112.17.218:21117`，公钥取环境文件的 `RUSTDESK_PUBLIC_KEY`。不得将 `KEY` 私钥提供给客户端或写入日志。客户端连通前还需完成原生服务激活与公网端口验证。

## 操作与验证

在本机仓库目录使用 Python：

```text
python -B scripts/rustdesk-native.py stage
python -B scripts/rustdesk-native.py check
python -B scripts/rustdesk-native.py validate
```

`stage` 仅写入新建的 `/opt/rustdesk`，已有目录时拒绝覆盖。`check` 核对安装文件与官方二进制摘要、版本、systemd 和防火墙语法。`validate` 在隔离网络命名空间启动临时进程并验证端口、专用防火墙、日志不含密钥及进程回收，不让测试端口或规则进入宿主网络。

按项目规则确认系统配置变更后，启用并查看：

```text
python -B scripts/rustdesk-native.py activate
python -B scripts/rustdesk-native.py status
```

正式启用后，还需从公网验证 TCP 与 UDP、通过两个客户端验证直连和中继，并在 BoyanKB 全栈运行时对比响应与资源峰值。隔离启动成功不代表真实远程会话、公网连通或共存负载已经通过。

管理、停止和故障处理见 [安装配置说明](../deployment/rustdesk/README.md)。

## 验证记录

2026-10-10：官方 1.1.16 安装文件已写入 `/opt/rustdesk`；安装包及三个可执行文件 SHA256 校验通过。私钥在服务器生成并仅写入 root 所有、0600 权限的环境文件。没有创建系统账户、接入系统单位、启用自启动或改变宿主防火墙。

`check` 通过二进制版本与完整性、四个 systemd 单位及 nftables 语法检查。`validate` 在独立网络命名空间启动 hbbs/hbbr：三个原生 TCP 端口可连、UDP 21116 已绑定；WebSocket 21118/21119 监听存在，加载专属规则后无法连接，原生 TCP 仍正常。两个测试进程退出并完成回收，捕获日志不含私钥或公钥。

2 秒空闲样本的两进程 RSS 合计 10,309,632 字节，约 9.83MiB；CPU 合计约单核 0.5%。这不代表正式远程会话、并发或 BoyanKB 共存时的资源峰值。正式激活、公网端口、客户端连接及中继吞吐量尚未验证。
