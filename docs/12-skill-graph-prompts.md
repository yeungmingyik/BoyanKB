# 技能管理、知识图谱与中文提示词

日期：2026-10-10。分支：`feat/skill-graph-prompt-fixes`。

## 成员关系

保留原生账号、成员及空间切换逻辑。刘伟健保留总部与星辰教育 Owner，可以切换两个空间；卡卡保持星辰教育 Viewer 和指定团队负责人，继续使用已授权的智能体、九项业务技能及邀请只读员工。此前所有权交接只形成文档，没有执行，因此不需要回滚账号数据。历史方案已标记取消。

## 业务技能与原生技能库

“业务技能”是 BoyanKB 新增的入口，展示五个业务场景及四种文件格式指南。对应技能包也保存在原生技能目录，但实际执行由受管智能体、固定版本指南和文档生成服务组成，包括资料引用、审校、确认及文件下载。它们不依赖通用代码沙箱。

原生“技能管理”支持上传带有 `SKILL.md` 的 ZIP 包或导入来源地址；目前没有在线编写 Skill 的文本编辑器。管理员和所有者可以管理技能包，保存到当前空间不等于已经安装并可运行。普通伙伴继续从“业务技能”入口使用获授权的现有能力。

加载失败的根因是试用网关同时拦截目录和沙箱接口，原页面又把二者一起加载。修复包括：

- 公网精确开放技能目录接口，服务端继续核验空间与角色。原生安装、会话沙箱和平台管理接口继续受限。
- 目录加载失败显示可重试错误，不再误显示为空目录。沙箱状态错误不抹掉已加载目录。
- `branding/brand.json` 的 `features.nativeSkillExecution=false` 控制当前界面的目录管理模式，不请求沙箱接口，只显示保存步骤。它只是界面配置，不能替代后端权限或网关限制。
- 受管预设引用的技能不能被普通上传覆盖或删除；完全相同内容的幂等登记允许通过。新普通技能包仍可由管理员保存，未安装的普通包可按原权限删除。

若以后要执行任意上传的脚本，需要另行配置隔离执行环境、出网策略、资源限制及 Agent 绑定，并同步调整界面配置和网关。此次没有将 Docker socket 暴露给应用，也没有把任意包的“保存成功”标为“可执行”。

## 知识图谱

原提示来自基础栈未部署 Neo4j。新增 `docker-compose.graph-local.yml`，锁定 Neo4j 镜像，仅 app 和 Neo4j 加入独立内部网络；不发布 7474/7687 主机端口。数据和日志使用独立持久卷，数据库容器限制 2GiB 内存；堆 512MiB、页缓存 256MiB。凭据由本机进程写入 `.env` 的 `BOYAN_NEO4J_PASSWORD`，不在文档或输出中展示。

上游图写入依赖 APOC，启用镜像内自带 core 插件，并限定所需过程；不开放文件导入导出。数据库健康检查使用实际 Bolt 查询，不能仅凭端口监听判定可用。

界面区分加载中、请求失败、明确未启用及已启用，失败可重新请求服务端状态。

现有“重新解析”会清理旧分块及索引，不能作为无损补图方法。此次采用只对现有分块执行实体关系抽取的方式。企业资料基线为 19 篇、27 个分块、正文 9167 字符；应在抽取前后核对原分块 ID 和正文摘要不变。

实际配置使用 `local-deepseek-chat`，启用八种中文关系：包含、提供、使用、依据、适用于、要求、负责、支持。抽取说明要求仅依据正文，不补写价格、职责或承诺。示例取自企业资料“教学包使用指南”：每堂课使用 PPT、逐字稿和导学视频。首轮只有启用开关、没有关系类型和示例，提示中的允许关系列表为空，本次模型只产出实体；补全配置后得到关系。当前代码没有调用旧的 `RemoveUnknownRelation` 过滤函数，不能将零关系归因于数据库或运行时过滤。不能只凭队列完成认定图谱可用。

新增资料的设置入口为“企业知识库 → 设置 → 模型配置 / 知识图谱”。保存图谱的类型、示例和说明走 `PUT /api/v1/initialization/config/:kbId`，必须保留原有模型、分块、存储和其他增强字段。普通 `PUT /knowledge-bases/:id` 的 `config` 不是部分补丁：省略分块配置会把它清零，且该接口不接收抽取配置。本轮已从变更前加密快照恢复原分块设置，并验证图谱及其他字段未变；原文、分块、向量摘要保持一致。

对本机既有企业资料补图，先取得计划摘要，再提交；同一调用人、相同来源及配置在队列保留期内重复提交不会重复入队。每次最多 100 篇、500 个选定分块；超过范围需另行分批，不要放宽脚本的完整性检查。

```powershell
python scripts/backfill-enterprise-graph.py
python scripts/backfill-enterprise-graph.py --apply --expected-plan-hash PLAN_HASH --wait-seconds 1200
```

