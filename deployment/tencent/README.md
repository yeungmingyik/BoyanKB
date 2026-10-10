# 腾讯云单机内部测试配置

`docker-compose.tencent.yml` 用于 Ubuntu 24.04、linux/amd64、4 核 8GB 的独立部署。使用九个已验收版本镜像，不自动拉取或构建，不覆盖旧生产或本机 Compose。

入口固定绑定 `127.0.0.1:18080`。数据库、Redis、Ollama、Neo4j、文档解析和渲染服务没有宿主端口。公网 IP 与 `PUBLIC_ORIGIN` 仅用于生成链接，不代表已开放公网入口或 HTTPS。

`env.example` 只包含占位值。真实部署由受控迁移程序生成服务器专用环境文件，再通过 `--env-file` 指定。不要上传完整试用辅助环境文件或 Cloudflare API 管理令牌。已有数据库须保留原 `SYSTEM_AES_KEY`；恢复 Neo4j 时使用原图数据库密码。其他密钥更换须同步更新其消费者。

## 静态校验

```bash
python3 deployment/tencent/validate-config.py
```

校验仅使用示例值，检查九服务、镜像锁、回环端口、网络隔离、只读配置挂载、沙箱和自动迁移开关以及资源上限，不启动业务服务，不读取真实环境文件。镜像清单在 `images.lock.json`；传输镜像后应额外验证目标机实际镜像 ID 和摘要引用。

## 镜像导出与 OCI 索引

