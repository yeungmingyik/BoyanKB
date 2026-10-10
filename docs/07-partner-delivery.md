# 合作伙伴交付审查

本轮日期：2026-10-10。范围是本机 `weknora-local` 的交付准备，不是阿里云上线。新版本功能、数据清理、正式环境部署分别验收；发现测试数据时不能标记为已达到交付标准。

## 用户界面

- 聊天输入框上方不再显示可选重排序模型提示。管理员在智能体配置中仍能看到检索配置，后台权限和受管预设校验继续执行。
- 业务技能先展示适用场景、需要提供的信息、确认和下载流程；完整技能协议折叠至技术详情。
- 文件生成过程显示中文操作名称与状态，成功响应中的内部参数默认折叠。真正失败的调用继续显示错误，不能用隐藏错误制造可用状态。
- 新对话和已有对话在手机宽度下可收缩，输入框和文件卡留在侧栏右侧的内容区域；其他管理页面的布局不受此覆盖影响。
- 模型“测试连接”、配置标识、引用来源、运行日志和 LICENSE 不属于应删除的验收数据。测试代码与历史验收记录保留在仓库，不作为伙伴业务资料导入。

## 工作空间授权

可用条件是当前账号属于有效工作空间、该空间已安装受管业务配置、技能版本校验通过，并且任务所引用的知识库对当前身份有效。系统管理员身份本身不会自动给每个空间安装三名业务智能体。

正式模式需同时支持新建伙伴空间和给已有空间启用业务能力。总部使用相同的权限和来源校验；不通过全局放开知识库、原生代码执行或前端标志实现开通。已有空间的成员角色、沙箱策略、同名自定义智能体不得被安装程序静默覆盖。

开通清单新增可选 `target_tenant_id`，省略时仍创建新的伙伴空间。指定时必须是已有有效空间，操作账号同时具有系统管理员、总部 Owner 和目标空间 Owner 权限。总部使用 `target_tenant_id == hq_tenant_id`、空 `members`，不创建合作组织或跨空间共享，也不新建账号；另建总部工作资料库，业务事实来源仍限定为清单中的总部知识库。工作资料不会自动成为总部权威资料。

已有空间必须已禁用代码沙箱，安装程序只读验证，不能替它关闭现有其他功能。遇到同名但配置不同的自定义智能体会停止供管理员检查；重复执行只核对已登记的智能体，不覆盖用户元数据或配置漂移。已安装预设的空间不能换一个幂等键重复开户。

本机总部示例为 `presets/workspaces/headquarters.example.json`；其中空间和知识库 ID 只适用于当前本机，迁移环境需核对实际 ID。默认只生成本地计划，不联网或读取凭据：

```bash
bash scripts/onboard-partner.sh --manifest presets/workspaces/headquarters.example.json
bash scripts/onboard-partner.sh --manifest presets/workspaces/headquarters.example.json --base-url http://127.0.0.1:8080 --env-file .env --apply --report temp/partner-delivery/headquarters-installed.json
```

执行模式使用 `.env` 中的 `ONBOARD_ADMIN_EMAIL` 和 `ONBOARD_ADMIN_PASSWORD`，或环境中的管理员 Token；不要把密钥写入命令。源知识库、模型、三名智能体、九项技能和渲染健康全部通过检查后才标记 ready。随后总部成员可直接进入“工具箱 → 业务技能”，无需切换到验收伙伴。

本机已完成总部开通，状态为 `ready`：三名业务智能体、九项技能全部可用且摘要验证通过；重复执行保持相同资源 ID，没有新建工作空间、账号或组织。证据为 `temp/partner-delivery/headquarters-installed.json`。执行前确认总部不存在任何已配置沙箱，再明确关闭该空间脚本执行；安装程序本身仍只读检查已有空间沙箱策略。

开通适配层将 v1 预设的旧 `question_suggestions.followups` 禁用配置转成 API 的 `follow_ups` 禁用配置，避免序列化差异导致重跑误报漂移；不改预设文件、版本或摘要。其他旧追问配置拒绝自动适配，已启用追问等真实配置漂移仍会停止。

## 正式名称

已将本机总部名称设为“博研总部”、管理员显示名设为“总部管理员”，真实飞书知识库及数据源名称设为“合作伙伴专用空间”。资源 ID、资料正文、账号邮箱和权限保持原值。登录后切换到“博研总部”即可使用；旧浏览器缓存的显示名可通过重新登录刷新。

`scripts/normalize-delivery-names.py` 默认只给计划，`--apply` 只更新四个指定对象的名称及更新时间，事务内验证容器项目、ID、旧名与新名；`--restore --apply` 可回退这四个显示名。执行后重复计划已全部显示 `changed=false`。飞书同步脚本兼容正式名与旧自动名，并继续验证原来源标记、空间 ID 和模型范围。

