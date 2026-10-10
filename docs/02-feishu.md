# 飞书合作伙伴专用空间同步

本方案仅将飞书知识空间 `7610776951925509072` 同步到本机 WeKnora 的专用知识库，现名为「合作伙伴专用空间」，原名为 `feishu-7610776951925509072`。连接器使用应用身份 `tenant_access_token`，首次遍历全部可见目录，后续按节点内容更新时间增量同步。本方案不需要用户 OAuth，不申请全部 scope。2026-10-08 首次导入使用空摘要模型，未通过导入摘要步骤发送正文到 DeepSeek；当前配置差异见第 5 节。

首次验收：2026-10-08。配置复核：2026-10-10。基线：Tencent/WeKnora v0.8.2。变更登记见 [CUSTOMIZATIONS.md](CUSTOMIZATIONS.md)。

**2026-10-10 当前状态：**知识库已配置远程摘要模型，不能继续沿用「摘要模型为空」的导入保证。`verify`、`snapshot` 允许只读核验，并明确输出 `summary_model_configured=true`、`sync_allowed=false`；`sync` 仍拒绝执行。恢复实际同步前需明确摘要处理策略，不能把只读对账成功当作同步已恢复。以下首次导入、V1/V2 更新及无变化增量结果属于 2026-10-08 历史验收。用户选择保留 probe，不删除源文档或本地副本。

## 1. 当前结果与待验证项

| 项目 | 当前结果 | 后续验证 |
| --- | --- | --- |
| 目标空间 | 用户指定合作伙伴专用空间 `7610776951925509072`，管理员已确认总数 19 | 沿用已指定范围，无需重复确认 |
| 所需 tenant 权限 | `wiki:wiki:readonly`、`drive:export:readonly`、`drive:file:download` 三项均已 `grant_status=1`；用户已确认版本审核上线 | 权限检查已通过，无需重复申请 |
| 现有应用是否只读 | 不是；已有 tenant `wiki:wiki` 等写能力，用户也明确选择应用可编辑身份；目标 19 篇资源均实测 `edit=true` | 沿用用户选择，不自动撤销；连接器本次只读取飞书，不代表应用身份本身只读 |
| 目标目录与 ACL | 最终逐篇权限检查为 `view=true` 19、`export=true` 19、`edit=true` 19，无同步权限拒绝；管理员确认源总数 19，本地 external ID 全覆盖 | 只证明目标 19 篇，不代表其他 Drive、外链或全部资源的权限范围 |
| 已指定 probe | 节点 `JJ0zw4wfjiSGwSkypq3csTVJnCc` 的 V1、V2 完整验证均退出 0；V2 真实检索命中且内容哈希、源更新时间相对基线均变化 | 前后快照均已保存；只有 probe 的记录发生变化，其他 18 篇不变；未删除 probe |
| Wiki 可见性审计 | `audit-access` 实测可见 Wiki 空间仅 1 个，即 `7610776951925509072` | 不证明 Drive 或单篇文档的全部 ACL；外部对照样本仍未验证 |
| helper 实测与测试 | 完整 19 个模拟测试通过；原失败节点的 `audit-documents` 已通过，最终原生 auth 遍历另确认目标 19 篇阅读、导出、编辑均允许 | helper CLI 检查 view/export；最终 edit 结果来自只读原生元数据查询 |
| 定制镜像与服务 | 最终镜像已构建并部署；middleware、core、wiki 三包 Go 测试全部通过，包含最新 origin 修复；6 个容器均 healthy，8088、8080 健康检查均 HTTP 200 | 健康检查不替代正文同步与检索验收 |
| 日志安全修复 | 定制镜像已运行；本次 app 日志窗口经内存扫描，真实 AppID、AppSecret 均未出现 | 只证明该检查窗口，不构成所有历史及未来日志均安全的保证 |
| 凭据静态存储 | 数据库只读布尔检查确认两项凭据均符合 `enc:v1` 密文格式 | 检查仅输出布尔结果，不输出凭据或密文 |
| 空间范围校验 | 全树预检查、官方 API origin 锁定和显式空配置拒绝已部署，模拟测试通过；目标空间真实同步已运行 | 真实外部空间拒绝样本仍未验证 |
| WeKnora 真实同步、数量、解析、检索 | 19 篇解析完成且 external ID 全覆盖；V2 增量更新 1 篇并通过真实检索；无变化重复同步 total、created、updated、failed 均 0 | 数量和 ID 均保持不变，已通过本次现有文档导入、更新及无变化增量验收 |
| 独立冒烟测试 | 新镜像下 `scripts/smoke-test.sh` 退出码 0，真实 DeepSeek 回答、引用及三种检索通过 | 仅使用 testdata 的独立 KB，不是飞书正文问答，也不替代飞书完整对账 |
| 真实新增、更新与删除 | 指定 probe 的 V1 到 V2 更新已验证；飞书新建文档与删除尚未验证 | 已导入的 19 篇属于现有文档；删除保持禁用 |
| 外部空间拒绝访问 | 未验证；用户确认目前没有可提供的外部对照空间 | 不自行寻找或创建样本，不把可见空间只有 1 个当作负向测试通过 |

权限查询成功只能说明应用 token 可用，不能说明目标 Wiki 可读。`POST /api/v1/datasource/{id}/validate` 同样只验证 token 获取，不能替代目标目录访问和正文导出验收。

定制镜像已部署，目标空间现有 19 篇均已导入并解析完成，本地 external ID 与源节点完整对应。用户先将《01.1项目产品手册》的相关选项设为可阅读用户，随后为满足部分文档仅允许编辑者导出的限制，明确选择将应用设为可编辑用户。最终用同一 tenant 应用逐篇只读查询 auth，目标 19 篇的阅读、导出、编辑均允许，无同步权限拒绝。当前保留用户选择的授权配置，不自动撤销编辑权限。

完整 V1 验证退出 0 后，`snapshot --output temp/feishu-before.json` 成功保存 19 篇基线。用户已将指定 probe 改为 V2，增量同步更新 1 篇，完整 V2 验证退出 0，真实检索命中 V2，内容哈希与源更新时间均较基线变化。随后无变化增量同步成功、处理数为 0，数量与 ID 均不变；`temp/feishu-after.json` 已保存，比对确认只有 probe 变化，其余 18 篇不变。新建文档、删除与外部拒绝样本仍未验证。复查 `audit-access` 仍只列出目标空间，6 个容器均 healthy。

