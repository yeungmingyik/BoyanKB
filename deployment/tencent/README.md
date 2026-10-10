# 腾讯云单机内部测试配置

`docker-compose.tencent.yml` 用于 Ubuntu 24.04、linux/amd64、4 核 8GB 的独立部署。使用九个已验收版本镜像，不自动拉取或构建，不覆盖旧生产或本机 Compose。

入口固定绑定 `127.0.0.1:18080`。数据库、Redis、Ollama、Neo4j、文档解析和渲染服务没有宿主端口。公网 IP 与 `PUBLIC_ORIGIN` 仅用于生成链接，不代表已开放公网入口或 HTTPS。

`env.example` 只包含占位值。真实部署由受控迁移程序生成服务器专用环境文件，再通过 `--env-file` 指定。不要上传完整试用辅助环境文件或 Cloudflare API 管理令牌。已有数据库须保留原 `SYSTEM_AES_KEY`；恢复 Neo4j 时使用原图数据库密码。其他密钥更换须同步更新其消费者。

## 静态校验

```bash
python3 deployment/tencent/validate-config.py
```

校验仅使用示例值，检查九服务、镜像锁、回环端口、网络隔离、只读配置挂载、沙箱和自动迁移开关以及资源上限，不启动业务服务，不读取真实环境文件。镜像清单在 `images.lock.json`；传输镜像后应额外验证目标机实际镜像 ID 和摘要引用。

## 完成数据恢复后启动

```bash
docker compose --env-file .env.tencent -f docker-compose.tencent.yml up -d --no-build --pull never --wait --wait-timeout 600
docker compose --env-file .env.tencent -f docker-compose.tencent.yml ps
```

启动前必须已向新卷恢复数据库、对象文件、Redis、Neo4j 和 Ollama 模型，并迁移 `config/config.yaml`、`config/builtin_models.local.yaml`。`AUTO_MIGRATE=false`，本配置不会初始化或升级应用 schema。Ollama 仅接内部网络，模型须从本机缓存迁移，不能依赖容器在线下载。

公开注册关闭，账号由管理员邀请。原生技能目录接口保留，任意技能执行、会话沙箱、平台管理和本机浏览器接口由入口限制。业务文档生成通过独立内部渲染服务执行。

## 资源与访问边界

容器内存上限合计 7072MiB，为宿主机保留约 1GiB。每类异步工作池配置为 1，模型调用并发为 2，Ollama 同时最多一个模型和一个请求，解析并发为 1；已有 renderer 使用串行 HTTPServer。这些上限不等于所有后台任务合计只有一个。数据库中的系统设置优先于同名环境配置，迁移后须核对是否存在并发覆盖。

批量导入、图谱抽取和 Office 生成错峰执行。大文档解析或复杂文件生成可能触及内存限制；真实内存峰值、云端恢复和问答质量仍须运行验收，不能用静态校验代替。旧七服务整栈备份脚本不适用本九服务配置。

Web 内网固定为 `10.239.80.0/24`；启动前确认目标机不存在冲突。Caddy 取直接连接来源，移除请求中的 Cloudflare 和 Forwarded 头，前端只信任内部网关，app 只信任内部前端。SSH 转发下记录的是转发连接来源，不会保留浏览器真实公网 IP。今后接 HTTPS 或代理需要同时调整入口、可信代理和外部 origin 并重新验证。

2026-10-10 已通过 Compose 示例配置解析、Caddy 适配与验证、两个入口脚本的 shell 语法检查。没有启动或停止业务服务，也未修改本机运行配置。

## 独立公网 IP HTTPS 入口

叠加 `docker-compose.tencent-ip-https.yml` 后，浏览器入口为 `https://193.112.17.218`。基础 Compose 与原 `Caddyfile` 不变；新配置保留全部注册、内部接口、技能、沙箱、上传大小、缓存和请求头过滤规则。`127.0.0.1:18080` 继续用于服务器本地健康检查和 SSH 转发诊断，不对公网发布。应用外部链接固定为 HTTPS，即使示例环境文件仍设置旧 HTTP origin，也不会生成明文公网登录地址。

公网网页仅允许 `WEB_ALLOWED_CIDRS` 指定的直接连接来源，默认 `218.104.190.149/32`。多个 CIDR 使用空格分隔，写入服务器 `.env.tencent`，例如 `WEB_ALLOWED_CIDRS="218.104.190.149/32 203.0.113.8/32"`；后一地址是文档示例，实际应替换为已确认的办公出口。来源不匹配返回 403，伪造 `X-Forwarded-For` 或 `Cf-Connecting-Ip` 不会获得访问权限。办公出口变化后须先更新白名单并重建网关；不要将其改为 `0.0.0.0/0`。腾讯云公网入口使用直接连接，不在网关前叠加未配置的 CDN 或反向代理。

云防火墙和宿主防火墙需要保留 TCP 22 仅允许管理电脑来源，新增 TCP 443 允许 `0.0.0.0/0`。443 对公网开放是为了证书验证，网页权限仍由上述白名单限制。无需开放 TCP 80、UDP 443 或任何数据库端口。不要仅在首次发证时开放443，续期同样需要它持续可达。

新网关显式使用 Let's Encrypt ACME `shortlived` profile 和 TLS-ALPN-01，不使用自签或内部 CA。IP 证书有效期160小时，Caddy 自动续期；配置剩余寿命比例为0.5，CA的 ARI 建议窗口可能优先决定时间。`default_sni` 支持不发送SNI的IP访问客户端，避免容器内网地址与公网地址不同导致选证书失败。TLS验证发生在HTTP业务路由之前，来源白名单不会阻断验证。服务器需要能解析并通过出站443访问LE服务，系统时间须准确。[LE IP证书说明](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)、[挑战类型](https://letsencrypt.org/docs/challenge-types/)、[Caddy续期设置](https://caddyserver.com/docs/caddyfile/options#renewal-window-ratio)。

