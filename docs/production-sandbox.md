# 生产沙箱与 Agent 执行隔离

当前生产基线禁用所有脚本执行。它支持检索问答和已有文档解析，不提供 Agent 执行 Python、Shell、安装 Skill 或生成 Word/PPT 的承诺。初期邀请制不改变这一边界。仅在专用运行时、隔离验收和 Office 验收都完成后，才为指定测试工作空间开启受控沙箱。

主部署流程见 [05-deploy-production.md](05-deploy-production.md)。2026-10-09，本机 staging 的 `app-baseline` 6 项检查已通过；这只证明当时 app 容器的受检表面，不代表远端沙箱、云网络或 Office 已通过。生产 Caddy 另行阻断会话沙箱、Skills 与命名后端写接口，保留工作空间禁用策略接口；app 不发布宿主端口。入口限制不能代替后端策略与运行时隔离。

## 上线门槛

1. 应用不挂载 Docker/containerd socket，不共享宿主机 PID、IPC 或 host 网络，不挂载运行时客户端证书，也不配置远端 Docker 环境变量。Docker TCP 2375/2376 不向应用开放。
2. 部署设置 `WEKNORA_SANDBOX_DOCKER_ENABLED=false`，系统设置有效值 `sandbox.docker_enabled=false`。数据库值优先于环境变量，不能仅检查 Compose。
3. 对每个工作空间，以该空间管理员身份调用 `PUT /api/v1/sandbox-configs/workspace-policy`，请求体为 `{"scripts_disabled":true}`。随后 `GET /api/v1/sandbox-configs` 必须返回 `success=true`、顶层 `workspace_scripts_disabled=true`、`data=[]`。该只读响应不会列出内部策略行。
4. 新建工作空间必须先完成 provision apply 的同一策略与回读检查，才交付邀请用户。不能把一个空间的通过结果代表所有空间；不能为普通成员提供后端管理或系统管理员身份。
5. 迁移已有环境时，核对命名配置、Skill 安装任务、会话 sandbox pin 和远端存活容器。切换开关不代表已经终止旧容器。停止或删除已有资源另按运维审批执行。
6. 不用伪造的 `.docx`/`.pptx`、改扩展名、下载空文件或只有自然语言答案来证明 Office 生成可用。

空间策略在 `internal/application/service/tenant_sandbox_resolve.go` 中读取失败时返回 `false`。它目前不是独立的 fail-closed 总开关；因此第 1、2、3 项必须同时成立。该策略也不能代替对所有管理接口、安装路径及既有执行任务的逐项阻断。管理员不能在生产基线里新增 Cube/E2B 等命名后端。

`scripts/acceptance-prod.py` 已为 bootstrap 与 A/B 验收空间设置并回读禁用策略，检查命名配置为空，并设置/读取实际数据库 Docker 开关。其创建流程先设置空间策略，再邀请用户或写入内容；该脚本的 fixture 通过仍不能代表后来新建的其他空间。

`scripts/check-sandbox-isolation.py` 的 `app-baseline` 仅检查容器挂载、namespace 和环境回退，不读取数据库、不登录 API，不证明以上全部门槛成立：

```bash
python scripts/check-sandbox-isolation.py --context production-app app-baseline --container boyankb-app
```

`production-app` 必须是运维已创建且指向目标部署的 Docker context。输出只含检查名与布尔值，不输出环境内容、挂载路径或凭据。返回码 `0` 表示所选检查范围通过，`1` 表示有失败项，`2` 表示无法验证。

## 代码已具备的能力与限制

