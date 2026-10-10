# BoyanKB 上游升级与回滚

生产目标为 `https://kb.boyantech.com`，采用邀请制。本流程准备于 2026-10-09；没有连接或更新阿里云 ECS。部署入口见 [05-deploy-production.md](05-deploy-production.md)，交付门槛见 [05-acceptance.md](05-acceptance.md)。

## 当前发布基线

上游为 `Tencent/WeKnora v0.8.2`，提交 `3e8b0bfc80b845b2d4b2ed683994748741450a97`。生产 Compose 使用已完成品牌定制的 app/frontend，七个运行镜像及本地 embedding 模型清单均由 [images.lock.json](../deployment/production/images.lock.json) 记录。以完整 digest 判定版本，不能仅凭 `v0.8.2` 或 `latest` 标签判定相同。

本次部署层从定制提交 `408bee95` 延续；新增定制文件登记于 [CUSTOMIZATIONS.md](CUSTOMIZATIONS.md)。保存实际发布的 Git 完整提交、Compose、所有只读挂载配置、镜像清单、备份编号和验收报告。不得保存包含凭据的完整 `docker inspect`、已展开的 Compose 配置或原始调试日志。

以下 Git、Docker、Python 命令可在 PowerShell 7+ 使用；构建脚本需要 Bash。Linux ECS 使用发行版支持的 Python 3、Docker Engine 和支持 `!override` 的 Compose v2。服务器 OS、架构和容量尚未提供，当前镜像锁针对 `linux/amd64`；其他架构需要重新构建和完整验收。

## 在测试环境合入新版本

1. 确认工作区没有未保存工作；把当前生产发布保留在原分支。为候选版本创建 `feat/upgrade-目标版本`，遵循 Conventional Commits。不要覆盖用户未提交文件，不使用 `reset --hard`、强制推送或修改原生产分支来试升级。
2. 从仓库实际配置的上游远程获取明确标签，核对标签对应完整提交和发布说明。`upstream` 只是下例的远程名称，先用 `git remote -v` 确认。只合入指定标签，不追随浮动主分支。
3. 逐项比较 `internal/router`、身份鉴权、知识检索、共享关系、系统配置优先级、数据库迁移、飞书同步、沙箱协议、前端 Nginx 和内置 YAML。特别复核邀请链接多次使用、OIDC 自动开户、原始跨空间管理员标志、API Key 能力及 KB 白名单。
4. 保留 LICENSE 和上游版权声明；先重新应用 `branding/`、品牌构建入口及配置覆盖，再处理无法通过覆盖解决的核心差异。逐项核对 CUSTOMIZATIONS，不能用字符串全局替换协议、包名或版权。

```powershell
git status --short
git switch -c feat/upgrade-target-version
git remote -v
git fetch upstream tag <目标标签>
git rev-parse "<目标标签>^{commit}"
git merge --no-ff <目标标签>
git diff --check
```

合并冲突必须按新旧语义处理；不要无条件接受 ours/theirs。上述尖括号参数由操作员替换后执行。

## 构建并锁定候选镜像

使用 [scripts/build-image.sh](../scripts/build-image.sh) 构建前端和需要更新的 app。该脚本只接受可选参数 `--with-app`；通过 `BOYANKB_FRONTEND_IMAGE`、`BOYANKB_APP_IMAGE` 环境变量指定独立候选标签，禁止覆盖目前使用的发布标签。上游 app、DocReader、PostgreSQL、Redis、Ollama 任一变化都属于发布变更，不只测试前端。

```powershell
$env:BOYANKB_FRONTEND_IMAGE = 'boyankb/frontend:upgrade-candidate'
$env:BOYANKB_APP_IMAGE = 'boyankb/app:upgrade-candidate'
bash scripts/build-image.sh --with-app
```

新镜像经过验收后才推送企业私有镜像仓库；本次没有执行 ACR 推送。将仓库返回的完整 `registry/repository@sha256:...` 同步写入候选 Compose 和 `images.lock.json`，并在干净主机验证拉取。现有本地自定义镜像设为 `pull_policy: never`，未分发镜像的 ECS 不能直接启动。镜像离线分发及其验证要求见部署文档。

运行时 digest 锁定不等于构建逐位可重现。未来重建还应审查 Dockerfile 的构建基础镜像、依赖锁、Go/Node 工具链及下载资源，必要时固定其 digest；本次没有对所有构建输入做供应链签名或漏洞扫描。需要人工验证：由发布负责人检查镜像扫描报告、来源、支持期限及必须修复的漏洞，保存结论。

`bge-m3:latest` 是配置中的模型名称，真实内容由锁文件中的 manifest SHA-256 约束。升级模型不是简单改名称：维度、分块策略或模型内容变化可能需要重建向量索引。先在测试副本验证召回与索引迁移，保留旧模型缓存和旧索引的完整备份。

## 数据副本与迁移演练

1. 按 [production-backup.md](production-backup.md) 创建加密一致性备份。`--quiesce` 会短暂停止所选源项目的入口及写入服务，需要安排维护窗口。生产停服、配置替换、数据库操作必须事先获得负责人授权；本次只在新建隔离项目执行过该类演练。
2. 用 `restore-prod.py` 恢复到从未存在的 `boyankb-restore-*` 项目和新目录。先保持所有网络 internal、仅回环测试端口，禁止恢复后的飞书同步、Webhook、邮件或外部模型自动连接真实服务。
3. 恢复必须先使用备份时相同的镜像及配置。脚本会拒绝镜像差异、已有目标、自动迁移及未经明确列出的挂载配置变化。不要为直接恢复到新版本关闭这些检查。
4. 完成旧版本恢复校验后，才在该测试副本切换候选镜像。隔离的新空库也可用于功能样本，但不能代替真实旧 schema 的迁移演练。
5. 常态 `AUTO_MIGRATE=false`、`AUTO_RECOVER_DIRTY=false`。迁移前停掉该测试副本的 app、frontend、Caddy；使用候选 app 的独立临时容器显式启用迁移。临时容器不发布端口，继续使用隔离网络。入口脚本可能在迁移后启动应用后台任务，不能给它开放互联网。
6. 观察 `schema_migrations` 版本与 `dirty=false`、迁移退出状态及数据校验后停止临时容器，重新以禁止自动迁移的配置启动。若出现 dirty、超时或数据差异，停止发布；保留失败现场，另建恢复副本调查。不要自动清 dirty、回退迁移版本或反复启动碰运气。