`PLAN_HASH` 替换为上一条输出；`--retry-failed` 仅显式重试已归档失败任务。撤权、来源变化及配置变化停止任务，短暂数据库故障可重试。该操作可能产生模型费用，不会重新解析文档。飞书后续检查或同步须显式传总部空间及完整图谱策略，见 [飞书配置](02-feishu.md#10-启用知识图谱后的兼容边界)。

普通 RAG 的图扩展与 Agent 工具是不同路径。受管三位智能体仍保留不可变 v1 的检索、读文档及文档生成配置；数据库启用不代表自动向它们加入任意工具。图谱中的关系由模型抽取，关系质量和业务含义仍需要人工核对来源。

修复了共享库图扩展按当前伙伴租户读取总部切片的问题：来源租户由服务器知识库记录确定，检查共享权限、文件范围及切片所属库后读取，返回前再核对授权。带标签限制的请求暂不执行整库图扩展，仍保留普通受限检索。Neo4j 图不是 Wiki 页面链接图；上游名为 `query_knowledge_graph` 的工具当前仍调用混合检索，不能仅凭工具名称认定它查询了 Neo4j。

部署时在现有 Compose 文件链末尾追加图谱覆盖文件，不直接使用上游 Neo4j 的公开端口配置：

```powershell
docker compose -p weknora-local --env-file .env -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml -f docker-compose.presets.yml -f docker-compose.delivery-local.yml -f docker-compose.trial.yml -f docker-compose.graph-local.yml --profile neo4j up -d neo4j app frontend
```

不要打印 `docker compose config` 或完整容器环境。检查服务状态可使用：

```powershell
docker ps --filter label=com.docker.compose.project=weknora-local --format '{{.Names}} {{.Status}} {{.Ports}}'
python scripts/check-trial-gateway.py
python scripts/check-skill-graph.py
python scripts/check-skill-graph.py --base-url https://kb.boyantech.xyz
python scripts/acceptance-trial.py --base-url http://127.0.0.1:8080
```

最后一项区分自动检查和人工验收，自动项通过但仍有人工项时会以非零状态结束；应读取报告的 `automated_result`、`failed` 和人工说明。

## 中文提示词

提示词不要求必须使用英文；指令语言也不等于回答语言。中文化主要改善管理员阅读和维护体验，是否提高回答质量需同题对照验证，尚未验证。

新增四份覆盖文件：`branding/prompt_templates/{context_template,rewrite,fallback,intent_prompts}.yaml`，连同原有两份，共覆盖 32 个模板。保留模板 ID、工具名、JSON 字段、枚举、引用语法和运行时变量。三项固定兜底直接输出中文最终回复，不再把“使用某语言回答”的模板指令返回给用户。

运行中的总部与星辰教育六个受管 Agent，已保存的系统提示词均有中文正文，未批量覆盖已有自定义内容，也没有改动不可变 `presets/boyan/v1` 和 `skills/boyan`。今后用户自己保存的自定义英文正文优先于模板引用，不会被升级自动翻译。

运行验收另发现两个空间的内置“快捷问答”未配置模型。已分别用管理员原生 Agent 更新接口仅将 `config.model_id` 从空值改为 `local-deepseek-chat`，其他配置逐项回读相同；这不改三位受管业务智能体，也不改变成员角色或知识库共享范围。新空间若要使用原生快捷问答，需要在其 Agent 模型设置中绑定可用模型，仅在单次请求传 `summary_model_id` 不能代替这一步。

## 采购建议

按“小规模起步、可扩容”选择 x86_64 ECS，继续使用 DeepSeek API；当前不需要 GPU。建议先月付验证实际负载，购买前在所选地域核对可售规格及费用。

仅内部单人测试可选 **4 vCPU / 8GiB / 5Mbps**。同时启用本地向量模型、Neo4j、解析及文件渲染时内存余量有限，批量入库、抽图和 Office 生成错峰执行，不在该机器构建镜像；正式伙伴试用建议升到 16GiB。5Mbps 理论传输上限约 0.625MB/s，大文件下载需要等待。这个判断不是多人并发压测结论。

| 项目 | 小规模起步 | 留足并行处理余量 |
| --- | --- | --- |
| CPU / 内存 | 4 vCPU / 16GiB | 8 vCPU / 32GiB |
| 系列 | 通用型 g 系列，同档 x86 规格 | 例如 `ecs.g8i.2xlarge` |
| 操作系统 | Ubuntu 24.04 LTS x86_64 | 同左 |
| 系统盘 | 100GB ESSD | 同左 |
| 数据盘 | 200GB ESSD，可扩容，独立挂载 | 视原始文档及产物增长扩容 |
| 公网 | 起步 5Mbps 或设置流量计费上限 | 根据 PPT 下载体验调整 |
| 备份 | 独立 OSS 存储加密备份，保留历史版本 | 同左 |

这张表是结合当前小资料量和云端推理模式的工程估算，不是经过压测的并发保证。8 vCPU/32GiB 的规格对应关系来自[阿里云通用型规格文档](https://help.aliyun.com/zh/ecs/user-guide/general-purpose-instance-families/)。Neo4j 内存应分别考虑堆、页缓存和操作系统，参见[官方内存配置说明](https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/)。本机 2GiB 图数据库限制只适用于当前小图，不能推导为任意生产图谱容量。

4核16GiB 起步时限制文档渲染并发为 1，批量解析和图谱补建错峰执行；不在业务服务器上并行构建镜像。CPU 持续高于 70%、内存持续高于 80%、产生 OOM，或解析/文件生成队列持续增长时，先确认瓶颈，再升到 8核32GiB或拆分执行节点。磁盘剩余低于 30% 时扩容；实际恢复和扩容路径应在上线前演练。

地域需结合用户所在地与备案状态决定。阿里云中国内地源站通过域名提供服务仍需办理 ICP 备案，使用 Cloudflare 代理不会改变这一要求；参见[阿里云备案 FAQ](https://help.aliyun.com/zh/icp-filing/basic-icp-service/support/faq-about-icp-filing-preparations)。香港地域的 ICP 要求不同，仍应依据实际业务确认适用手续，不把选择地域视为所有合规义务的替代。

## 验证记录

| 检查 | 结果 |
| --- | --- |
| 公网 HTTPS 与网关路由 | 9 项通过，证书正常；匿名 catalog 401、技能安装 404、沙箱 403 |
| 管理员及伙伴接口 | 本机和公网各 16 项通过；目录、九项业务能力、角色及中文模板正常 |
| 浏览器 | 公网 10 项通过，无页面错误；9 个技能包可见、可打开上传界面、不请求禁用沙箱；图设置正常显示 |
| 伙伴既有权限 | 26 项自动检查通过；整体仍标需要人工验证，不以非零退出码否定已通过的自动项 |
| 图谱补建 | 19 篇、27 个活动分块全部 completed；Neo4j 384 个实体、326 条关系，缺失必要属性为 0；原文、分块、向量摘要未变 |
| 源配置恢复 | 分块、图片配置与加密基线 SHA-256 一致；其他字段、图谱开关及抽取配置未变 |
| 问答 | 总部及伙伴的快捷问答均完成中文回答，各 8 个唯一引用切片均在企业资料范围内；事件累计各 16 条，不是 16 个唯一切片 |
| 图检索权限回归 | 6 项定向及完整 chat_pipeline 包通过；另有真实插件链集成测试证明共享抽实体、源租户读取及撤权边界 |
| 补建权限与重试 | service 11 项及 12 个子场景、handler 2 项通过；CLI 13 项通过 |
| 技能包保护及界面 | Catalog/Skill 231 项；前端定向 18 项、图状态 3 项、类型检查和镜像构建检查通过 |
| 飞书 helper | 43 项离线测试通过；本地 19 篇完成及策略匹配，实时飞书目录连接超时，未执行同步 |

真实问答的最终引用未出现 `match_type=6`，因此**未验证图检索在这两题带来了独有引用或质量增益**。离线集成测试证明同一切片既被向量又被图命中时，会按现有顺序只保留向量来源标记；这只能解释可能性，不能作为本次模型实际执行了图分支的证据。Neo4j 实际读写、关系存储和共享权限逻辑分别已验证。

语义质量尚未验收：总部这道多主题问题没有召回“01.3 管理标准化 (SOP)”并表达了资料不足，而伙伴回答引用到了该文档；说明接口、引用范围通过不等于召回完整。上线前应拆分具体问题、核对引用原文，并用固定业务题集评价召回与回答，不宣称本轮已保证所有回答准确。

当前镜像锁：app `v0.8.2-trial-graph-v3@sha256:ac806ca72aa54f18c73b5a4140cb18c75aa0f3a41fe1a1cd2cb5b5f7da04d756`；frontend `v0.8.2-trial-skills-v3@sha256:4951e0703bd3c948554293a2e6fa3448db7206228b8e9fc4f481bf78f300537b`；Neo4j `2025.10.1@sha256:155c8aad10d5c838bc3bbc476c0418779086547822acb214ec5e3d49ba336907`。均为 linux/amd64，完整仓库名见 `deployment/trial/images.lock.json`。

本机证据保存在 `temp/enterprise-trial/skill-graph-ui/2026-10-10T07-28-51-988Z/`（最终截图与报告）、`temp/enterprise-trial/acceptance/20261010-151747-99ad425d/report.json`、`temp/enterprise-trial/gateway-graph-final.json`、`temp/graph-backup/graph-counts-after.json`、`temp/graph-backup/ingestion-config-restored.json` 及 `temp/graph-backup/qa-*.json`。包含业务问答的私有证据不提交仓库，不应作为公开下载资源。

需要人工验证：抽查图谱的实体、关系、来源引用是否符合业务含义；使用实际问题核对中文回答；用预期伙伴人数同时进行问答及 Office 生成，测量响应和排队时间后调整云配置。自动结构或接口检查不能证明所有业务回答正确。