| 位置 | 事实 | 生产含义 |
| --- | --- | --- |
| `internal/container/sandbox.go` | 全局 manager 是 disabled；服务端没有 Lite host 回退 | 没有命名后端时不执行宿主机脚本 |
| `internal/sandbox/docker_enabled.go` | Docker 开关为 DB > env > false | 修改 `.env` 不能覆盖已开启的数据库设置 |
| `internal/router/routes_infra.go` | 后端创建/修改、Skill 安装需要 Admin；完整 API Key 可进入该 API 组 | 工作空间管理员和完整 API Key 都是敏感权限 |
| `internal/types/tenant.go`、`internal/sandbox/tenant_config.go` | 后端支持独立 daemon、TLS 证书目录、CPU、内存、PID、runtime、network mode | 可以连接专用 VM，不需要应用主机 socket |
| `internal/sandbox/docker_remote_client.go` | `Memory`、`MemorySwap=Memory`、`NanoCPUs`、`PidsLimit`、`CapDrop=ALL`、`no-new-privileges`、init | 有每容器资源约束，不等于租户/集群总配额 |
| 同上及 `docker_engine.go` | 默认 PID 1 和执行用户为 root；加回 CHOWN、DAC_OVERRIDE、FOWNER、FSETID、SETGID、SETUID、KILL | rootfs 可写，目录约定不能作为文件访问隔离；不能挂业务目录或凭据 |
| `internal/types/sandbox_network_policy.go` | Docker 只支持整体断网；拒绝 IP/域名细粒度列表；策略缺省允许出站 | 生产受控配置必须显式 `network_mode=none`，不能使用默认 bridge |
| `internal/sandbox/docker_remote_client.go` | 每条命令使用容器内 GNU `timeout -s KILL` | 不能防御同 UID/root 脚本篡改命令、超时工具或其他进程；不是宿主机硬期限 |
| `internal/agent/tools/shell_exec.go` | Shell 请求超时上限为 600 秒 | 普通调用有上限，但不等于整个沙箱生命期或不可绕过的资源监管 |
| `internal/sandbox/docker_idle_sweeper.go` | 根据可写活跃标记回收空闲容器 | 没有不可续期硬寿命；应用停机也不能依赖其清扫线程 |
| `internal/sandbox/docker_snapshot.go` | Skill 安装通过 commit 保存文件系统快照 | 不支持只读 rootfs 的直接替换，镜像层和磁盘仍需外部配额/清理 |

