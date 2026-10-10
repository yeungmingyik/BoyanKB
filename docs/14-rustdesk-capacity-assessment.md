# RustDesk 同机部署容量评估

评估日期：2026-10-10。用途：偶尔远程控制一台电脑。目标：复用 BoyanKB 腾讯云服务器，降低对知识库服务的影响。

## 结论

可以考虑同机部署 RustDesk Server OSS，先按单路办公远程控制配置资源和中继带宽上限。现阶段没有必要仅为这一用途另购服务器，但共享宿主和公网出口无法承诺完全无影响。正式启用前须在 BoyanKB 全栈运行时验证。

服务器部署的是独立的 `hbbs` 注册与连接协调服务、`hbbr` 中继服务；控制端和被控电脑安装 RustDesk 客户端。无需给云服务器安装桌面环境或 GPU，也无需修改 WeKnora 核心代码。[服务端仓库](https://github.com/rustdesk/rustdesk-server)

## 配置与实测

现有部署文档记录 Ubuntu 24.04、4 核、8GB、5Mbps。九个 BoyanKB 服务的内存上限合计 7072MiB，CPU 配额上限合计 8.75 核；这些是各容器上限之和，不是实际占用或保证资源。[腾讯云部署](13-tencent-deployment.md)、[资源配置](../docker-compose.tencent.yml)

本次通过已有、校验主机指纹的 SSH 连接只读采集：

| 指标 | 结果 |
| --- | --- |
| CPU | 4 个逻辑处理器；1 秒样本忙碌率 3.2% |
| 宿主可见内存 | 7685.2MiB |
| 可用内存 | 7021.1MiB |
| Swap | 总计 1988.0MiB，空闲 1987.7MiB |
| 根分区 | 59GiB，空闲约 44GiB |
| 运行中的 Docker 容器 | 0 |
| RustDesk 默认端口 | 当前未监听 |

该样本没有覆盖 BoyanKB 运行负载，不能据此认定可用内存始终充足。如果九个 BoyanKB 容器同时达到配置内存上限，宿主可见内存仅剩约 613MiB，还需容纳系统及其他进程。

## RustDesk 资源规划

官方说明服务端 CPU、内存要求很低，基础云服务器即可运行，没有给出统一的硬性最低核数和内存。[官方安装说明](https://rustdesk.com/docs/en/self-host/rustdesk-server-oss/install/)

以下为本用途的初始工程预算，属于待验证的部署建议：

| 资源 | 初始建议 |
| --- | --- |
| CPU | 两个服务合计最多 0.5 核；不足时再评估 |
| 内存 | 两个服务合计最多 256MiB；观察连接、稳定性和 OOM |
| 磁盘 | 预留 1–2GiB 用于镜像、数据和受限日志 |
| 中继带宽 | 总量先限制在约 1Mbps，并验证限速实际生效 |

优先允许 P2P 直连。两端直连成功后，桌面数据不经云端中继；直连失败时，画面和文件传输会消耗这台服务器的带宽。官方给出的普通办公参考流量约 100KB/s，折合约 0.8Mbps；高动态 1080p 场景可明显超过现有 5Mbps 出口。1Mbps 中继额度适合先验证普通办公，不保证视频、大文件或高画质流畅。[官方流量说明](https://rustdesk.com/docs/en/self-host/rustdesk-server-oss/install/)

服务端具备中继总量和单连接限速参数。部署时须针对固定版本检查单位与实际效果；Docker CPU、内存限额本身不会限制网络带宽。[中继实现](https://github.com/rustdesk/rustdesk-server/blob/master/src/relay_server.rs)

## 隔离方案

- 使用独立目录 `/opt/rustdesk`、独立 Compose 项目、数据卷和网络，不连接 BoyanKB 后端网络。
- 为两个服务设置 CPU、内存、进程数和日志大小上限，固定镜像版本或摘要。
- 原生客户端需要 TCP 21115、21116、21117 和 UDP 21116。它们与 BoyanKB 配置中的 TCP 443、回环 18080 及 SSH 22 不冲突。
- 此用途无需 Pro 管理端口 21114，也无需网页客户端端口 21118、21119；不新增这些公网入口。
- BoyanKB 的 Compose、数据库、网关、证书和卷保持独立。实际部署涉及服务器及防火墙配置变更，按项目约束另行确认后执行。

端口依据：[官方 Docker 部署说明](https://rustdesk.com/docs/en/self-host/rustdesk-server-oss/docker/)。

## 验证方法

1. 在 BoyanKB 九服务运行后，采集空闲及文档导入、问答、Office 生成时的 CPU、MemAvailable、Swap、OOM 和公网流量基线。
2. 核对 RustDesk 端口没有冲突、安全组放行符合需要、容器资源上限及日志上限生效。
3. 用控制端和被控电脑完成实际连接，确认连接使用直连还是中继；两种路径分别验证。
4. 中继会话持续进行办公操作，并验证带宽上限；同时对比 BoyanKB 登录、问答和文件下载体验与基线。
5. 若出现宿主 OOM、持续换页、BoyanKB 明显变慢或网络排队，先停止 RustDesk 专用服务，再调整预算或迁移到独立服务器。

本次完成官方资料、仓库配置和服务器只读检查；未部署 RustDesk，未改变服务器、防火墙或 BoyanKB 配置，未进行共存负载验证。
