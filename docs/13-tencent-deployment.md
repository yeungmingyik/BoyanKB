# 腾讯云内部测试部署

目标主机为 `193.112.17.218`，广东地域，Ubuntu 24.04、x86_64、4 核 8GB、5Mbps。部署目录为 `/opt/boyankb`，Compose 项目为 `boyankb-tencent`。本机 `weknora-local` 保留，Cloudflare 隧道和域名解析不切换。

本方案用于邀请制账号的内部测试，登录页按用户要求向所有来源开放。公开 IP 不是正式对外服务免备案的替代方案；正式合作伙伴上线前应完成腾讯云要求的备案与服务合规检查。[腾讯云备案 API 常见问题](https://cloud.tencent.com/document/api/243/19630)、[备案常见问题](https://cloud.tencent.com/document/product/243/18910)。

## 入口和账号

已上线入口：`https://193.112.17.218`。2026-10-10 已验证可信 HTTPS 证书、登录页200、两个正式账号公网登录和公开注册403。按用户要求先上线，完整业务验收暂缓。

| 账号 | 邮箱 | 空间 | 权限 |
| --- | --- | --- | --- |
| 刘伟健 | `liuweijian@boyantech.xyz` | 博研文化总部 | 系统管理员、空间 Owner |
| 冯伟杰 | `fengweijian@boyantech.xyz` | 博研文化总部 | Viewer，可问答和使用已授权业务技能 |

初始密码在本机 `temp/tencent-cloud/.env` 的 `ADMIN_PASSWORD`、`MEMBER_PASSWORD`，不写入本文、Git 或日志。首次登录后分别修改密码。服务器 `.env` 仅供交接与验收脚本使用，`.env.tencent` 为容器运行配置；二者均须 root 所有、0600 权限。

新账号使用新 UUID。交接只发生在云副本，先验证新账号，再停用旧 `aldream.lau@gmail.com` 与 `testpartner001@gmail.com`，撤销旧令牌和未接受邀请。旧个人聊天不改归属；原生空间共享的 API/embed 频道历史保留，Owner 仍可能按原生权限查看。两个新账号只加入总部。2026-10-12 已按用户要求备份并软删除星辰教育旧工作空间、对应共享空间及其知识库共享；历史数据未硬删除。具体范围、残留及验证见 [技能、共享与同步核查](15-skills-sharing-sync-review.md)。

2026-10-10 初次迁移时保留企业知识库、19 篇文档、图谱、模型、三个受管业务智能体和九项技能，按当时要求保留测试文档，并暂停飞书同步。2026-10-12 后续已按用户要求清理测试副本及测试会话，现有 18 篇正文解析完成；云端开启每 30 分钟新增、修改增量同步，本机保持暂停。技能已统一到原生技能管理，普通成员可使用，Admin、Owner 管理；九项受管预设继续按版本维护。当前详情见文末和 [本次实施记录](16-skills-and-cloud-sync.md)。

## 网络与资源

仅发布 TCP 443 和受管理电脑 IP 限制的 SSH 22。数据库、Redis、Ollama、Neo4j、解析及渲染服务无宿主端口。`127.0.0.1:18080` 是服务器本地诊断入口。

443 来源允许所有 IPv4 地址，供网页访问及 Let's Encrypt TLS-ALPN-01 验证。2026-10-10 按用户要求，将服务器 `.env.tencent` 的 `WEB_ALLOWED_CIDRS` 设为 `"0.0.0.0/0 ::/0"`，所有来源均可打开登录页；IPv6 CIDR 不代表该服务器已提供 IPv6 公网入口。公开注册仍关闭，知识库、智能体、技能和文件仍由登录身份与空间权限控制。SSH 22 继续限制管理电脑来源，80 与 UDP 443 无需开放。设置与回退命令见 [入口来源配置](../deployment/tencent/README.md#调整入口来源)。

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
10. 初始化专用证书卷，叠加 IP HTTPS 配置启动；具体命令见 [部署配置说明](../deployment/tencent/README.md)。实际验证证书、登录与授权边界、注册关闭、技能、问答和文件。

## 验证与回退

```bash
python3 deployment/tencent/validate-config.py
python3 deployment/tencent/validate-ip-https.py
docker compose --env-file .env.tencent -f docker-compose.tencent.yml -f docker-compose.tencent-ip-https.yml ps
python3 scripts/tencent-acceptance.py readonly
python3 scripts/tencent-runtime.py
python3 scripts/tencent-acceptance.py exercise
curl --noproxy '*' -fSI https://193.112.17.218/login
```

`readonly` 会登录和读取授权资料，不创建业务内容；登录本身会生成认证记录。`tencent-runtime.py` 核对九服务、图谱范围、APOC能力与并发配置，只写私有报告。`exercise` 以新成员身份创建中文业务会话、问答和 Word/PPT 文件并消耗模型额度，保留输出与报告，不自动删除。脚本逐次核对任务与大纲绑定，演练确认和审校，不能绕过阻断；自动点击确认不代替业务人员审阅。检索结果属于企业库不代表答案引用已经准确，脚本自动通过不代替事实审查和原生 Office 打开检查。

入口最小检查：`GET /login` 应为200；未携带令牌的 `GET /api/v1/knowledge-bases` 应为401；`POST /api/v1/auth/register` 应为403；`GET /api/v1/system/admin` 应为404。通过 Docker 端口清单确认数据库、Redis、Neo4j 等内部服务没有宿主发布端口。命令见 [部署配置说明](../deployment/tencent/README.md#入口检查)。这些检查不代替业务验收。

需要人工验证：从手机移动网络等另一出口打开登录页，应能访问；分别用两个账号在独立浏览器会话登录，核对仅总部成员关系、技能可用、成员不能管理团队；用 Word/PowerPoint 打开产物确认无修复提示及版面正常；逐条核对回答与来源；用知识库没有的问题确认明确说明缺少依据。续期窗口后再次核验证书更新。

回退优先停止云端入口并继续使用原本机部署；不把云端修改覆盖回本机。数据库恢复、删除卷或切换生产入口前再次确认具体目标与影响。不可用 `docker compose down -v` 清理失败现场。

## 执行记录

2026-10-10：已核对 SSH 主机指纹并取得 root 访问；已安装 Docker 29.9.0、Compose 5.6.0。基础 Compose、HTTPS 覆盖和 Caddy 静态校验通过。

完整源快照 `temp/tencent-cloud/snapshots/migration-20261010b` 已生成，九个组件通过摘要与库存验证，源端九服务全部恢复健康。数据库76张表；企业库19文档、27分块、27向量；图谱384实体、326关系；保留一个约1.2GB的 `bge-m3:latest` 模型，模型内容摘要固定。

首轮 `migration-20261010` 在PG格式校验失败，未生成成功manifest，保留供诊断，不用于恢复。已修复Windows缓冲文件句柄传递和Neo4j慢启动等待，28项快照回归测试通过。云端Docker Hub连接超时，采用本机导出的固定镜像包经SSH传输。后续云端结果如下。

### 云端上线结果

2026-10-10 已完成云端新卷恢复、账号交接及公网HTTPS启动。九服务全部健康；数据库76张表在应用启动前核对行数与内容摘要一致，企业库19文档、27分块和27向量内容摘要一致，模型名称、大小和内容摘要一致。四个冷备卷先验证文件库存，再启动服务。云端未重新导入或清空原本机数据。

镜像归档为7,188,096,716字节，SHA256为 `aefe585b039eab8f27eed46781a8f0c605388872e5119412afff15d6d358adb0`。首个归档因 `save --platform` 仅保留平台manifest，通过184,320字节原始OCI元数据补包恢复九个原索引，补包SHA256为 `20783fbdc4ebf0327e6a17a2df3c0f2616edf66ee8ebb8a73420e06c5047b359`；九个tag及锁定摘要引用全部复核一致。未来导出已修正为保留原索引，3项导出回归测试通过。

首次启动遇到两项权限问题并已修复：Redis官方root入口无法遍历恢复的0700目录，改为先通过gosu切换至redis再运行原入口；两份应用YAML和两份Caddy配置改为root所有、对应服务组可读的0640，环境文件保持0600。Redis修复在无网临时容器中通过认证PING、匿名拒绝、SET/GET、降权和AOF落盘检查；恢复脚本25项测试通过。云端恢复报告保留首次失败及后续修复记录，未删除或重建数据卷。

两个新账号均使用独立UUID，只有总部成员关系；旧账号及令牌已停用。交接时验证了新账号没有继承私人会话、九项业务技能可用且摘要正确、三个业务智能体可读取。旧空间频道历史按原生规则保留，源端本机账号未替换。

HTTPS使用Let's Encrypt YE1签发的IP SAN证书，本次有效期为UTC `2026-10-10 09:01:27` 至 `2026-10-17 01:01:26`。本机真实TLS链及IP验证通过，没有忽略证书错误。两账号通过公网HTTPS登录，公开注册返回403。本机无密钥结果记录为 `temp/tencent-cloud/public-https-report.json`；服务器恢复和交接记录分别为 `/opt/boyankb/tencent-restore.report.json`、`/opt/boyankb/accounts-plan.progress.json`。

### 暂缓的验收与已知限制

用户要求先上线，因此未执行云端 `tencent-acceptance.py exercise`，未创建问答或Word/PPT验收会话。业务问答、引用准确性、未知问题拒答、Office产物、真实成员邀请和独立网络来源验证均未完成；需要人工验证，按前面的验证清单执行。

附加只读图谱检查 `tencent-runtime.py` 返回 `CYPHER_SINGLE_RESULT_REQUIRED`，尚未定位是哪条Cypher返回行数与解析器预期不符。Neo4j容器健康且冷备文件库存一致，但这不能替代云端实体/关系数量、APOC功能和应用图谱查询验证。不能把该脚本记录写成通过；后续分别定位其六条聚合/版本查询的列数与行数，修复解析或实际配置后重跑。源端384实体、326关系是迁移基线，云端语义计数暂为未验证。

初始白名单配置曾对云机443回环连接返回403，附加伪造的X-Forwarded-For、Cf-Connecting-Ip及Forwarded头仍为403；旧报告为 `/opt/boyankb/https-denial-report.json`。该结果仅记录调整前的来源限制，不能作为当前入口仍限制其他IP的证据。

2026-10-10 后续按用户要求取消网页来源限制，仅调整服务器 `WEB_ALLOWED_CIDRS` 并重建 gateway；登录、邀请制、RBAC、SSH来源限制和数据库端口隔离保持原配置。实际核验九服务全部健康，其余八个容器ID不变，网关宿主端口不变，内部服务没有宿主发布端口。原环境文件备份为 `/opt/boyankb/backups/web-access-20261010T100816Z-305e58b3.env`，权限0600；变更报告为 `/opt/boyankb/web-access-report.json`。

原先被拒绝的云机443回环来源现已通过可信TLS取得登录页200；未登录的知识库及身份API为401，公开注册403，Swagger404。新结果记录为 `/opt/boyankb/web-access-checks.json`。本机公网登录页也为200，内部管理API为404，TLS验证通过。此次没有登录、生成会话或执行业务模型请求；独立手机/其他外网出口的真实访问仍待人工确认。

证书自动续期尚未跨周期验证；另一个外网出口的登录页访问仍需要人工验证。2026-10-10 上线时飞书数据源暂停且无定时同步，该状态已由下述 2026-10-12 云端同步启用替代。通用代码沙箱仍关闭。当前是邀请制内部测试上线，不代表生产或完整业务验收通过。

初次部署使用 `feat/tencent-cloud-deployment`，后续网页来源调整使用 `feat/tencent-public-web-access`。因并行任务切换了主目录分支，后续修复和本文在独立工作目录 `D:/BoyanKB/WeKnora-worktrees/tencent-cloud-deployment` 提交；凭据和迁移文件仍位于原目录 `D:/BoyanKB/WeKnora/temp/tencent-cloud`，不迁入Git。

### 2026-10-12 技能合并、清理与云端同步

已发布前端 `boyankb/frontend:v0.8.2-skills-unified-v2@sha256:2cc82fd44eca6e421ad5a20a0803f1c89133d386a025f913d91cd61177764bf6`，仅重建 frontend，其他八个容器保持原 ID，九服务全部健康。工具箱只保留原生技能管理；成员可查看和使用已授权技能，管理操作仍要求 Admin 或 Owner，九项受管业务预设仍由版本化流程维护。未开启通用代码沙箱，未改变后端角色权限。

用户已从飞书删除测试文档，本轮已移除云端对应副本并软删除测试会话 `local-smoke-stardust`。企业知识库剩余 18 篇且全部解析完成，描述改为中文资料用途。其他业务文档、三个受管智能体、九项技能及模型保留。

云端数据源已启用 `active`、`0 */30 * * * *`、`incremental`、`overwrite`、`sync_deletions=false`，唯一同步飞书空间不变。一次原生手动同步成功，变更数为 0。本机保持暂停、空表达式，相关同步队列为 0，未发现匹配的 Windows 同步计划；本机不得再手动或定时同步。

自动调度另通过临时每分钟计划演练：北京时间 10:39:00 触发、10:39:04 成功，审计记录 97 确认 `trigger=schedule`，最终每半小时计划已恢复；具体证据见 [实施记录](16-skills-and-cloud-sync.md)。需要人工验证：实际修改授权文档后，等待计划执行，核对云端正文和检索变化及本机无新增同步。源端删除仍保留本地副本，本次测试内容清理不改变该策略。

前端独立升级和回滚、自动同步启用与暂停命令见 [腾讯云维护说明](../deployment/tencent/README.md#仅更新前端与回滚)。原初次迁移的 19 篇、图谱数量和镜像摘要继续作为历史基线，不能代替清理后的当前状态。

### 2026-10-15 快速问答异常结束修复

在 `feat/qa-failure-completion` 完成并测试超时、模型服务故障、流式异常结束的统一处理；快速问答整轮期限为 120 秒，失败后保留已有内容、明确提示原因并保存完成状态，刷新不应重新进入等待。故障文案不携带供应商原始响应。无数据库迁移，不调整模型配置或飞书同步。

已发布 app/frontend 的 `v0.8.2-qa-failure-v1` 固定摘要镜像，锁定值见当前 Compose、镜像锁及 [完整记录](21-qa-failure-completion.md)。九服务健康，独立对比确认仅 app/frontend 重新创建，其他七容器 ID 和镜像、全部环境及端口不变。公网 HTTPS 登录 200，认证、注册关闭和内部接口边界检查通过。配置备份与报告为 `/opt/boyankb/backups/frontend-20261015T042256Z-72fc753b/report.json`，旧镜像保留。

发布后登录检查运行提交为 `77516e949`，实际企业知识库问答经 HTTPS 在 21.060 秒返回完整正文和引用并结束，历史消息读取确认 `is_completed=true`。模型故障、超时、异常 EOF、主动停止和错误刷新恢复通过本机自动回归；未在生产中主动制造供应商故障。该验证仅覆盖本次快速问答修复，未替代此前暂缓的完整业务验收。