CPU、内存和 swap 约束依赖实际宿主内核/cgroup 支持，不能只看配置文件。Docker 官方说明资源限制和 `memory-swap` 的语义见[资源限制](https://docs.docker.com/engine/containers/resource_constraints/)。

## SSRF 校验与网络隔离

`internal/sandbox/url_guard.go` 在配置保存和实际拨号时校验 app 访问的控制面/数据面地址，处理 DNS 重绑定和 metadata 地址。`allow_private_endpoints=true` 仅允许应用连接指定私网后端，不会限制沙箱内 Python、curl、DNS、原始 socket 或安装脚本的网络。

因此 SSRF 校验不等于网络隔离。Docker bridge 默认可出站，Compose 的业务网络、宿主机网关、数据库私网和 metadata 必须由运行时网络边界处理。不能把 IPv4 的 `0.0.0.0/0` 拒绝规则说成同时覆盖 IPv6；Cube/E2B 的网络能力必须在对应提供商单独实测，不能从本地字段推断通过。

离线 Docker 使用 `none` 只保留容器自身 loopback。`127.0.0.1` 在该独立 namespace 中不是宿主机；没有外部网卡就不应存在内网、metadata、host gateway 或 IPv6 外部路由。Docker 官方说明见[none 网络](https://docs.docker.com/engine/network/drivers/none/)。

Agent 的网页抓取、MCP、浏览器连接、模型请求可能由 app 或其他服务发出，不经过沙箱网络。离线沙箱不代表整个 Agent 离线；需关闭不需要的工具，并由生产应用出站策略限制这些独立流量。

## 专用 VM 上的受控离线模式

此模式目前未验证：当前没有已授权的远端 VM、runsc 实例和 Office 沙箱镜像。配置模板位于 `deployment/sandbox/offline-config.example.json`，其中示例域名和全零镜像摘要必须替换，不能原样启用。

连接关系为 `app → mTLS:2376 → 专用 Docker daemon → runsc 沙箱`。应用只持有这台专用运行时的客户端证书；不持有业务主机 Docker socket。mTLS 凭据仍等同该 daemon 的控制权，不是低权限代理；独立 VM 必须不含业务数据库、云管理员凭据或共享业务磁盘。Docker 官方说明见[保护 daemon socket](https://docs.docker.com/engine/security/protect-access/)。

启用步骤：

1. 准备独立 Linux VM，安装 Docker Engine 与已固定版本的 gVisor `runsc`。配置 daemon 的 `runsc` runtime，先以官方例子验证能创建和运行；配置方法见[gVisor Docker 接入](https://gvisor.dev/docs/user_guide/quick_start/docker/)。gVisor 增加用户态内核隔离，不代表没有漏洞或无须网络/资源约束；边界见[gVisor 安全模型](https://gvisor.dev/docs/architecture_guide/intro/)。
2. VM 网络或上级防火墙仅允许 app 地址连接私网 2376，使用双向 TLS；禁止明文 2375、公网 daemon、未经授权的 SSH、连接业务网段和 metadata。IPv4/IPv6 均默认拒绝。只在受控构建/镜像导入窗口放行固定镜像仓库；测试运行期间关闭运行时 VM 出站。不要给 VM 附加云实例角色或 metadata 凭据。
3. 按 Docker 官方文档签发独立服务端/客户端证书，SAN 必须匹配配置地址。证书目录只读挂给 app，容器路径固定 `/run/sandbox-docker-tls`，仅包含 `ca.pem`、`cert.pem`、`key.pem`。CA 私钥不进入 app、数据库或仓库。示例 Compose 接口为只读绑定该目录；主部署文件另行配置。
4. 创建一个仅运维可用的测试工作空间。将审核后的依赖、字体、模板和 Skill 烘焙到固定 digest 镜像，导入专用 daemon；禁止运行时临时联网安装。标准 `docker/Dockerfile.sandbox` 仅提供通用 Python/Node/CLI，并未预装 Word/PPT 库。
5. 仅此测试空间保存模板配置：Docker 网络 `none`、runtime `runsc`、1 CPU、1024 MiB、128 PID、空 env、空共享卷、关闭 desktop；允许私网控制面。镜像必须与审核摘要一致。所有生产空间继续保持禁用。先核对系统开关与测试空间策略，再由运维批准该测试空间开启。
6. 从该空间实际创建一个独立测试会话。确认应用生成的真实容器标签、image ID 和 HostConfig；不是另起一个参数不同的 `docker run` 来冒充应用通过。
7. 执行以下探针与应用的完整沙箱验证，保留结果。完成后人工检查文档并关闭测试配置；运行中容器清理按审批处理。

`deployment/sandbox/daemon.example.json` 是独立 VM 的 daemon 配置模板。必须把文档示例地址 `192.0.2.10` 替换为该 VM 的私网监听地址，并放入对应证书。它默认使用 `runsc`、只监听 mTLS TCP、限制容器日志大小，不开启 UNIX socket；这些选项不等于 daemon 请求体准入或每租户配额。镜像导入和运行时管理需要另建运维 mTLS 身份。

在新 VM 上先验证最终配置，再由运维按其发行版服务管理方式安装，不能覆盖业务主机的 daemon。若发行版 service 已传 `-H fd://`，它与配置内 `hosts` 冲突，必须在这个专用 VM 的 service override 中消除重复参数后再启用：

```bash
dockerd --validate --config-file /etc/docker/daemon.json
```

校验成功仅证明参数有效，不证明证书可用、运行时可启动或防火墙通过。Docker 的配置与重复参数规则见[daemon CLI](https://docs.docker.com/reference/cli/dockerd/)。专用 VM 的启动、防火墙和证书配置尚未在实际目标机器验证。

模板中的 `default_timeout_sec=120` 不是运行时硬寿命。正式向普通用户开放前，必须增加 daemon 侧准入限制、外部硬寿命监管、并发总额和磁盘配额；没有这些时仅允许受控验收，不达到不可信多租户脚本生产开放标准。daemon API 的简单路径白名单无法检查 `HostConfig`，不能阻止另建 privileged/host-network/挂载容器。不能以 socket proxy 名称替代请求体校验。

## 离线探针

先由运维创建显式命名的远端 Docker context。TCP context 必须包含 TLS 材料并启用验证；SSH context 必须使用已有的受控 SSH 身份。探针不创建容器、不拉镜像、不安装包、不更改配置，不读取 `.env`。

仅检查已存在容器：

```bash
python scripts/check-sandbox-isolation.py --context production-sandbox-test offline-sandbox --container FULL_64_HEX_CONTAINER_ID --tenant-id TEST_TENANT_ID --config-id TEST_CONFIG_UUID --image-id sha256:APPROVED_IMAGE_ID
```

在明确授权的同一测试容器中运行网络探针：

```bash
python scripts/check-sandbox-isolation.py --context production-sandbox-test offline-sandbox --container FULL_64_HEX_CONTAINER_ID --tenant-id TEST_TENANT_ID --config-id TEST_CONFIG_UUID --image-id sha256:APPROVED_IMAGE_ID --probe-authorized-test-container
```

该参数会通过 `docker exec` 启动一个有外层超时的短期 Python 进程，只读取接口和路由，并建立 loopback 正向对照及外部目标 TCP 连接尝试。不发送 HTTP、DNS、metadata 请求正文，不读取业务文件正文或容器内环境内容；只输出结果布尔值。容器 inspect 阶段在检查进程内核对 managed/workspace/config 标签、`runsc`、`none`、无挂载/额外设备/host gateway/端口发布、CPU/内存/PID、swap、capabilities、init 和代理变量，不回显 inspect 原始数据。

只有创建参数检查全部通过才执行连接探针。连接被拒绝或普通超时不能单独证明隔离，因此探针要求不可达/权限拒绝等错误，并同时要求只存在 loopback、没有外部 IPv4/IPv6 路由。宿主 Docker context 名称不能证明 VM 真正独立；网络拓扑和防火墙仍需运维核查。容器内 root 可伪造 `/proc` 读取和 Python 输出，探针必须在干净可信测试镜像运行，不能把恶意脚本运行后的自报作为信任依据。

结果仅证明检查时的容器配置和网络，不证明未来硬超时、内核无漏洞、总量配额、其他 Agent 工具流量或 Office 端到端可用。真实远端执行结果当前为未验证。

本地模拟验证命令：

```bash
python -B -m unittest discover -s deployment/sandbox -p test_isolation.py
```

18 项通过，覆盖网络/runtime/namespace/资源越界、socket/证书挂载、额外权限、身份或镜像不符、未授权不执行、前置失败不执行、拒绝本机及无 TLS context、错误输出脱敏，以及模板与脚本语法。测试使用合成 inspect 数据和 mock Docker，不证明远端隔离已通过。

## Word/PPT 验收

禁用沙箱基线不执行生成脚本。现有 docreader 是解析依赖，不能拿来执行 Agent 任意代码。本机只读镜像清单没有可复用的 sandbox/Office 镜像，仓库也没有 `skills/preloaded` 目录。

离线沙箱可以运行预安装的 `python-docx` 和 `python-pptx`，分别生成真实 OOXML 文件；能力见各项目官方文档：[python-docx](https://python-docx.readthedocs.io/en/latest/)、[python-pptx](https://python-pptx.readthedocs.io/en/latest/)。构建阶段固定依赖版本和包哈希，准备中文字体及本地素材，运行阶段保持断网。运行时镜像、依赖锁和 Skill 尚未交付，不能报告已完成生成验收。

`deployment/sandbox/office-smoke.py` 是待执行的库级验收脚本。它只在 `/workspace/output` 下新建独立目录，生成包含中文与唯一标记的 Word 和两页 PPT，再用同一库重新打开并检查 ZIP 完整性。把它放入审核后的镜像，或上传到授权测试会话后执行；它不自动安装依赖、不访问网络。其 PASS 只表示库和文件格式通过。

完整产品验收还必须：

1. 用户在测试 Agent 对话中明确请求一份中文 Word 和两页 PPT，实际工具执行成功，文件位于 `/workspace/output`。
2. 应用产物收集器返回可下载附件；通过应用鉴权下载后的字节必须能重新解析，文档包含指定文本、PPT 恰好两页，扩展名与实际 OOXML 一致。
3. 用 Word/PowerPoint 或受控 LibreOffice 打开/渲染，人工核对中文字体、分页、表格、文字裁切与可编辑性。格式探针不证明版式通过。
4. 另一用户/空间不能下载该会话产物；删除/失效链接按产品权限验证。整个验收中禁止外部图片链接、联网模板和凭据写入产物。

以上真实生成、产物下载、版式和跨空间拒绝均为未验证，需待受控运行时获批并可用后执行。

## 后续最小补丁

| 补丁位置 | 所需行为 | 验证 |
| --- | --- | --- |
| `internal/application/service/tenant_sandbox_resolve.go` | 策略查询失败时返回错误或 disabled，不以 false 继续 | 模拟策略仓库失败，所有解析路径拒绝执行 |
| `internal/container/sandbox.go`、`internal/sandbox/tenant_resolver.go`、sandbox 管理/check/Skill 入口 | 不可被租户或 DB 覆盖的部署强制禁用开关；覆盖创建、重连、探针和安装 | 配置 DB 开启、命名 Cube/E2B/Docker 后仍统一拒绝；已有连接有明确终止策略 |
| `internal/sandbox/docker_remote_client.go` 及配置验证 | 运维固定 daemon/image/runtime/none 与资源上限，不允许工作空间管理员放宽；执行账号与安装账号分离 | 请求 privileged/bridge/越界资源/未批准镜像时 fail-closed |
| 专用 daemon 的准入层/运行时控制器 | 校验请求体；每租户及全局并发/CPU/内存/存储上限；容器外不可续期硬寿命；故障时拒绝新建 | 达限拒绝、恶意后台进程和更改活跃标记仍按硬期限终止、监管重启后续管 |

这些补丁尚未实施。当前交付仅新增配置模板、只读/显式授权探针与本文件，不改变业务核心或真实运行环境。
