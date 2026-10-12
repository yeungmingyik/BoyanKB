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

两服务共享 0.5 核 CPU 上限、192MiB 内存软阈值和 256MiB 硬上限，禁止使用 swap；专用日志持久配额 16MiB。中继参数设为 `0.9`，约 0.944Mbps 的双向载荷总量，不包含 TCP/IP 开销；公网单会话协议测试通过，实际桌面体验仍需两端客户端验证。

## 网络

原生客户端需要 TCP `21115`、`21116`、`21117` 和 UDP `21116`。它们与 BoyanKB 的 TCP `443`、本机诊断端口 `18080` 不重叠。腾讯云安全组需单独放行所需端口。

程序原生会额外监听 TCP `21118`、`21119`；专属 nftables 表 `inet rustdesk` 对 IPv4、IPv6 丢弃这两个端口的入站连接。配置不清空宿主规则、不启用 UFW、不更改 Docker 网络。两个应用依赖防火墙服务，其规则加载失败时应用启动失败；此依赖不会持续监测运行期间被其他操作删除的规则。

客户端的 ID 服务器填写 `193.112.17.218:21116`，中继服务器填写 `193.112.17.218:21117`，API 服务器留空，公钥取 `RUSTDESK_PUBLIC_KEY`。本机 `temp/rustdesk-native/client/.env` 已保存这三个客户端字段，仅含公钥，不含服务端私钥，并由 Git 忽略。两端客户端使用同一服务端配置。不得将服务器环境文件中的 `KEY` 私钥提供给客户端或写入日志。

2026-10-12 用户完成云侧放行后，公网原生 TCP、UDP 和双向中继协议测试均通过。这台服务器关联的腾讯云安全组或实例防火墙需要保留以下入站规则：

| 协议 | 端口 | 用途 |
| --- | --- | --- |
| TCP | 21115–21117 | NAT 检测、连接协调、中继 |
| UDP | 21116 | 注册与心跳 |

来源覆盖两端实际出口地址；使用固定出口时可限定对应 CIDR。安装脚本未修改云侧规则，放行由用户在云控制台完成。不要放行 21118、21119，也不需要修改 BoyanKB 的 443 规则。

CVM 操作入口为目标实例关联的安全组，在入站规则中选择添加规则、自定义，分别填写 `TCP:21115-21117` 和 `UDP:21116`，策略均为允许。两端使用变化的公网出口时，来源可设 `0.0.0.0/0`；固定出口可分别限定两端公网 IP 的 `/32`。保留已有规则，新增允许规则应位于匹配范围内的拒绝规则之前。[腾讯云添加安全组规则](https://cloud.tencent.com/document/product/213/112614)

若实例属于轻量应用服务器，则进入实例详情的防火墙页签，选择添加规则、自定义，分别选择 TCP 与 UDP 并填写上述端口。规则保存后生效，无需重启实例。[腾讯云实例防火墙](https://cloud.tencent.com/document/product/1207/44577/)

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

当前已完成正式启用和公网 TCP、UDP、中继协议验收。两端客户端进入设置中的网络、ID/中继服务器，填写上文配置；控制端输入被控端的 RustDesk ID 发起连接，并由被控端接受或输入其访问密码。仍需通过真实客户端验证桌面直连和中继体验；高峰共存负载未验收。

管理、停止和故障处理见 [安装配置说明](../deployment/rustdesk/README.md)。

## 验证记录

2026-10-10：官方 1.1.16 安装文件已写入 `/opt/rustdesk`；安装包及三个可执行文件 SHA256 校验通过。私钥在服务器生成并仅写入 root 所有、0600 权限的环境文件。没有创建系统账户、接入系统单位、启用自启动或改变宿主防火墙。

`check` 通过二进制版本与完整性、四个 systemd 单位及 nftables 语法检查。`validate` 在独立网络命名空间启动 hbbs/hbbr：三个原生 TCP 端口可连、UDP 21116 已绑定；WebSocket 21118/21119 监听存在，加载专属规则后无法连接，原生 TCP 仍正常。两个测试进程退出并完成回收，捕获日志不含私钥或公钥。

2 秒空闲样本的两进程 RSS 合计 10,309,632 字节，约 9.83MiB；CPU 合计约单核 0.5%。这不代表正式远程会话、并发或 BoyanKB 共存时的资源峰值。截至 2026-10-10，正式激活、公网端口、客户端连接及中继吞吐量尚未验证。

2026-10-12：用户确认后执行 `activate`，创建专用系统账户，接入 systemd 与日志配置，启用 hbbs/hbbr 及开机自启，激活专属防火墙表。后续 `status` 确认两个服务均为 active/running、enabled，重启次数均为 0，资源 slice 的 CPUQuota 为 500ms/s、MemoryMax 为 268435456 字节、MemorySwapMax 为 0。

通过进入两个进程的挂载命名空间并使用 `rustdesk` 身份检查，确认无法读取 `/opt/boyankb`，自身 `data` 可写，程序可读可执行但不可写，环境文件不可直接读写；systemd 注入的运行密钥与受控环境文件一致，三个带宽参数均为 `0.9`。独立日志命名空间已建立，采样占用 4MiB，检查时日志不含公钥或私钥。

使用服务器内网地址执行原生协议测试：UDP 21116 对无效短注册请求返回预期响应且不创建设备；中继拒绝错误公钥，正确公钥可配对并双向转发。1MiB 双向载荷传输用时 11.813 秒，平均 710116bit/s，低于约 943718bit/s 配置上限。该样本验证当前单会话路径，不代表并发总限速或桌面实际体验。

启用前后 BoyanKB 九个容器 ID 一致，全部 healthy，启动时间仍为 2026-10-10，重启次数均为 0。公网 `/login` 返回 HTTP200，TLS 校验通过。本次未执行业务问答或模型付费调用，也未做高峰共存压测。

首次公网验证时，本机 FlClash 虚拟网卡使仅凭 TCP connect 返回成功的检查产生误判。直接指定物理 WLAN 出口后，SSH22 和 HTTPS443 可达，RustDesk TCP21115–21117 与 UDP21116 超时；服务端短时计数未收到相关公网探测，用户随后确认云侧端口未放行。物理出口测试只设置测试 socket 的发送接口，没有修改系统路由或关闭代理。[Windows socket 接口选项](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options)

用户确认已放行后，使用同一物理出口复测：TCP21115、21116、21117 可达，UDP21116 返回预期协议响应；错误公钥被拒绝，正确公钥可配对并双向转发。1MiB 双向载荷用时 11.438 秒，平均 733428bit/s，低于配置上限。TCP21118、21119 仍不可达。同期 BoyanKB 公网 `/login` 返回 HTTP200，TLS 校验通过，九个容器仍全部 healthy、ID 和启动时间未变、重启次数均为 0；两个 RustDesk 服务保持 active/running、enabled，重启次数均为 0。

私有验收报告位于本机 `temp/rustdesk-native/`：`pre-activation-20261012.json`、`activation-verification-20261012.json`、`internal-protocol-20261012.json`、`public-ingress-20261012.json`、`public-protocol-after-firewall-20261012.json`、`boyankb-after-public-verification-20261012.json`。本次通过公网原生协议验证服务，未操作真实桌面客户端，未重启整台服务器验证自启动。