```bash
python scripts/normalize-delivery-names.py
python scripts/normalize-delivery-names.py --apply
```

## 已发现的数据清理范围

依据总部开通后的只读数据库快照 `temp/partner-delivery/audit-headquarters-ready.json`。清单中的每条资源都有实际 ID，执行前应重新核对。此表是待审批清单，不是已删除记录。

| 对象 | 当前范围 | 处理要求 |
| --- | --- | --- |
| 测试工作空间 | 10001、10002 两个初始个人空间，10003、10004 两个预设验收伙伴空间 | 总部业务能力验证通过后再退役；不得只改成正式伙伴名称 |
| 测试账号 | `presets-20261009-a`、`presets-20261009-b`，均为测试邮箱 | 连同默认空间和成员关系审查，保留总部管理员 |
| 测试智能体 | 总部 `local-smoke-rag`、`品牌验收（测试专用）`；验收 A/B 各三名 | 总部新安装的正式三名智能体不在此范围 |
| 测试知识库 | `1b285a4d-6850-4d95-8773-c642e709f408`；A/B 两个专属资料库 | 含 3 篇冒烟及隔离标记文档，不删除业务飞书库 |
| 测试共享 | 两个验收组织、两条总部知识库共享、关联组织成员 | 不把它们改成面向全部账号的共享 |
| 测试技能安装 | A/B 共 18 个目录项、两个安装登记 | 保留版本化技能资源与总部正式安装 |
| 测试嵌入入口 | `321397ef-a518-4337-ae3e-c70af72a207e` | 与品牌测试智能体绑定，退役时撤销入口 |
| 有验收标记的会话 | 71 个 | 标记只能证明创建来源；可能存在后来人工续聊，按实际 ID 清单确认 |
| 空嵌入会话 | 4 个、均无消息 | 随上述测试入口退役 |
| 总部冒烟会话 | `f894a7b5-6b22-473b-85d2-4ad1f77b2476`，含 10 条消息 | 单独确认；不可因为名称就断言所有内容均可删除 |
| 文件任务 | 50 个，其中 23 个已发布 | 删除会话不会清除任务中的文件字节；备份后单独处理 |
| 飞书探针 | 本地 `9e5b0d17-1622-4e29-85d6-3645a773fdae`；源节点 `JJ0zw4wfjiSGwSkypq3csTVJnCc` | 先在源端删除或移出同步范围，再清本地；否则下次同步可能重新导入 |

必须保留总部管理员身份、真实飞书知识库 `d9285ed1-0760-474b-ba82-a13347598176` 中的 18 篇业务文档、飞书源配置、模型、Logo、模板、技能版本以及许可证。19 篇的旧验收基线包含一篇探针；清理探针后的正式业务基线应是 18 篇，不应被误判为同步丢文档。

删除前需要完整备份数据库与文件，并核对本次要保留的资料。已有会话软删除与物理清除是不同操作，不能把列表消失当作备份、文件字节或所有历史记录都已消失。真实管理员邮箱与密码交接需要由部署负责人完成，不把本地冒烟账号邮箱当作正式交付账号。

## 可重复只读检查

```bash
python scripts/audit-delivery.py --project weknora-local --postgres-container WeKnora-postgres --report temp/partner-delivery/audit-before-release.json
node scripts/check-branding.mjs --url http://127.0.0.1:8088
python scripts/check-presets.py
```

报告路径必须尚不存在。数据审计脚本只有固定只读查询，校验容器所属 Compose 项目和服务，不输出凭据、完整邮箱、聊天正文或文档正文。退出码 `2` 表示找到需确认的已知测试资源，`1` 表示执行失败，`0` 仅表示没有匹配到已知验收标记，不能代替人工审阅全部业务内容。完整具体 ID 清单保存在本机报告中，不装入交付镜像。

需要人工验证：用实际伙伴普通账号逐一检查工作空间切换器、智能体、知识库及检索、会话历史、业务技能、下载文件和嵌入入口。使用无依据的问题检查拒答和引用。总部管理员能看到自己的配置，不代表普通伙伴也可读取；两类身份应分别检查。

## 镜像与生产部署差距

本机已运行以下构建，摘要写入 `docker-compose.delivery-local.yml`；七个服务均为 healthy。仅覆盖本机 Compose 项目，原生产镜像锁文件未变。

| 服务 | 本机镜像 | 摘要 |
| --- | --- | --- |
| app | `boyankb/app:v0.8.2-delivery-v1` | `sha256:6d9d6babb4b08085bb15cdb78a7418c80bcac1b3a54babd85a2b3eda2ab2c7d1` |
| frontend | `boyankb/frontend:v0.8.2-delivery-v2` | `sha256:db9c7bada464c8fe9929e91fda0084057edd041d7869e0cb74866918cd1fc421` |
| document-renderer | `boyankb/document-renderer:boyan-v2-delivery-v1` | `sha256:b874335fc84e984444d12ffb50644d3b395f8ff4344152a9f563337d816bb49a` |