Compose 需要支持 `!override` 的版本（2.24.4或更新）。新入口用专用持久卷 `gateway-tls-data`、`gateway-tls-config` 替换原 `/data`、`/config` tmpfs，仅 `/tmp` 保持临时文件系统。证书和ACME账号私钥随数据卷持久保存，纳入加密备份，不打印私钥、不清空这些卷。

```bash
python3 deployment/tencent/validate-config.py
python3 deployment/tencent/validate-ip-https.py
docker compose --env-file .env.tencent -f docker-compose.tencent.yml -f docker-compose.tencent-ip-https.yml --profile ip-https-init run --rm --no-deps gateway-volume-init
docker compose --env-file .env.tencent -f docker-compose.tencent.yml -f docker-compose.tencent-ip-https.yml up -d --no-build --pull never --wait --wait-timeout 600
docker compose --env-file .env.tencent -f docker-compose.tencent.yml -f docker-compose.tencent-ip-https.yml ps
```

上述启动命令须在业务数据恢复、镜像核验和防火墙配置完成后执行。`gateway-volume-init` 仅初始化两个专用卷根目录的 `65532:65532` 所有权与0700权限，使用固定镜像、只读根文件系统、无网络、没有宿主目录挂载；不递归修改已有文件。日常网关以65532身份运行。不要日常开启 `ip-https-init` profile，只有手动初始化命令需要它。

本地配置验证使用 `env.example`，不会读取真实密钥；同时检查路由与基础配置一致、两个宿主端口、HTTPS origin、持久卷和初始化范围。2026-10-10 新配置已在Caddy 2.11.7完成 `adapt --validate`，没有启动源栈或请求证书。适配器对显式转发头给出的冗余警告不影响配置有效性；这些头保留原入口规则。

实际上线后，从白名单内电脑执行：

```bash
curl --noproxy '*' -fSI https://193.112.17.218/login
openssl s_client -connect 193.112.17.218:443 -servername 193.112.17.218 </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates -ext subjectAltName
```

`curl` 必须不带 `-k` 成功，证书应包含IP SAN `193.112.17.218`，并处于有效期内。从未加入白名单的另一出口访问应得到403，即使附加 `X-Forwarded-For: 218.104.190.149` 也应拒绝。网关本地健康检查只验证HTTP诊断入口，不证明公网TLS或证书续期正常。首次发证、双来源限制和真正续期尚未验证，需要云端启动后检查证书序列号/有效期在续期窗口后更新。不要为测试反复清除证书卷申请新证书。

## 私有文件传输与断点续传

`scripts/tencent-transfer.py` 复用已核对指纹的 `tencent-ssh.py` 连接参数，不接受未知或变更的主机密钥。目标固定为193.112.17.218的root账号、Ubuntu24.04 x86_64以及具有正确部署标记的 `/opt/boyankb/incoming/`。子目录必须0700，文件必须0600；拒绝路径穿越、软硬链接和已存在但摘要不同的完成文件。

仅允许传输 `temp/tencent-cloud/` 下与镜像锁相符的 `images*.tar.gz`/`.tar.json`、`snapshots/<批次>/` 中完成清单列出的备份文件，以及 `config-bundle-*.tar.gz`/`.tar.json`。配置包的receipt格式为 `boyankb-tencent-config-v1`，包含 `archive`、`size`、`sha256` 和 `files` 路径到SHA256的映射；tar成员必须是脚本中列出的精确配置白名单，并逐个匹配本机文件内容。SSH私钥、本机完整 `.env`、Cloudflare凭据不在传输范围。`runtime.env` 只接受已完成快照内、字段集合与 `env.example` 一致的服务器环境文件。

```bash
python3 -B scripts/tencent-transfer.py probe --source temp/tencent-cloud/images-20261010.tar.gz
python3 -B scripts/tencent-transfer.py upload --source temp/tencent-cloud/images-20261010.tar.gz
python3 -B scripts/tencent-transfer.py upload --source temp/tencent-cloud/images-20261010.tar.json
python3 -B scripts/tencent-transfer.py upload --source temp/tencent-cloud/config-bundle-20261010.tar.gz
```

`probe` 只读检查目标，不创建文件。可用 `--destination snapshots/<批次>/<原文件名>` 指定incoming内相对路径，禁止更改原文件名。上传始终使用二进制SSH stdin，不经PowerShell文本管道转换；每256MiB或30秒输出字节进度，不输出文件内容。

断线后重跑原上传命令：脚本核对 `.partial` 元数据及本地/远端前缀SHA256后续传，完成时重新计算远端完整SHA256，并以不覆盖目标的原子改名提交。完成文件摘要相同则直接跳过，摘要不同则停止，保留已有文件；不要手工覆盖冲突文件或混用其他上传工具修改 `.partial`。每次运行都会重新验证本地归档，首个上传字节前可能有一段哈希计算时间。

2026-10-10 传输脚本在本机通过18项适用测试，另外6项Linux专用协议测试在隔离、无网络的Linux容器内通过，合计24项。Linux测试实际验证了二进制中断、续传、校验、原子完成、重复跳过、冲突拒绝和只读探测；不代表已将业务文件上传到云服务器。