迁移具体容器命令取决于目标版本 entrypoint，应先审查该版本脚本，再记录到发布记录。需要人工验证：新版本是否增加其他持久卷、外部向量库、对象存储、定时任务或密钥；当前备份实现覆盖 PostgreSQL 检索与 local 对象存储，发现不支持的外部组件会拒绝备份。

## 回归门槛

每次候选发布都执行 [05-acceptance.md](05-acceptance.md) 的配置、RBAC、租户 Key、检索、注入样本、无依据回答和恢复检查。隔离样本使用新前缀，不从生产导出伙伴秘密作为测试文本。调用 LLM 的测试会消费模型配额，脚本必须显式带 `--allow-llm`。

```powershell
python scripts/acceptance-prod.py --self-test
python deployment/sandbox/test_isolation.py
python scripts/test_prod_backup.py --age-binary <age可执行文件路径> -v
python scripts/check-production.py --project <测试项目> --env-file <测试环境文件> --compose docker-compose.prod.yml --compose docker-compose.staging.yml --base-url https://localhost:8443 --ca-file <测试CA文件> --http-url http://localhost:8089 --output <新报告路径>
python scripts/acceptance-prod.py --base-url https://localhost:8443 --env-file <测试环境文件> --ca-file <测试CA文件> --isolated --create-fixtures --random-identities --allow-llm --fixture-prefix <本次唯一前缀> --output <新报告路径>
node scripts/check-branding.mjs
```

候选镜像/版本变化后应同步更新检测器依赖的锁文件。验收 fixture 前缀须符合 `prodaccept-[a-z0-9-]{1,19}`，例如 `prodaccept-upgrade01`。源代码品牌检查与实际 HTTP 资源、登录布局、五语言、favicon、widget 是不同检查，按 [05-ui-polish.md](05-ui-polish.md) 执行现有界面验证。API 检查不能替代 Office 打开、页面可用性和中文资料质量验收。

**需要人工验证**：新旧真实业务问题集的召回及引用质量；飞书修改/删除语义；伙伴跨空间和共享授权界面；负载下延迟/OOM；定时备份失败告警；证书续期；如启用 Word/PPT，实际文件打开及品牌。任一上线必需项未通过，不能以脚本中某个 `PASS` 代替签收。

## 发布与回滚

阿里云生产切换尚未执行。发布时先取得维护及配置变更授权，完成最后一次停写备份，记录源版本、schema、备份 SHA-256 与解密私钥保管位置。保存旧发布目录和旧镜像，不覆盖密钥、不删除旧卷。先在受限入口启动候选并验证，再开放伙伴入口；DNS/负载均衡切换另行记录，不能把本机回环端口验证视为公网已切换。

若上线失败，先限制流量、停止新写入并保留失败数据库。没有 schema/存储格式变化且已验证旧版本兼容时，才可以切回旧镜像。**存在数据库迁移或兼容性不确定时，必须同时恢复旧版本数据库、对象文件、Redis、配置和原密钥，再使用旧镜像；不能仅把镜像标签改回去。**

恢复脚本故意只允许新的隔离目标。先完成隔离恢复与应用验证，再为已验证恢复数据准备生产发布实例并经授权切换入口；不要关闭隔离保护直接覆盖原生产数据库。回滚后可能丢失备份之后的写入，需要明确 RPO 并保留失败版本数据供人工补录。回滚也不能恢复已发送的外部通知或撤回已泄露的内容。

需要人工验证：记录每次实际 RTO/RPO、责任人、切换时间、回滚触发条件和恢复后伙伴验收结果。本机小规模合成数据演练没有证明生产数据量下的恢复时长。恢复私钥与加密备份必须保存在不同故障域；仅把两者放在同一 ECS 磁盘不能构成灾难恢复。

## 本机图谱覆盖的升级附加项

2026-10-10 的九服务试用栈使用 `docker-compose.graph-local.yml` 和独立 Neo4j 持久卷；此变更没有直接部署到 ECS，也没有修改生产 Compose。迁移到云端前要把 Neo4j 纳入完整备份、恢复和资源规划；现有七服务备份与生产恢复脚本不能作为新增图数据库的整站恢复证明。

升级候选需回归技能 catalog 角色门禁、受管包防覆盖、六份中文提示词覆盖、图谱补建的权限和来源摘要，以及共享库图检索的源租户解析。重复执行相同补图计划应维持已完成状态；撤销共享后伙伴不得获得图扩展分块。测试命令和真实验收范围见 [技能与图谱](12-skill-graph-prompts.md)，加密图谱冷备及同镜像新卷离线检查见 [局部备份](graph-partial-backup.md)。

本轮图谱功能未增加 PG schema 迁移，旧 app 镜像仍保留；回退前仍需保存当前配置与数据，确认旧代码能处理已经启用的抽取配置及排队任务。不要删 Neo4j 卷来“回滚”，也不要在保留图谱开关时直接撤掉数据库。跨 Neo4j 版本升级不能直接套用本次同镜像文件级恢复；应按目标版本官方迁移要求先在隔离副本验证。