在本机已存在这些镜像时，可以重复应用覆盖：

```bash
docker compose -p weknora-local -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml -f docker-compose.presets.yml -f docker-compose.delivery-local.yml up -d --no-deps --pull never app frontend document-renderer
docker ps --filter label=com.docker.compose.project=weknora-local --format '{{.Names}} {{.Status}}'
```

这些镜像目前只构建在本机；跨服务器需要受控传输或私有镜像仓库。重新构建会产生新的摘要，必须核验并更新对应覆盖，不能仅复用标签推断字节相同。

渲染镜像使用专属构建上下文白名单，只接收运行脚本和已批准的模板。应用镜像只复制业务预设运行文件，不复制 `presets/boyan/v1/tests`。前端最终镜像只发布构建后的资源，不导入本机测试数据库或 `temp/`。

现有 `docker-compose.prod.yml` 与 `deployment/production/images.lock.json` 仍锁定早期基础栈镜像，并非当前业务技能/PPT v2 交付栈。上线前必须在测试环境完成以下更新和演练，不能直接叠加本机配置后宣称生产就绪：

1. 应用和前端换成经过本轮验证的自定义镜像，并锁定镜像摘要。
2. 加入固定文档渲染服务、独立内部网络、调用令牌、健康依赖和资源限制；启用对应预设目录及 PPT v2 版本。
3. 初始化脚本生成渲染令牌；生产检查器的服务清单更新到实际栈。
4. 备份和恢复脚本目前只允许原七个服务，加入渲染服务后的完整栈需要重新验证，不能沿用旧版恢复通过的结论。
5. 使用正式域名 `kb.boyantech.com` 验证 DNS、HTTPS、邀请账号、伙伴隔离和撤权；服务器地域与规格仍需确定。

本轮不改已有生产配置，不连接云服务器。相关上线手册见 [生产部署](05-deploy-production.md)、[升级与回滚](05-upgrade-runbook.md)、[验收清单](05-acceptance.md)。

## 本轮验证

| 检查 | 结果与范围 |
| --- | --- |
| 总部开通 | 实际 API 安装及两次运行通过；三名智能体、九项技能、同一组资源 ID；未通过发起新对话产生验收文本 |
| Python 回归 | 开通、只读审计、飞书命名兼容、事务改名共 61 项通过 |
| Go 回归 | 开通服务、总部文件工作流、开通 HTTP 参数及授权校验通过 |
| 前端 | 本轮 52 项定向测试及完整类型检查通过；最终镜像额外运行 13 项 i18n、38 项既有交互测试，构建及 favicon 字节核对通过 |
| 已部署浏览器 | 62 项通过、0 失败：总部九项技能可用、三名智能体绑定、正式显示名、技能中文说明与技术协议折叠、聊天无重排常驻提示；390px 新旧聊天输入框及文件卡均位于侧栏右侧且没有横向溢出；无页面错误、业务写入或外联尝试 |
| 品牌 | 524 个前端源码文件、13 个后端接入文件无未批准残留；部署后 14 个静态 URL 通过；此扫描不证明用户上传资料或历史会话已清理 |
| 名称 | 实际四个对象改名后再次计划均为 `changed=false`；未改邮箱、密钥和正文 |
| 运行状态 | 本机 app、frontend、document-renderer、docreader、PostgreSQL、Redis、Ollama 均 healthy |

浏览器报告：`temp/partner-delivery/ui-2026-10-10T02-31-11-474Z/report.json`。已查看同目录 `headquarters-skills-desktop.png`、`partner-planner-mobile.png`、`partner-tool-mobile.png`；图片中的既有测试伙伴或历史内容仍对应待审批清理范围，不代表已清除。只读浏览器验收未新建会话、发送问题或添加测试资源。

离线回归入口：

```bash
python -m unittest scripts/test_onboard_partner.py scripts/test_audit_delivery.py scripts/test_feishu_space_sync.py scripts/test_normalize_delivery_names.py
```

需要人工验证：实际总部及伙伴成员的业务任务、内容准确性、正式文件的 Office 打开效果、200% 缩放和触屏输入；本轮界面与安装验证不能替代专业审校。生产网络、备份恢复、真实伙伴撤权和外部源删除应在最终生产版本另行演练。

2026-10-10 后续企业试用交接已按新授权退役上述旧账号、验收空间和非源知识库，并使用单一飞书知识库重新开通总部和星辰教育。实际备份恢复、邀请注册、只读问答、Word 打开与临时域名结果以 [企业试用交付](08-enterprise-trial.md) 为准。用户选择暂时保留飞书“测试文档”，不能将本轮交付描述成知识库完全无测试内容。
