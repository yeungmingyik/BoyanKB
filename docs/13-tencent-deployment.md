# 腾讯云内部测试部署

目标主机为 `193.112.17.218`，广东地域，Ubuntu 24.04、x86_64、4 核 8GB、5Mbps。部署目录为 `/opt/boyankb`，Compose 项目为 `boyankb-tencent`。本机 `weknora-local` 保留，Cloudflare 隧道和域名解析不切换。

本方案用于受限来源的内部测试。公开 IP 不是正式对外服务免备案的替代方案；正式合作伙伴上线前应完成腾讯云要求的备案与服务合规检查。[腾讯云备案 API 常见问题](https://cloud.tencent.com/document/api/243/19630)、[备案常见问题](https://cloud.tencent.com/document/product/243/18910)。

## 入口和账号

预定入口：`https://193.112.17.218`。首次证书签发和公网访问须通过后方可交付，配置文件存在不代表服务已经上线。

| 账号 | 邮箱 | 空间 | 权限 |
| --- | --- | --- | --- |
| 刘伟健 | `liuweijian@boyantech.xyz` | 博研文化总部 | 系统管理员、空间 Owner |
| 冯伟杰 | `fengweijian@boyantech.xyz` | 博研文化总部 | Viewer，可问答和使用已授权业务技能 |

初始密码在本机 `temp/tencent-cloud/.env` 的 `ADMIN_PASSWORD`、`MEMBER_PASSWORD`，不写入本文、Git 或日志。首次登录后分别修改密码。服务器 `.env` 仅供交接与验收脚本使用，`.env.tencent` 为容器运行配置；二者均须 root 所有、0600 权限。

新账号使用新 UUID。交接只发生在云副本，先验证新账号，再停用旧 `aldream.lau@gmail.com` 与 `testpartner001@gmail.com`，撤销旧令牌和未接受邀请。旧个人聊天不改归属；原生空间共享的 API/embed 频道历史保留，Owner 仍可能按原生权限查看。原星辰教育空间及历史数据不做硬删除，两个新账号只加入总部。

企业知识库、19 篇文档、图谱、模型、三个受管业务智能体和九项技能保留。飞书测试文档按用户要求保留，不能声称资料中已不存在测试内容。飞书数据源保持暂停和无定时同步，避免本机与云端同时消费修改。

## 网络与资源

仅发布 TCP 443 和受管理电脑 IP 限制的 SSH 22。数据库、Redis、Ollama、Neo4j、解析及渲染服务无宿主端口。`127.0.0.1:18080` 是服务器本地诊断入口。

443 来源允许所有 IPv4 地址，以便 Let's Encrypt 执行 TLS-ALPN-01 验证。网页另外由 `WEB_ALLOWED_CIDRS` 限制，默认 `218.104.190.149/32`，不信任客户端伪造的代理来源头。办公出口变化需同时检查 SSH 来源和网页白名单。80 与 UDP 443 无需开放。

Caddy 使用真实 IP SAN 证书，shortlived profile 有效期约160小时，证书卷持久化；首次成功签发不证明续期已验证。保持443可达，后续核对证书序列号与有效期更新。[Let's Encrypt IP 证书](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)。

九服务内存上限合计7072MiB；这仅适合当前小规模、无多人并发测试。批量导入、图谱抽取、Office 生成错峰执行，需观察宿主可用内存、swap、OOM 和磁盘。通用代码沙箱仍关闭，技能目录保存与已授权业务文档生成可用，不代表所有任意代码技能都能执行。

## 迁移顺序

1. 使用专用 root SSH 公钥，核对控制台提供的 ED25519 主机指纹。执行 `scripts/tencent-ssh.py pin-host --fingerprint <控制台指纹>` 和 `preflight`。不要关闭 StrictHostKeyChecking。
2. 安装 Docker 和 Compose，记录 `/opt/boyankb/host-packages.lock`。不替换已有其他部署，不执行全系统升级。
3. `scripts/tencent-images.py --output temp/tencent-cloud/images-20261010.tar.gz` 导出九个已锁定镜像；对照 `deployment/tencent/images.lock.json`，包括自定义前后端及图谱版本。
4. `scripts/tencent-snapshot.py plan` 检查源栈九服务、七卷、空闲队列、暂停同步、数据库与资料范围。使用新输出目录执行 `backup --quiesce --output <目录>`；短暂停止写入者，完成后恢复原服务。
5. 快照包含 PG custom dump、本地对象文件、Redis AOF、Neo4j 数据、Ollama 模型、白名单运行环境和两个绑定配置。逐文件 SHA256 与卷内路径清单校验。快照目录含密钥和业务数据，受本机 ACL 保护，传输使用 SSH；此格式未做静态加密，不能称为加密离线备份。
6. 用 `scripts/tencent-transfer.py upload --source <文件> --destination <incoming相对路径>` 传输，支持 `.partial` 续传和最终 SHA256 验证。不要上传 SSH 私钥、完整本机环境文件或 Cloudflare 管理令牌。
7. 云端加载镜像后复验九个 Image ID、平台及摘要引用可解析。无浮动版本回退、无自动拉取。
8. `scripts/tencent-restore.py` 默认只验证；显式恢复仅允许新云端卷，拒绝覆盖已有容器或卷。先验证卷清单和数据库摘要，再启动业务服务。失败保留目标供诊断，不自动删库或删卷。
9. 制作并验证交接前 PG 备份，再运行 `scripts/tencent-accounts.py` 生成计划；使用输出的计划摘要及备份摘要显式 apply。旧账号只在新账号登录、角色和资料保护检查通过后停用。
10. 初始化专用证书卷，叠加 IP HTTPS 配置启动；具体命令见 [部署配置说明](../deployment/tencent/README.md)。实际验证证书、来源限制、注册关闭、技能、问答和文件。

## 验证与回退

```bash
python3 deployment/tencent/validate-config.py
python3 deployment/tencent/validate-ip-https.py
docker compose --env-file .env.tencent -f docker-compose.tencent.yml -f docker-compose.tencent-ip-https.yml ps
python3 scripts/tencent-acceptance.py readonly
python3 scripts/tencent-acceptance.py exercise
curl --noproxy '*' -fSI https://193.112.17.218/login
```

`readonly` 会登录和读取授权资料，不创建业务内容；登录本身会生成认证记录。`exercise` 以新成员身份创建中文业务会话、问答和 Word/PPT 文件并消耗模型额度，保留输出与报告，不自动删除。生成必须经过真实大纲确认和审校，不能绕过阻断。脚本的自动通过不能代替事实审查和原生 Office 打开检查。

需要人工验证：分别用两个账号在独立浏览器会话登录，核对仅总部成员关系、技能可用、成员不能管理团队；用 Word/PowerPoint 打开产物确认无修复提示及版面正常；逐条核对回答与来源；用知识库没有的问题确认明确说明缺少依据。从未加入白名单的另一网络访问应403，即使伪造转发头也应拒绝。续期窗口后再次核验证书更新。

回退优先停止云端入口并继续使用原本机部署；不把云端修改覆盖回本机。数据库恢复、删除卷或切换生产入口前再次确认具体目标与影响。不可用 `docker compose down -v` 清理失败现场。

## 执行记录

2026-10-10：已核对 SSH 主机指纹并取得 root 访问；已安装 Docker 29.9.0、Compose 5.6.0。基础 Compose、HTTPS 覆盖和 Caddy 静态校验通过。镜像传输、整栈恢复、账号交接及公网 HTTPS 尚待运行结果更新。