`scripts/tencent-images.py` 先核对九个源镜像的锁定 ID、Linux 和 amd64，再使用不带 `--platform` 的 `docker image save` 导出完整可用索引。不要手动添加 `--platform linux/amd64`：Docker 的 containerd 镜像存储会将导出目标从原 OCI index 改为该平台的 manifest，云端默认 `inspect.Id` 因而与原锁不同，即使实际运行内容一致。来源：[Docker 导出实现](https://github.com/moby/moby/blob/master/daemon/containerd/image_exporter.go)。

例如本次 app 原索引为 `sha256:ac806ca72aa54f18c73b5a4140cb18c75aa0f3a41fe1a1cd2cb5b5f7da04d756`，amd64 manifest 为 `sha256:a5b745db948239deaa9a6cb5c0b61f98268a5d02f827e24e24805b0b1968e183`。不应为通过检查直接替换锁文件，先验证原索引到平台 manifest、config 的摘要链。

```bash
python3 -B scripts/test_tencent_images.py
docker image inspect boyankb/app:v0.8.2-trial-graph-v3 --format '{{json .Descriptor}} {{.Id}}'
docker image inspect --platform linux/amd64 boyankb/app:v0.8.2-trial-graph-v3 --format '{{json .Descriptor}} {{.Id}}'
```

常规迁移应重新生成完整镜像归档。如果目标已经校验并加载了平台层，可另行校验和补入源端原始 OCI index、manifest、config 等元数据，并复用目标 content store 内的层；这种补包不是独立镜像备份。必须逐个验证原 blob SHA256、九个锁定引用、平台映射及云端既有镜像，导入后再次验证 tag 和 `tag@原索引摘要`，成功后才允许数据恢复。[containerd 导入实现](https://github.com/containerd/containerd/blob/main/client/import.go)。

## 完成数据恢复后启动

```bash
docker compose --env-file .env.tencent -f docker-compose.tencent.yml up -d --no-build --pull never --wait --wait-timeout 600
docker compose --env-file .env.tencent -f docker-compose.tencent.yml ps
```

启动前必须已向新卷恢复数据库、对象文件、Redis、Neo4j 和 Ollama 模型，并迁移 `config/config.yaml`、`config/builtin_models.local.yaml`。`AUTO_MIGRATE=false`，本配置不会初始化或升级应用 schema。Ollama 仅接内部网络，模型须从本机缓存迁移，不能依赖容器在线下载。

配置传输文件默认由 root 以0600保存，但挂载给容器的四个服务配置需要指定组读取。app 入口切换到 UID/GID 1000；网关以65532运行。`tencent-restore.py` 在核验并复制配置后，将两份 YAML 设置为 `root:1000 0640`，将 `deployment/tencent/Caddyfile`、`Caddyfile.ip-https` 设置为 `root:65532 0640`。仅处理这四个普通文件，拒绝软硬链接，不递归改变目录。宿主目录保持0700，Docker 使用单文件只读挂载；`.env.tencent`、快照的 `runtime.env` 和其余密钥仍仅 root 可读。将所有配置一律保留为 root:root 0600，会导致非 root 的 app 或网关启动时权限不足。

```bash
stat -c '%U %g %a %n' config/config.yaml config/builtin_models.local.yaml deployment/tencent/Caddyfile deployment/tencent/Caddyfile.ip-https .env.tencent
python3 -B scripts/test_tencent_restore.py
```

前两项应为组1000/0640，中间两项为组65532/0640，环境文件仍为root/0600。恢复程序只调整文件权限，不修改已核验的配置内容。

恢复的Redis目录属于999:1000，AOF子目录为0700。受限root没有绕过目录权限的能力，因此自定义入口在生成0600认证配置后先执行 `gosu redis`，再运行官方入口；不扩大容器capabilities，不递归改写已有数据权限。该路径已在无网临时容器验证认证、匿名拒绝、读写和AOF持久化，并在云端实际恢复健康。

公开注册关闭，账号由管理员邀请。原生技能目录接口保留，任意技能执行、会话沙箱、平台管理和本机浏览器接口由入口限制。业务文档生成通过独立内部渲染服务执行。

## 资源与访问边界

容器内存上限合计 7072MiB，为宿主机保留约 1GiB。每类异步工作池配置为 1，模型调用并发为 2，Ollama 同时最多一个模型和一个请求，解析并发为 1；已有 renderer 使用串行 HTTPServer。这些上限不等于所有后台任务合计只有一个。数据库中的系统设置优先于同名环境配置，迁移后须核对是否存在并发覆盖。

批量导入、图谱抽取和 Office 生成错峰执行。大文档解析或复杂文件生成可能触及内存限制；真实内存峰值、云端恢复和问答质量仍须运行验收，不能用静态校验代替。旧七服务整栈备份脚本不适用本九服务配置。

Web 内网固定为 `10.239.80.0/24`；启动前确认目标机不存在冲突。Caddy 取直接连接来源，移除请求中的 Cloudflare 和 Forwarded 头，前端只信任内部网关，app 只信任内部前端。SSH 转发下记录的是转发连接来源，不会保留浏览器真实公网 IP。今后接 HTTPS 或代理需要同时调整入口、可信代理和外部 origin 并重新验证。

2026-10-10 已通过 Compose 示例配置解析、Caddy 适配与验证、两个入口脚本的 shell 语法检查。没有启动或停止业务服务，也未修改本机运行配置。

## 独立公网 IP HTTPS 入口

叠加 `docker-compose.tencent-ip-https.yml` 后，浏览器入口为 `https://193.112.17.218`。基础 Compose 与原 `Caddyfile` 不变；新配置保留全部注册、内部接口、技能、沙箱、上传大小、缓存和请求头过滤规则。`127.0.0.1:18080` 继续用于服务器本地健康检查和 SSH 转发诊断，不对公网发布。应用外部链接固定为 HTTPS，即使示例环境文件仍设置旧 HTTP origin，也不会生成明文公网登录地址。

公网网页来源由 `WEB_ALLOWED_CIDRS` 控制。2026-10-10 按用户要求，服务器 `.env.tencent` 设置为 `WEB_ALLOWED_CIDRS="0.0.0.0/0 ::/0"`，所有来源可打开登录页。公开注册仍关闭，资料、智能体、技能和文件必须通过既有登录及空间授权。Compose 未设置变量时仍回落到原白名单 `218.104.190.149/32`，部署和重建时必须带 `--env-file .env.tencent`。IPv6 CIDR 不代表当前主机已提供 IPv6 公网入口。腾讯云公网入口使用直接连接，不在网关前叠加未配置的 CDN 或反向代理。

云防火墙和宿主防火墙保留 TCP 22 仅允许管理电脑来源，TCP 443 允许 `0.0.0.0/0`，供网页访问和证书验证。无需开放 TCP 80、UDP 443 或任何数据库端口。不要仅在首次发证时开放443，续期同样需要它持续可达。

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

### 调整入口来源

以 root 在服务器执行。默认只预览原来源和目标来源，不输出环境文件中的其他值：

```bash
cd /opt/boyankb
python3 -B scripts/tencent-web-access.py --allow '0.0.0.0/0' '::/0'
```

确认预览后应用，向所有来源开放登录页：

```bash
python3 -B scripts/tencent-web-access.py --allow '0.0.0.0/0' '::/0' --apply
```

脚本校验云主机与私有文件，拒绝重复配置键，先在 `/opt/boyankb/backups/` 保存 root/0600 备份，再仅修改 `.env.tencent` 的 `WEB_ALLOWED_CIDRS`。它比较前后有效Compose配置，确认只有来源变量变化，仅重建 gateway，并检查其余八个容器ID、端口映射和九服务健康状态。结果写入私有 `/opt/boyankb/web-access-report.json`。无需修改Caddy规则或重启数据库。

```bash
docker compose --env-file .env.tencent -f docker-compose.tencent.yml -f docker-compose.tencent-ip-https.yml ps
```

如需回退到原管理电脑白名单，先预览再应用。若办公出口已变更，应填经确认的新出口CIDR。无需更改SSH规则或开放数据库端口。

```bash
python3 -B scripts/tencent-web-access.py --allow '218.104.190.149/32'
python3 -B scripts/tencent-web-access.py --allow '218.104.190.149/32' --apply
```

### 入口检查

从外网电脑执行，以下命令均不携带账号或令牌：

```bash
curl --noproxy '*' -fSI https://193.112.17.218/login
curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' https://193.112.17.218/api/v1/knowledge-bases
curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{}' https://193.112.17.218/api/v1/auth/register
curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' https://193.112.17.218/api/v1/system/admin
openssl s_client -connect 193.112.17.218:443 -servername 193.112.17.218 </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates -ext subjectAltName
```

预期依次为登录页200、未登录知识库请求401、注册403、内部接口404。`curl` 必须不带 `-k`，证书应包含IP SAN `193.112.17.218`，并处于有效期内。需要人工验证：换用手机移动网络等另一个出口打开登录页，确认可访问；页面可打开不代表未登录可读取资料。

在服务器核对容器端口：

```bash
docker ps --filter label=com.docker.compose.project=boyankb-tencent --format 'table {{.Names}}\t{{.Ports}}'
```

只有 gateway 应出现宿主端口映射，即 `0.0.0.0:443->443/tcp` 和 `127.0.0.1:18080->8090/tcp`。数据库、Redis、Neo4j、Ollama、解析和渲染服务不得出现宿主端口映射；镜像自身声明的裸端口不等于对外发布。SSH继续使用既有来源限制。

上述为本次调整的核验方法，实际结果见部署记录。初始证书签发与可信TLS登录已通过，真正续期尚未跨周期验证。网关本地健康检查只验证HTTP诊断入口，不证明公网TLS或证书续期正常。后续检查证书序列号/有效期在续期窗口后更新，不要为测试反复清除证书卷申请新证书。

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

## 当前上线状态

2026-10-10后续已完成真实传输、云端数据恢复、九服务健康检查、两正式账号交接和可信IP HTTPS登录。上文静态测试及准备阶段记录不代表最终状态；以 [部署执行记录](../../docs/13-tencent-deployment.md) 为准。用户要求暂缓业务验收，问答及Office生成尚未在云端执行，附加图谱解析检查仍需排查，证书续期待跨周期验证。