本次部署的镜像 config ID 为 `sha256:5f47174e719deceec745be8ebc1ba56df15a035f6980036bfae81cec4c3547dc`。已创建专用 KB `d9285ed1-0760-474b-ba82-a13347598176` 和数据源 `c32b43f4-41b3-4060-9820-9371bdafed8b`；首次同步日志为 `6f02d11b-cd2d-47c3-9352-3396e577d197`。

排障历史仅供定位同类问题，不代表当前状态：

| 历史现象 | 处理与结果 |
| --- | --- |
| 权限列表曾为 36 条、调整后 37 条，仅新增 user `wiki:wiki` | 改为按应用身份核对所需权限，不以总数判断；当前三项 tenant 权限均已授权 |
| 导出权限加入新版本后尚未生效；两次 `/scopes/apply` 返回 HTTP 400、`212002 unauthorized scopes were empty` | 当时未生成审批；用户随后确认新版本经企业管理员审核上线，导出权限现已生效 |
| 目标目录返回 HTTP 400、飞书 `131006` | 处理目标空间的可阅读成员 ACL 后，完整目录递归成功 |

## 2. 权限与飞书侧配置

推荐在飞书开发者后台的**应用身份权限**中声明以下三项：

| Scope | 本次用途 | 官方依据 |
| --- | --- | --- |
| `wiki:wiki:readonly` | 获取目标 Wiki 节点及目录关系 | [获取知识空间子节点列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list.md) |
| `drive:export:readonly` | 将在线文档、表格等导出为本地文件 | [创建导出任务](https://open.feishu.cn/document/server-docs/docs/drive-v1/export_task/create.md) |
| `drive:file:download` | 下载 Wiki 中原始文件类型的节点，如 PDF | [下载文件](https://open.feishu.cn/document/server-docs/docs/drive-v1/download/download.md) |

原版数据源界面列出 `wiki:wiki:readonly`、`drive:drive:readonly`、`drive:export:readonly`、`docx:document:readonly` 四项。本机强制 `FEISHU_DOCX_PARSE_MODE=export`，无需为了 blocks 解析新增 `docx:document:readonly`，也无需为本次同步新增覆盖更广的 `drive:drive:readonly`。已有获批的 `drive:drive:readonly` 或 `drive:file:readonly` 可以满足文件下载接口，但脚本会提示已有较宽授权；这不是本方案要求新增的权限。

飞书实际访问取决于 **API 能力权限与文档、节点 ACL 的交集**。仅配置 scope 不能获得所有知识库内容；应用可用范围也不是云文档访问范围。由目标空间所有者将应用对应身份添加到合作伙伴专用空间，检查子节点继承和单独授权。可阅读身份配合允许阅读者导出，是权限更小的可选配置；本次用户明确选择可编辑身份，以满足部分文档的导出限制，后续沿用该选择。不要为了同步启用组织公开分享，也不要混用个人 `user_access_token` 扩大可见范围。节点列表要求调用身份有父节点阅读权限，且可能受权限过滤返回空页但仍有后续分页，必须继续分页。[官方节点列表说明](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list.md)

本次目标空间 ACL 已获授权并通过目录递归验证。空间统一授权使用**包含应用机器人的专用群**；当前还须保留用户已配置的应用可编辑授权，只处理 `7610776951925509072`，不自动添加管理员或扩展到其他空间。成员关系或继承权限变化后重新运行 `inventory` 和 `audit-documents`，以阅读、导出及实际同步结果验收。

可以在空间层统一授予阅读权限。将已发布应用作为机器人加入专用群，再由知识库管理员在目标空间的“知识库设置 → 成员设置”中将该群添加为“可阅读的成员”。这是更小权限的可选起点；本次已由用户选择可编辑授权，不要求为采用该建议而改回。使用应用机器人，不使用自定义机器人。若搜索不到应用，检查已发布版本的可用范围是否包含资源所有者。[知识库 API 常见问题](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/wiki-v2/wiki-qa)

默认情况下，知识库成员可访问既有页面，新增子页面继承父级权限；页面也可以在“管理协作者”中把“当前知识库成员”设为无权限，使该页面及其子页面不再沿用空间成员访问权。因此，空间统一授权不能覆盖所有页面例外，也不等于所有页面都允许导出。原先四篇是否曾存在特殊继承设置为**未验证**，不能仅根据历史失败推断继承被关闭。[设置当前知识库管理员和成员](https://www.feishu.cn/hc/zh-CN/articles/530997871518)、[快速了解知识库权限](https://www.feishu.cn/hc/zh-CN/articles/821998241087)

空间成员关系属于资源 ACL；应用身份 scope 是另一层能力授权。本次所需三个 scope 已生效，不需要为统一空间授权再申请权限，也不需要个人 OAuth。此前四篇虽可读但不能导出；用户调整选项并选择应用可编辑身份后，最终 19 篇 auth 结果均为 `view=true/export=true/edit=true`。这证明目标资源 ACL 也允许编辑，不只是应用拥有额外写 scope；本次连接器仍仅执行读取。未启用组织或外部公开分享。若用户日后主动选择收窄，可将应用降为可阅读身份并使相应文档允许阅读者导出，再逐篇验证；本次不自动撤销已授权的编辑权限。

管理员授权流程：

1. 在应用身份权限中声明所需权限，保存并发布包含这些权限的应用版本。
2. 执行 `permissions`，确认所需权限已经声明，查看每条 `grant_status`。`1` 为已授权，`2` 为未授权。[查询租户授权状态](https://open.feishu.cn/document/application-v6/scope/list.md)
3. 在本次授权范围内执行 `permissions --apply`。该接口没有请求体中的 scope 列表，只能针对当前应用已有的待授权权限发起申请，不能替代后台添加权限，也不能把 user 权限变成 tenant 权限。脚本遇到缺少声明、无关待授权项或未知状态时拒绝申请。[向管理员申请授权](https://open.feishu.cn/document/application-v6/scope/apply.md)
4. 管理员批准后再次执行 `permissions`。提交申请成功不是授权成功。

官方状态文档列出 `scope_name` 和 `grant_status`；本次实际响应还包含身份信息。脚本会区分返回的 `scope_type`，避免把用户身份权限误认为应用身份权限。不要反复提交申请来尝试修复缺少声明；官方接口存在同版本次数上限及重复申请错误。

当前三项权限均已授权，不需要再执行申请操作。后续版本若返回 `212002`，应核对待授权范围和版本上线情况；该错误本身既不是审批已提交的证据，也不是指定权限已授权的证据。

## 3. 凭据与运行配置

凭据只写入已忽略的本机 `.env`，不写入脚本、命令行、文档或日志。所需变量名如下，不在此记录实际值：

| 变量 | 用途 |
| --- | --- |
| `FEISHU_APP_ID` | 企业自建应用 ID |
| `FEISHU_APP_SECRET` | 应用密钥 |
| `SMOKE_ADMIN_EMAIL`、`SMOKE_ADMIN_PASSWORD` | 已有本机 WeKnora 管理账号；需要目标工作空间 Admin 或 Owner 权限 |
| `SMOKE_BASE_URL` | 默认 `http://127.0.0.1:8080`，必须对应受保护 app 容器的本机端口 |
| `SYSTEM_AES_KEY` | app 运行环境中的固定 32 字节加密密钥 |
| `OLLAMA_BASE_URL` | 本方案使用 `http://ollama:11434` |

`scripts/feishu-space-sync.py` 在内存中读取所需 `.env` 项，环境变量可覆盖同名项；不会输出 AppID、AppSecret、登录口令或 token。脚本通过 Docker inspect 在内存中检查运行容器的加密密钥长度，不输出完整容器环境。不要把原始 `.env`、`docker inspect` 或解析后的 `docker compose config` 输出粘贴进验收记录。

数据源创建时，AppID 和 AppSecret 经本机 API 交给 app。`DataSourceConfig.ToJSON()` 仅在 `SYSTEM_AES_KEY` 为 **32 字节**时加密 `credentials` 中的字符串值，再保存到数据库；缺失或长度错误会失去静态加密保障。32 个 ASCII 字符满足此长度，32 个中文字符不满足。沿用已有正确密钥，备份时安全保管，不能随意更换。脚本在提交凭据前检查运行容器的密钥长度；本次数据库只读布尔检查确认两项凭据的 `enc:v1` 密文格式均为 true，未输出凭据或密文内容。

创建、查询、列表和更新数据源的响应会移除 `config.credentials`，仅返回配置状态。普通 `PUT /datasource/{id}` 不更新凭据；轮换必须使用 `PUT /datasource/{id}/credentials`，由本机工具在内存中构造请求，不能在 shell 参数中写真实凭据。响应中的 `configured` 也不能代替实际连接验证。

`sync` 复用已有专用数据源时，会以当前 `.env` 或环境中的 AppID、AppSecret 原子替换整组凭据，再触发同步，确保权限预检和连接器使用同一个应用。它不读取旧凭据。`verify`、`snapshot` 不做该凭据替换，也不触发同步。

原版访问日志遗漏 `app_secret` 脱敏，飞书 token 获取日志还输出前后片段。此次已在 `internal/middleware/logger.go` 增加 AppID、AppSecret、飞书前缀及 tenant token 脱敏，请求和响应共用 `sanitizeBody`；在 `internal/datasource/connector/feishu/core/client.go` 删除 token 片段日志，只记录有效期。日志仍保留。本次在定制镜像运行后才提交真实凭据；对 app 日志检查窗口在内存中匹配真实 AppID、AppSecret，均未发现，不代表全部历史和未来日志均已验证。重复部署同样必须先运行定制镜像再提交凭据；标签只用于部署检查，不能证明任意来源镜像可信。

## 4. 重复部署与执行步骤

以下命令在仓库根目录的 PowerShell 7+ 中运行。需要 Docker Compose、Python 3 和可用的本地 Ollama 模型。命令不会打印凭据，构建和服务启动会改变本机运行状态。

```powershell
$PSVersionTable.PSVersion
python --version
python scripts/feishu-space-sync.py --help
python scripts/feishu-space-sync.py permissions --help
python scripts/feishu-space-sync.py verify --help
python scripts/feishu-space-sync.py snapshot --help
python scripts/feishu-space-sync.py audit-access --help
python scripts/feishu-space-sync.py audit-documents --help
```

本机覆盖配置位于 `docker-compose.local.yml`，app 镜像为 `weknora-local/app:feishu-v0.8.2`，构建文件为 `docker/Dockerfile.feishu-local`，要求镜像标签 `io.weknora.feishu-guard=1`。它强制 export 模式，将 `open.feishu.cn` 精确追加到 `SSRF_WHITELIST_EXTRA`，适配本机 DNS；不使用泛域名白名单，不关闭 SSRF 检查。Ollama、DeepSeek 的已有配置与飞书目录访问范围不是同一层控制。

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml build app
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d postgres redis docreader ollama app frontend
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T ollama ollama list
```

首次部署缺少模型时执行：

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T ollama ollama pull bge-m3:latest
```

检查 app 健康状态与镜像标签，不输出其环境变量：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8080/health
Invoke-RestMethod -Uri http://127.0.0.1:8088/health
$feishuImageId = docker inspect --format '{{.Image}}' WeKnora-app
docker image inspect --format '{{index .Config.Labels "io.weknora.feishu-guard"}}' $feishuImageId
```

本地模型必须是 `local-embedding`、`source=local`、`type=Embedding`、`name=bge-m3:latest`，维度 1024。Dockerfile 构建阶段的 middleware、飞书 core、wiki 三包测试全部通过，包含最新官方 origin 校验修复，最终镜像已构建并部署。可在具备依赖的 Go 1.26 环境单独运行模拟测试：

```powershell
go test -ldflags='-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn' ./internal/middleware ./internal/datasource/connector/feishu/core ./internal/datasource/connector/feishu/wiki -count=1
```

此命令与 `docker/Dockerfile.feishu-local` 一致，将上游 Qdrant、Milvus 依赖的 protobuf 同名注册冲突处理为警告，避免测试进程在初始化阶段退出。

helper 当前 29 个模拟测试已通过，覆盖只读核验与同步权限分离、摘要配置差异、原有范围和增强开关检查、快照字段过滤。不调用真实飞书接口，可单独复跑：

```powershell
python -B scripts/test_feishu_space_sync.py
```

权限检查、目录预检和真实同步依次执行；某一步失败后停止，不把失败当作空目录成功：

```powershell
python scripts/feishu-space-sync.py permissions
```

仅在未来确有未授权的所需应用身份权限、已完成声明且没有无关待授权项时使用以下命令；本次权限已经生效，无需执行：

```powershell
python scripts/feishu-space-sync.py permissions --apply
```

管理员批准后，且第 5 节同步策略检查通过时：

```powershell
python scripts/feishu-space-sync.py permissions
python scripts/feishu-space-sync.py inventory
python scripts/feishu-space-sync.py audit-access
python scripts/feishu-space-sync.py sync --timeout 7200
python scripts/feishu-space-sync.py verify --timeout 7200
```

`inventory` 仅读取目标目录元数据；`audit-access` 分页核对应用可见 Wiki 空间，可选检查用户指定的拒绝样本；`audit-documents` 只读检查目标 docx 的阅读和导出权限；`sync` 在策略检查通过后创建或复用专用资源并抓取正文写入本机；`verify` 不触发同步，核对目录、解析状态，可进一步验证指定 probe 的真实关键词检索；`snapshot` 保存只读对账基线与当前导入策略状态。所有动作都会取得所需 token。脚本没有自动修改或删除飞书 probe 的子命令，具体参数以 `--help` 为准。

脚本不覆盖同名但配置不兼容的知识库或数据源。发生冲突时应检查配置和归属，不删除后重建来绕过检查。脚本也不处理首次管理员注册，请先完成本机管理员账号及工作空间创建。

## 5. 专用知识库、数据源和 API

知识库和数据源现名均为「合作伙伴专用空间」，脚本兼容旧显示名 `feishu-7610776951925509072`，描述归属标记仍为 `managed-feishu-sync:7610776951925509072`。2026-10-08 初始配置使用本地 BGE-M3 向量化，开启向量与关键词索引；`summary_model_id` 为空，关闭问题生成、自动标签、知识库 profile、Wiki、知识图谱、VLM 和 ASR。脚本新建知识库仍使用这套配置。它避免导入摘要等步骤调用远程模型；主动选择远程聊天模型提问是另一项数据处理操作。

**2026-10-10 配置差异与只读检查：**现存知识库 `d9285ed1-0760-474b-ba82-a13347598176` 的向量模型仍为 `local-embedding`（本地 BGE-M3），但 `summary_model_id` 已为 `local-deepseek-chat`。该模型的来源为 `remote`，不能由 ID 中的 `local` 判断为本地推理。问题生成、自动标签、profile、VLM、ASR、Wiki 和知识图谱仍关闭；19 篇正文解析完成，现存摘要状态为 failed；数据源仍为 paused、空 schedule、增量模式、删除同步关闭。这些只读数据库结果不代表今天执行了新的正文同步。

普通摘要默认启用，独立于上述增强开关：`internal/application/service/knowledge_process_config.go` 中 `ResolveProcessConfig` 默认 `SummaryEnabled=true`，`internal/application/service/datasource_service.go` 的文件导入未传入关闭摘要的逐文档覆盖。因此当前配置重新导入时可能调用远程摘要模型，不能简单删除脚本的摘要检查。管理员交接脚本仅修改知识库创建者并在事务内核对其余配置未变，说明摘要配置在交接前已存在；具体修改人、时间和入口**未验证**。前端新建时会选择默认聊天模型，编辑表单也要求聊天模型，但这不足以证明本次差异由哪次界面操作产生。

当前 helper 的行为分开如下：

| 动作 | 当前非空摘要模型下的行为 |
| --- | --- |
| `verify` | 允许核对已有资源；不创建资源或触发同步，输出 `validation_mode=read_only`、`summary_model_configured=true`、`sync_allowed=false` |
| `snapshot` | 与 verify 相同，并将这些字段写入 JSON 的 `ingestion_policy`，不保存模型配置、密钥或正文 |
| `sync` | 在调用数据源或同步接口之前拒绝；不自动清空摘要模型、不改变已有配置 |

本地向量模型、目标空间、归属标记、索引、增强关闭和数据源范围等检查均继续严格执行。`sync_allowed` 仅表示此 helper 的导入策略判断，不是后端全局同步开关；不要通过界面或其他 API 绕过当前未解决的摘要策略。数据源 paused 也不等于取消已有队列任务。

当前只读复核命令如下；快照路径必须尚不存在。离线测试已通过，真实只读快照结果以本次命令输出为准，不沿用历史验收结果：

```powershell
python scripts/feishu-space-sync.py verify --timeout 300
python scripts/feishu-space-sync.py snapshot --output temp/feishu-readonly-20261010.json --timeout 300
```

恢复操作性同步仍待确定摘要策略并重新验收：保持入库不调用远程摘要，需要明确禁用导入摘要或处理空摘要模型与现有界面的兼容性；若允许远程摘要，则需明确数据处理范围后另行调整策略。本次仅修复只读核验兼容性，没有修改知识库、数据源、模型或飞书内容，也没有替用户作出该策略选择。

空 `summary_model_id` 不阻止上游普通摘要任务入队。摘要 worker 在 `internal/application/service/knowledge_process.go:1290` 检查到模型为空后跳过模型调用，并将 `summary_status` 设为 `failed`；该状态不等于 `parse_status=failed`。正文解析、索引和摘要必须分别验收：本方案要求 `parse_status=completed` 且实际检索成功，不要求生成摘要。本次 19 个源节点均为 docx，没有表格节点。

数据源核心配置如下，凭据由脚本在内存中补充：

```json
{
  "knowledge_base_id": "<专用知识库ID>",
  "name": "合作伙伴专用空间",
  "type": "feishu",
  "config": {
    "type": "feishu",
    "resource_ids": ["7610776951925509072"],
    "settings": {
      "timezone": "Asia/Shanghai",
      "allowed_space_id": "7610776951925509072"
    }
  },
  "sync_mode": "incremental",
  "sync_schedule": "",
  "status": "paused",
  "sync_deletions": false,
  "conflict_strategy": "overwrite"
}
```

空间 ID 必须是字符串，无 `space:` 前缀。当前空 schedule 不创建 cron，`paused` 禁止定时触发但允许人工同步；这两项不会取消已入队失败任务的自动重试，不能把它们理解为后台不会再访问飞书。任务使用 Asynq `MaxRetry(5)`，重试可继续处理并更新原同步日志。若将来启用 schedule，该版本使用包含秒的六字段 cron。首次 incremental 无游标，仍会遍历所选空间的完整可见目录；后续使用游标保存的节点编辑时间跳过未变化内容。此实现通过轮询比较更新时间，不是飞书事件推送。

| 操作 | 本机 API | 响应形状或用途 |
| --- | --- | --- |
| 创建知识库 | `POST /api/v1/knowledge-bases` | `{success,data:{id,...}}` |
| 列出知识库 | `GET /api/v1/knowledge-bases` | `{success,data:[...]}` |
| 创建数据源 | `POST /api/v1/datasource` | 裸数据源对象；创建请求需 `config.credentials` |
| 列出数据源 | `GET /api/v1/datasource?kb_id={kb_id}` | 裸数组；列表附带最近同步日志 |
| 查询、更新数据源 | `GET/PUT /api/v1/datasource/{id}` | 裸对象；更新时保留完整配置，普通 PUT 忽略 credentials |
| 轮换凭据 | `PUT /api/v1/datasource/{id}/credentials` | 请求为 `{credentials:{app_id,app_secret}}`；响应仅含配置状态 |
| 测试已保存凭据 | `POST /api/v1/datasource/{id}/validate` | `{status:"connected"}`；仅证明 token 获取成功 |
| 列出目标一级目录 | `GET /api/v1/datasource/{id}/resources?parent_id=7610776951925509072` | 裸资源数组；不等于全树数量 |
| 触发同步 | `POST /api/v1/datasource/{id}/sync` | 无需请求体；裸 SyncLog 对象，保存其 `id` |
| 查询本次同步 | `GET /api/v1/datasource/logs/{log_id}` | 裸 SyncLog，含 `status`、`items_*` |
| 查询历史 | `GET /api/v1/datasource/{id}/logs?limit=10&offset=0` | 裸数组 |
| 分页验收文档 | `GET /api/v1/knowledge-bases/{kb_id}/knowledge?page=1&page_size=100` | `{success,data:[...],total,...}` |
| 查询单篇文档 | `GET /api/v1/knowledge/{id}` | `{success,data:{...}}` |
| 实际检索 | `POST /api/v1/knowledge-bases/{kb_id}/hybrid-search` | `{success,data:[...]}`；分块 ID 字段为 `id` |

`/sync` 不接收 `force_full` 控制参数；传入该字段不会强制全量。确需全量重抓时，先完整更新数据源为 `sync_mode=full`，触发并等待该次日志完成，再完整更新回 incremental。现有 helper 固定使用 incremental，不提供全量切换命令。全量重抓可能替换已有本地知识 ID，且不是可靠的删除对账替代品。

JWT 调用者需要当前工作空间 Admin 或 Owner 才能管理数据源；共享知识库接收空间不能配置源空间的数据源。API Key 若用于另行实现的工具，需要 `manage_datasources`，建知识库还需 `manage_kbs`，文档读取和检索还需 `retrieve`，并满足知识库范围限制。当前 helper 使用管理员 JWT，不创建额外 Key。

## 6. 空间保护的保证与边界

`settings.allowed_space_id` 是可选保护。未配置时保留上游行为，不能把整个 WeKnora 实例视为已经强制空间隔离。专用 helper 要求该值和 `resource_ids` 都精确对应目标空间。

启用保护时，`FetchAll`、`FetchIncremental` 和 `FetchStream` 都先检查全部选中目录树的元数据，之后才开始读取正文。异空间节点、所有 shortcut（包括同空间快捷方式）、缺少空间或类型信息、未知 `node_type`、受支持文档缺少对象 token、分页不完整等情况会拒绝本次抓取。资源选择器的根列表只返回允许空间，不枚举其他空间。未知或不受支持的 `obj_type`，例如 mindnote、slides，跳过正文并在数量对账中单独列出。

strict 模式已锁定对应地区的官方 HTTPS API origin，并拒绝显式空 `allowed_space_id`。飞书使用 `https://open.feishu.cn`，Lark 使用其对应官方地址；省略 `credentials.base_url` 时采用地区默认值，显式配置只接受官方地址及其单个尾斜线形式。自定义主机、代理路径、查询参数、空值及非字符串值会被拒绝。相关模拟测试已通过，覆盖在请求发出前拒绝不可信地址及空范围配置。仅 SSRF 校验不能证明公网服务是飞书；定制镜像现已部署并执行目标空间同步，但没有真实外部空间拒绝样本，不能把模拟拒绝测试描述为真实负向访问验收通过。

预检查失败或超时后不降级到无保护抓取。预检查会保存整棵目录树元数据，期间没有正文处理 checkpoint；大型空间或持续限流可能反复超时。当前任务超时为两小时，发生失败后还可能由任务队列重试；脚本超时不意味着后台任务已停止，先查询原同步日志再决定下一步。

这些检查不证明飞书节点在预检查后永远不移动，检查与导出之间存在并发变更窗口；也不约束管理员修改配置、其他数据源或应用已有的额外权限。必须同时维护飞书侧经用户选择的 ACL 和专用空间边界。官方 API 可能按权限过滤节点，成功返回也不能证明看到了空间所有内容。

## 7. 数量、解析与增量验收

不能只比较同步日志中的成功数量，更不能使用数据源的 `total_items_synced` 作为验收依据：当前生产代码未填充该字段。同步 `success` 表示抓取和提交本机处理任务成功，不保证异步解析、向量化已完成。

每次验收记录以下内容：

| 符号或集合 | 取数与判断 |
| --- | --- |
| N：原始可见节点数 | 对目标空间逐层递归、逐页列节点；记录类型分布和 `node_token` 集合 |
| M：支持节点数 | N 中 `doc/docx/sheet/bitable/file` 节点数；与不支持类型分开记录 |
| 本地来源集合 | 分页获取专用 KB 文档，筛选 `metadata.datasource_id`，核对 `external_id` 与源节点 token |
| 来源边界 | `metadata.space_id`、`source_resource_id` 均为目标空间；没有无关文档或重复 external ID |
| 完成数 | 每篇 `parse_status=completed`；解析状态 pending、processing、finalizing 继续等待；`parse_status` 为 failed 或 cancelled 不通过；空摘要模型导致的 `summary_status=failed` 单独记录 |
| 实际可检索性 | 用指定 probe 的唯一文本查询 hybrid-search，确认命中该文档对应 `knowledge_id` |

本次已验证飞书侧 `N=19`、`M=19`，全部为目标空间中的 docx origin 节点，且包含指定 probe。管理员已独立确认空间总数为 19，与应用递归数量一致。同步日志会被队列重试更新，以下区分当时观测与后续查询，不把每条日志当作不可变事件：

| 次数 | 同步日志 ID | total / created / failed | 结果 |
| --- | --- | --- | --- |
| 首次，当时观测 | `6f02d11b-cd2d-47c3-9352-3396e577d197` | 19 / 14 / 5 | partial；五篇创建导出任务返回 HTTP 403、`1069902` |
| 第二次，当时观测 | `3e4c249b-aa4c-4237-9ca6-6741e14090f1` | 5 / 1 / 4 | 用户补充阅读授权后，“第三部分成果与荣誉交付”成功导入 |
| 第三次，当时观测 | `f1c50627-38a4-4ba4-9636-cb84ec41538a` | 4 / 0 / 4 | failed；当时四篇均返回 `1069902`，任务随后被自动重试 |
| 第三次日志，后续查询 | 同上 | 4 / 1 / 3 | 04:42:55 UTC 已变为 partial，另补入 1 篇；`started_at` 仍为 04:31:23 UTC |
| 补齐目录的手动同步 | `7f02e8fe-b7b2-4d69-86e8-49df6b04b701` | 3 / 3 / 0 | success，`items_updated=0`；其余文档由此前同步及重试导入 |
| 用户修改 V2 后的增量同步 | `1df478b1-0e73-4a77-8af8-7dd84f82d338` | 1 / 0 / 0 | success，`items_updated=1`，仍为 19 篇解析完成 |
| 不再修改正文的重复同步 | `6a005d74-f6b9-4e74-bd34-8e6e29f45ce2` | 0 / 0 / 0 | success，`items_updated=0`；19 篇完成，数量与 ID 均不变 |

当前累计 19 篇均 `parse_status=completed`，本地 external ID 完整覆盖源 19 节点，完整 V1 和 V2 验证均退出 0。补齐目录的手动同步只新增 3 篇仍可达到总数 19，是因为旧失败任务已自动重试补入 1 篇；不能只累计某次打印的创建数判断缺失。源码 `internal/application/service/datasource_service.go:494` 配置 `MaxRetry(5)`，1117 附近的错误处理会保存状态并返回错误交由队列重试。当前未配置 cron 且数据源 paused，也不阻止这些已入队任务重试。首次导入的 14 篇均观察到 `summary_status=failed`，符合空摘要模型跳过远程摘要的预期，不能据此判定正文解析失败。

排查此前失败的四篇时，使用已授予的 tenant `wiki:wiki` 调用 `GET /drive/v1/permissions/{obj_token}/members/auth?type=docx&action=view` 和同一路径的 `action=export`，只读取权限元数据，没有新增 scope；接口的 `data.auth_result` 区分阅读与导出权限。[判断当前应用是否有云文档权限](https://open.feishu.cn/document/server-docs/docs/permission/permission-member/auth.md)

现已封装为可复用的 `audit-documents` 命令。默认检查目标空间全部 docx；也可以重复传入 `--node-token`，仅检查指定节点。以下是默认检查和本次四篇的定向检查，按需选择：

```powershell
python scripts/feishu-space-sync.py audit-documents
python scripts/feishu-space-sync.py audit-documents --node-token EqrtwEBRKiZyqZkH7s6cMIB0nQh --node-token BeXAwQI4niJSkOki6nycZhQhnob --node-token NL4TwzQsNi8GR1kJThzcHeQsnJb --node-token LBDgw64aPiAmUikWXSgcQdEBnse
```

该命令先完成目录范围预检，再用 `get_node` 复核所有选中节点属于目标空间、为 origin docx；全部通过后才逐篇查询 `view`、`export`。它不取正文，不输出对象 token、标题或凭据，也不申请 scope。当前应用已有 tenant `wiki:wiki`，满足此诊断接口的能力要求；只有三个最小同步 scope 的另一应用未必能调用该诊断接口，不能把接口无权调用误判为文档不可读。结果仅输出节点 ID、动作和布尔 `auth_result`；接口错误输出 `check_error` 及数值错误码，响应缺少有效布尔值则标记未验证。全部为 true 才退出 0，有 false 或未验证则退出 1。调整前全 19 篇诊断为 view 19 true、export 15 true 和 4 false，退出 1；调整后 Eqrt 节点先变为阅读与导出均 true，其余 BeXA、LBDg、NL4T 三节点随后均为 true，最后三篇定向诊断退出 0。结合已通过的其余 15 篇及完整导入验收，当前 19 篇均可导出。

最终又用同一 tenant 应用的原生 auth 接口遍历目标 19 篇 docx，分别查询 `action=view`、`action=export`、`action=edit`，只读元数据，结果为 `view_true=19`、`export_true=19`、`edit_true=19`，无 `sync_permission_denied`。这项 edit 检查是单独的原生查询，不能误称 `audit-documents` CLI 已内置 edit 检查。复查可见 Wiki 空间仍只有目标 1 个；上述结果不覆盖其他 Drive 文件、单篇外链或外部拒绝样本。

| 文档 | Wiki 节点 token | 当前验证结果 |
| --- | --- | --- |
| 01.1项目产品手册 | `EqrtwEBRKiZyqZkH7s6cMIB0nQh` | 调整为可阅读用户导出后 `view=true/export=true`，已导入并解析完成 |
| 第三部分成果与荣誉交付 | `QuHwwFjIkipT8xkC9dscklXnnJd` | 第二次同步成功，已解析完成 |
| 01.3管理标准化 | `BeXAwQI4niJSkOki6nycZhQhnob` | 用户选择应用可编辑身份后 `view=true/export=true`，已导入并解析完成 |
| 学校荣誉申请申报指南 | `NL4TwzQsNi8GR1kJThzcHeQsnJb` | 用户选择应用可编辑身份后 `view=true/export=true`，已导入并解析完成 |
| 白名单赛事带赛手册 | `LBDgw64aPiAmUikWXSgcQdEBnse` | 用户选择应用可编辑身份后 `view=true/export=true`，已导入并解析完成 |
| 指定 probe | `JJ0zw4wfjiSGwSkypq3csTVJnCc` | `view=true`、`export=true`，V1 与 V2 真实检索均通过 |

创建导出任务的官方错误说明把 `1069902` 描述为缺少文档阅读权限，因此首次按阅读授权排查；独立检查随后发现四篇可读但不可导出，说明不能仅凭该错误码判定缺少阅读权限。[创建导出任务错误码](https://open.feishu.cn/document/server-docs/docs/drive-v1/export_task/create.md) 用户调整导出选项并选择应用可编辑身份后，四篇权限、真实导入及完整 V1 验收均已通过；本次没有新增 scope。

以下是用户最后调整前的只读诊断历史，不代表当前 ACL；诊断请求本身未修改飞书权限，也未新增 scope：

| 检查 | 当时失败的四篇文档 | probe 对照 | 结论边界 |
| --- | --- | --- | --- |
| `GET /drive/v1/permissions/{obj_token}/public?type=docx` | 均为 `security_entity=anyone_can_edit` | `security_entity=anyone_can_view` | v1 是官方历史接口，字段差异不能完整证明现代权限继承或企业策略 |
| 同一 v1 public 接口，改用 Wiki 节点 token 与 `type=wiki` | 与 docx 查询结果一致 | 与 docx 查询结果一致 | 已排除本次这两种 token/type 查询结果不同的情况，不能推出所有权限来源一致 |
| `GET /drive/v2/permissions/{obj_token}/public?type=docx` | 均为 HTTP 403、`1063002` | 同为 HTTP 403、`1063002` | `copy_entity`、`lock_switch` 均**未验证**；不能把接口拒绝解释为这些字段的值 |
| `GET /drive/v1/permissions/{obj_token}/members?type=docx&fields=type` | 均有当前应用记录：`perm=view`、`perm_type=container`、`type=app` | 未见直接应用协作者记录，已有空间授权且实测可阅读、可导出 | 当前 AppID 只在内存中匹配，未输出 AppID 或 member ID；不需要再次逐篇添加应用 |

这些历史证据用于说明当时的有效权限差异，不能完整确定现代继承、企业策略或锁定状态。用户随后已调整导出选项并明确选择可编辑授权，当前导入阻塞已解除，不再等待该选项文字或重复添加应用。v2 的 `copy_entity`、`lock_switch` 未重新取得，仍保留未验证。

`metadata.datasource_id` 与同步日志中的 `data_source_id` 拼写不同。文档列表没有按 datasource ID 的独立查询参数，应分页后按 metadata 检查。附件可能展开为带 `#file#` 或 `#image#` 的 external ID；不能把文档总数直接等同于 Wiki 节点数。内容哈希去重、不支持类型、无权看到的子节点、解析失败都要单独解释，不能因总数接近就宣称完全一致。

当前 helper 会严格检查支持节点覆盖、来源集合、重复项及解析完成状态；缺失项不会自动忽略。本次应用递归数量已与管理员确认的 19 项一致，但总数相同本身不能替代本地 external ID 集合及正文解析验收。后续目录或 ACL 变化后，应重新核对空间总数与目录集合。

用户已指定目标空间内的 probe 节点 `JJ0zw4wfjiSGwSkypq3csTVJnCc`，其空间链接使用同一 `leboshiedu` 租户。完整 V1 验证退出 0，确认 19 篇覆盖与解析完成，并真实检索命中 probe；V1 的 `knowledge_id` 为 `b6053950-898c-4a90-8f18-c38628189af7`，分块为 `9a4de8e5-8eae-4dbf-b59f-eee9d41b0c5e`。随后 `snapshot --output temp/feishu-before.json` 成功保存 19 篇基线。用户已修改为 V2 并完成增量验收，未删除该节点。

重复部署时，先确认指定 probe 的 V1 正文包含 `WEKNORA_FEISHU_PROBE_V1`。完成首次导入后，验证 V1 的索引内容并保存基线。以下流程本次已经完成，现有快照不会被覆盖：

```powershell
python scripts/feishu-space-sync.py sync --timeout 7200
python scripts/feishu-space-sync.py verify --node-token JJ0zw4wfjiSGwSkypq3csTVJnCc --expect-text WEKNORA_FEISHU_PROBE_V1
python scripts/feishu-space-sync.py snapshot --output temp/feishu-before.json
```

快照只允许写入仓库 `temp` 下的 JSON 路径，拒绝覆盖已有文件；重复测试请选择新的文件名，并在后续 `--baseline` 中使用同一路径。快照保存空间、数据源、文档 ID、哈希和时间戳；2026-10-10 起另保存 `ingestion_policy`，版本仍为 1，历史基线继续兼容。不保存正文、模型配置、AppSecret 或访问凭据。节点标识可能以 external ID 保存，这是对账元数据，不是认证 token。

用户将同一 probe 文档改为包含 `WEKNORA_FEISHU_PROBE_V2`，记录修改时间，然后执行：

```powershell
python scripts/feishu-space-sync.py sync --timeout 7200
python scripts/feishu-space-sync.py verify --node-token JJ0zw4wfjiSGwSkypq3csTVJnCc --expect-text WEKNORA_FEISHU_PROBE_V2 --baseline temp/feishu-before.json
```

该验证要求 probe 已解析完成、关键词检索命中该文档的真实分块且包含 V2 标记，并要求相对基线的 `file_hash` 或 `source_updated_at` 至少一项变化。仅 knowledge ID 改变不能通过；节点或对象编辑时间另作记录。更新实现可能删除并重建本地 knowledge，ID 变化本身也不是失败。

本次用户修改后的 V2 同步日志为 `1df478b1-0e73-4a77-8af8-7dd84f82d338`，`items_total=1`、`items_created=0`、`items_updated=1`、`items_failed=0`，状态 success。上述带 V1 基线的完整 `verify` 命令退出 0，19 篇均解析完成，真实关键词检索命中 V2。验收证据如下：

| 字段 | V2 验收结果 |
| --- | --- |
| `hash_changed` | true |
| `source_updated_at_changed` | true |
| `object_edit_time_changed` | true |
| `node_edit_time_changed` | false；不能仅用 Wiki 节点时间判断正文是否变化 |
| 更新前 knowledge ID | `b6053950-898c-4a90-8f18-c38628189af7` |
| 更新后 knowledge ID | `9e5b0d17-1622-4e29-85d6-3645a773fdae` |
| V2 命中分块 ID | `77a343a1-9462-4522-8fd2-4fca1b656a5c` |

不修改飞书内容，再次执行 `sync`、`verify`。无变化增量的 `items_total=0`、`items_skipped=0` 都合法，因为未变化节点在连接器中直接跳过；文档数量和 ID 集合应保持不变。

本次重复同步日志 `6a005d74-f6b9-4e74-bd34-8e6e29f45ce2` 为 success，`items_total=0`、`items_created=0`、`items_updated=0`、`items_failed=0`；19 篇仍解析完成，`incremental_noop` 检查确认 count 与 IDs 均不变。随后执行以下命令并成功保存更新后快照：

```powershell
python scripts/feishu-space-sync.py snapshot --output temp/feishu-after.json
```

两份 JSON 快照比较为 19 → 19，external ID 集合相同；只有 probe `JJ0zw4wfjiSGwSkypq3csTVJnCc` 的 `knowledge_id`、哈希和 `source_updated_at` 变化，其他 18 个节点的这三项值均不变。快照与真实 V2 检索共同支持本次更新只影响指定文档，重复同步未额外重建其余文档。

helper 的 probe 验证仅执行关键词检索，不调用聊天模型或远程 embedding。等效请求如下：

```json
{
  "query_text": "WEKNORA_FEISHU_PROBE_V2",
  "knowledge_ids": ["<probe对应的本地knowledge_id>"],
  "match_count": 100,
  "keyword_threshold": 0,
  "disable_vector_match": true,
  "disable_keywords_match": false
}
```

完整 19 篇对账、解析、V1 与 V2 真实检索、前后快照、用户实际修改后的增量更新及无变化重复同步均已通过。飞书中新建文档仍未验证；导入现有文档不等于已验证飞书端的新建事件。

此外，原 `scripts/smoke-test.sh` 在新镜像下退出码为 0，真实 DeepSeek 回答、引用与三种检索通过。这项测试仅使用 testdata 创建的独立知识库，没有用飞书正文进行远程模型问答，不能替代上述飞书专用知识库验收。

## 8. 删除与外部访问负向验收

当前 `sync_deletions=false`，源文档删除后本地副本保留。此时严格目录对账可能因本地仍有已移除节点而失败，这是需要人工解释的保留策略结果，不应自动删除来让测试变绿。

如果未来经用户确认启用 `sync_deletions=true`，增量连接器依赖旧游标与本次完整枚举的差集检测删除，只删除本数据源对应的本地知识，不删除飞书文件。权限收缩、可见性变化也可能被当作节点消失；完整返回的目录未必等于空间真实全量。因此必须先排除授权变动。全量模式可能重置比较基线，不能声称一次 full 就能可靠清理所有历史残留。

删除验收须事先由用户确认具体 probe 和本地删除范围，保留前后 ID、游标和数量记录，再比较关闭与开启删除同步的结果。当前不执行真实删除，不修改现有删除策略；结果为**未验证**。

`audit-access` 已实测当前应用可见 Wiki 空间列表仅有目标空间 1 项。这只描述该列表接口的可见范围，不是 Drive、单篇文档分享或所有 ACL 的完整证明，也不替代拒绝样本测试。

用户确认目前没有可提供的外部拒绝对照空间，因此不执行该负向测试，结果保持**未验证**。未来仅在用户指定明确存在、属于其他空间的控制样本后，使用其给定的空间 ID 执行：

```powershell
python scripts/feishu-space-sync.py audit-access --deny-space '<用户指定的外部空间ID>'
```

该命令先验证目标空间可访问，仅请求外部空间的节点元数据；只有 HTTP 403 或已识别的飞书资源拒权码 `131006` 才记为样本被拒绝，随后再次确认目标空间仍可访问。404、空列表、网络失败或未知错误不能自动视为通过，成功列出外部空间则失败并要求检查 ACL。不导出或下载外部正文，不自行遍历其他空间寻找样本；一个样本被拒绝也不能证明应用对所有外部文档都无权访问。此项仍为**未验证**。

定制镜像已部署，19 篇全部解析完成，完整 V1/V2 检索、前后快照、真实增量更新及无变化重复同步均已通过。沿用用户选择的应用可编辑授权。未经确认不修改或删除飞书文档。真实新建文档、删除及外部拒绝样本仍未验证，不因完成现有文档导入与更新而标记通过。

## 9. 常见问题

| 现象 | 解释与处理 |
| --- | --- |
| 三项 scope 都已授权，目录可见，为什么仍可能导出失败 | 阅读和导出是不同权限。本次曾出现 `view=true/export=false`，用户调整导出选项并选择应用可编辑身份后已解决；`audit-documents` 可分别验证两种权限 |
| 已添加应用是否需要重新添加 | 不必反复添加。应核对有效阅读与导出权限；本次当前采用用户明确选择的可编辑授权，四篇原失败文档已经导入 |
| 当前能否称为只读应用 | 不能。目标 19 篇均实测允许编辑，且应用已有额外写 scope；本次同步仅读取。只有用户以后主动选择收窄时，才调整为只读加允许阅读者导出并重新验收 |
| 能否对整个空间一次授权，避免逐篇添加 | 可以将应用机器人所在专用群添加为目标空间可阅读成员。默认权限继承覆盖既有页面及新增子页，但页面可有继承例外，导出设置也需分别检查；不扩大到其他空间 |
| 同步状态为 partial，但已有文档出现在知识库 | 抓取可以部分成功，且旧 failed 任务重试会更新同一日志。本次最终已补齐 19 篇并完整验证，不能只凭某条旧日志判断当前总数 |
| 没有 cron 且数据源 paused，为什么文档数量仍变化 | 已入队任务仍可能按 Asynq `MaxRetry(5)` 自动重试。paused 与空 schedule 不会取消这些任务；本次旧失败日志重试补入 1 篇，最新手动同步因此只新增 3 篇 |
| 容器 healthy、健康接口 200，是否已经完成同步验收 | 仅证明服务健康。还需核对源与本地来源集合、`parse_status=completed` 及真实关键词检索 |
| `summary_status=failed`，是否代表正文导入失败 | 2026-10-08 初始配置留空摘要模型，worker 跳过摘要并设置该状态。当前已配置远程摘要模型，既有 failed 状态不能证明未来导入不会调用远程模型。独立检查 `parse_status` 与检索，并按第 5 节确认当前同步策略 |
| `verify` 或 `snapshot` 成功但 `sync_allowed=false` | 只证明已有数据可对账，摘要配置仍不符合 helper 的导入策略。实际 `sync` 保持拒绝，需先明确摘要处理策略，不要绕过校验 |
| 日志扫描没有发现凭据，是否保证从未泄漏 | 本次只对指定 app 日志窗口做内存匹配。已部署脱敏修复并通过相关测试，但不能由一次扫描推出全时段保证 |
