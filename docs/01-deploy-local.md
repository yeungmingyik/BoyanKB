# 本机 Docker 部署

本方案从 `D:\BoyanKB\WeKnora` 启动 WeKnora，使用仓库主 Compose 文件和本机覆盖文件。前端及 API 仅绑定本机回环地址；数据库、Redis、docreader、Ollama 通过 Compose 网络通信。

本文记录基础栈首次部署。后续飞书接入增加了应用日志脱敏和指定空间保护，本地 app 已改为从源码构建的镜像；该分支重建时先按 [飞书部署步骤](02-feishu.md) 构建 app，不再对 app 执行下文的发布镜像拉取命令。其他五个服务仍使用本文镜像，DeepSeek 冒烟脚本的使用方式不变。

## 1. 部署文件与服务

| 文件 | 用途 |
| --- | --- |
| `docker-compose.yml` | 上游服务定义 |
| `docker-compose.local.yml` | 本机端口、Ollama、Redis 持久化和健康检查、内置模型挂载 |
| `config/builtin_models.local.yaml` | DeepSeek 问答模型与本地 Embedding 模型声明；凭据通过环境变量展开 |
| `.env` | 本机参数、应用密钥、管理员初始凭据；不提交 Git |
| `scripts/smoke-test.sh` | 创建验收数据并验证解析、检索和真实模型问答 |

| 服务 | 镜像/作用 | 访问方式 |
| --- | --- | --- |
| `frontend` | `wechatopenai/weknora-ui:v0.8.2` | `http://127.0.0.1:8088` |
| `app` | `wechatopenai/weknora-app:v0.8.2` | `http://127.0.0.1:8080`；健康检查 `/health` |
| `docreader` | `wechatopenai/weknora-docreader:v0.8.2` | 容器内 `docreader:50051` |
| `postgres` | `paradedb/paradedb:v0.22.6-pg17`；业务数据和向量检索 | 容器内 `postgres:5432` |
| `redis` | `redis:7.0-alpine`；任务队列等 | 容器内 `redis:6379` |
| `ollama` | `ollama/ollama:0.18.2`；本地 Embedding | 容器内 `ollama:11434` |

本方案拉取已发布镜像，不构建当前工作区源码。已核对 app 镜像 revision 与当前仓库提交一致，并完成模型实际调用；后续修改 Go、前端或 docreader 源文件不会自动进入这些镜像。

命名卷保留 PostgreSQL 数据、上传文件、docreader 临时文件、Redis 数据及 Ollama 模型。日常启停不删除卷。

## 2. 本机参数

命令使用 PowerShell 7+。本机已具备 Docker Desktop、Docker Compose、Git Bash 和 Python 3；脚本不要求安装额外 Python 包。

本机 `.env` 已从 `.env.example` 的有效赋值生成，再覆盖本地端口、固定镜像版本和模型配置。数据库、Redis、JWT、AES、签名及管理员密码均重新随机生成，示例中的 Langfuse 凭据已清空并禁用该集成。DeepSeek Key 最初写为 `REPLACE_WITH_DEEPSEEK_API_KEY`，随后由用户在 `.env` 的 `DEEPSEEK_API_KEY` 字段自行填写。

用编辑器维护该文件，不覆盖已有随机密钥。以下是相关字段及含义，不包含真实秘密：

| 字段 | 本机配置 |
| --- | --- |
| `WEKNORA_VERSION` | `v0.8.2` |
| `FRONTEND_PORT` / `APP_PORT` | `8088` / `8080` |
| `DB_DRIVER` / `RETRIEVE_DRIVER` | `postgres` / `postgres` |
| `STORAGE_TYPE` | `local` |
| `OLLAMA_BASE_URL` | `http://ollama:11434` |
| `SSRF_WHITELIST_EXTRA` | `ollama,api.deepseek.com`；仅放行两个精确主机名 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL_NAME` | `deepseek-flash` |
| `DEEPSEEK_API_KEY` | 用户提供的有效 API Key |
| `EMBEDDING_MODEL_NAME` | `bge-m3:latest` |
| `AUTO_MIGRATE` | `true` |
| `SMOKE_ADMIN_EMAIL` | `admin@weknora.local` |
| `SMOKE_ADMIN_PASSWORD` | 已在 `.env` 生成的 32 字符随机密码，包含字母和数字 |
| `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL` | 与 `SMOKE_ADMIN_EMAIL` 一致 |
| `SYSTEM_AES_KEY` | 已生成的 32 字节 ASCII 密钥 |
| `COMPOSE_FILE` | `docker-compose.yml;docker-compose.local.yml` |
| `COMPOSE_PATH_SEPARATOR` | `;` |

截至 2026-10-08，DeepSeek 官方列出的模型名是 `deepseek-flash`，OpenAI 格式地址为 `https://api.deepseek.com`；计费及模型变更以[官方模型和价格页](https://api-docs.deepseek.com/quick_start/pricing/)为准。

Embedding 使用 BGE-M3，模型定义中的向量维数为 1024。模型需单独下载到 Ollama 卷；[Ollama 模型页](https://ollama.com/library/bge-m3)显示 `bge-m3:latest` 约 1.2 GB。模型下载成功和服务健康是两项不同检查。

## 3. 拉取、启动与检查

在 PowerShell 7 中运行：

```powershell
Set-Location 'D:\BoyanKB\WeKnora'
docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.local.yml pull frontend app docreader postgres redis ollama
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build --wait --wait-timeout 300 frontend app docreader postgres redis ollama
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T ollama ollama pull bge-m3:latest
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T ollama ollama list
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
Invoke-RestMethod 'http://127.0.0.1:8080/health'
```

下载模型期间保持 Docker Desktop 运行。首次模型加载和文档解析可能比后续调用慢；本机默认使用 CPU，没有在覆盖文件中申请 GPU。

浏览器打开 [WeKnora 本机前端](http://127.0.0.1:8088)。初始管理员密码在本机 `.env` 的 `SMOKE_ADMIN_PASSWORD` 字段，本文和脚本输出不展示密码。

命令显式传入两份 Compose 文件，便于复现。因为 `.env` 同时配置了 `COMPOSE_FILE` 与分隔符，在仓库根目录也可使用 `docker compose ps` 等短命令。若从其他目录执行，应先切回仓库根目录。

## 4. 管理员引导与冒烟验收

`WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL` 只提升已存在的账号，不创建账号。首次引导需先完成注册，再启动一次 app；只有系统尚无系统管理员时才会提升该邮箱用户。对应实现是 `cmd/server/bootstrap.go`。

初次创建管理员时，公开注册需要临时允许，并保持仅本机访问。创建和提权完成后可在 `.env` 设置 `DISABLE_REGISTRATION=true`，再重建 app 容器，使后续账户通过管理员或邀请流程加入。

### 4.1 完整验收

填写有效 `DEEPSEEK_API_KEY` 后执行：

```powershell
& 'D:/Git/bin/bash.exe' scripts/smoke-test.sh
if ($LASTEXITCODE -ne 0) { throw '本机完整冒烟验收未通过' }
```

脚本负责加载本机配置并刷新 app 环境，准备管理员、知识库和测试文档，再检查解析完成、向量/关键词/混合检索命中文档、真实 DeepSeek 回答以及引用证据。它会创建测试数据，并可能为管理员引导重启 app。

验收文档来自 `testdata/wiki_test/doc1_stardust_memo.md`；知识库和会话名为 `local-smoke-stardust`，智能体名为 `local-smoke-rag`，验收标记为 `weknora-local-smoke-v1`。脚本重复运行会优先复用同名验收对象，不自动清除这些数据。

只有完整模式返回 0 才能记录完整验收通过。缺少有效 API Key、DeepSeek 调用失败、检索或引用检查失败，均不能视为通过。一般错误返回 1，用户中断返回 130。真实问答会向 DeepSeek 发送问题及检索内容，并按账户规则计费。

### 4.2 仅准备与检索

```powershell
& 'D:/Git/bin/bash.exe' scripts/smoke-test.sh --prepare-only
if ($LASTEXITCODE -ne 0) { throw '本机准备与检索检查未通过' }
```

该模式用于先准备管理员、知识库和文档，检查解析及真实检索。它不证明 DeepSeek 问答或引用通过，不替代完整验收。

### 4.3 验收记录

验收日期：2026-10-08。执行 `& 'D:/Git/bin/bash.exe' scripts/smoke-test.sh` 返回 0；再次执行复用已有账号、知识库、文档和会话，仍返回 0。

| 项目 | 验收依据 | 本次结果 |
| --- | --- | --- |
| Compose 配置 | `config --quiet` 返回 0 | 已通过 |
| 六个服务 | `compose ps` 与健康检查 | 已通过；完整冒烟后复查 app、frontend、docreader、postgres、redis、ollama 全部 running + healthy |
| 前端/API | 前端可访问、`/health` 成功 | HTTP 200；`/health` 返回 `status=ok` |
| Embedding 模型 | `ollama list`、实际向量化 | 已通过；`bge-m3:latest`，ID `790764642607`，1.2 GB，向量化成功 |
| 管理员 | 注册/登录、系统管理员权限 | 已通过；管理员可重复登录，系统管理员权限检查成功 |
| 解析与检索 | 测试文档完成解析并被检索命中 | 已通过；空间 `10000`，向量/关键词/混合检索各命中 1 条测试文档内容 |
| 真实问答及引用 | 完整脚本返回 0 | 已通过；DeepSeek 返回真实回答，回答中的引用与 SSE 中测试文档来源一致 |

真实回答识别出艾琳·科尔博士（Dr. Elara Cole）、幽能引擎（Psionic Engine）和“2150 年前”三项文档事实，引用 chunk 与 SSE 中已上传文档的来源匹配。

已确认发布 app 镜像的 `org.opencontainers.image.revision` 为 `3e8b0bfc80b845b2d4b2ed683994748741450a97`，与本次部署核对时的仓库提交一致。DeepSeek `/models` 认证、完整问答验收均已通过；脚本 Bash/Python 语法检查及缺少 Key 时非零退出的分支也已通过。

最终数据库迁移版本为 `110`，`dirty=false`；运行复查未发现错误。未配置 Rerank 时的跳过提示及 merge fallback 提示符合本方案配置，未影响检索、回答和引用验收。

## 5. 常见问题

### app 日志只有一行行 `json`

本机运行环境已有 `LOG_FORMAT=json`，Compose 插值优先使用宿主机环境变量。WeKnora 把该值视为日志模板字面量，因此日志正文被隐藏。本地覆盖已显式设置 `LOG_FORMAT: "%d %level [%traceId] %msg"`，无需修改上游日志代码；重新创建 app 后生效。

### docreader 镜像拉取长时间无进度

本次下载出现该情况，保留 Docker 下载缓存并重新执行 `docker pull wechatopenai/weknora-docreader:v0.8.2` 后完成。下载完成的摘要为 `sha256:3c36e7e738ba515315523876d8041f9bb02809145a4e3b331e2550e4bff98b7a`。无需删除镜像缓存或数据卷。

### 修改 `.env` 后配置没有生效

`docker compose restart` 会重启旧容器，不重新注入修改后的环境变量。修改 DeepSeek Key、模型名或注册开关后运行：

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build --force-recreate app
```

随后等待 `/health` 恢复，再重试验收。`SMOKE_ADMIN_PASSWORD` 是脚本登录凭据；账号创建后，单独改该字段不会自动修改数据库里的账号密码。

### 模型列表缺少两个内置模型

确认覆盖文件已加载、`config/builtin_models.local.yaml` 存在、`AUTO_MIGRATE=true`。当前源码在自动迁移分支中调用内置模型 YAML 加载器，设为 `false` 会跳过该加载步骤，见 `internal/container/container.go:887`、`:917`。

若只修改了挂载的 YAML，应用需要重启才会重新读取；若同时修改 `.env`，应按上一节重建 app。内置模型是跨空间使用的系统级定义，新建空间不需要手工重新录入这两个模型。本次两个模型已通过配置加载及实际向量化、问答调用。

### AES 密钥长度错误

代码读取 `SYSTEM_AES_KEY` 的原始字节，必须恰好 32 字节，见 `internal/utils/crypto.go:19`。使用 32 个 ASCII 字符最直接；64 个十六进制字符虽然可能表达 32 字节随机数，但这里不会自动解码，实际会被当作 64 字节而失效。已有加密数据后不要随意换密钥，否则原凭据无法解密。

### 首次管理员没有成为系统管理员

确认账号邮箱与 `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL` 一致，且完成注册后 app 已再次启动。若已有系统管理员，引导变量不会再提升其他人；应由已有系统管理员处理。关闭注册后首次账号无法创建时，先在仅本机暴露的环境中完成引导，再关闭公开注册。

### 注册提示密码不符合要求

默认密码规则为 8–32 个字符，至少包含一个字母和一个数字；开启复杂密码策略时还需满足大小写及特殊字符要求。本次初始 36 字符随机密码被注册 API 拒绝，已改为满足规则的 32 字符随机密码后完成注册。对应校验位于 `internal/application/service/user.go` 的 `ValidatePasswordPolicy`。

### Ollama 模型或向量化失败

`ollama` 健康只说明服务可响应，不代表 BGE-M3 已下载。检查 `ollama list`，缺少时重新执行模型拉取命令。app 使用 `http://ollama:11434`，不能填写 `http://127.0.0.1:11434` 指向 app 自己。模型维数和知识库向量配置必须一致；本方案均按 1024 维配置。

本次 BGE-M3 下载曾停滞，重启 Ollama 服务后重新拉取，保留已有模型下载缓存并完成下载：

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml restart ollama
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T ollama ollama pull bge-m3:latest
```

### DeepSeek 返回认证、余额或网络错误

核对 `.env` 中 Key 是否有效、账户可用余额、模型名与 API 地址；修改后重建 app。Docker 拉取镜像、Ollama 下载模型及 app 访问 DeepSeek 是三条网络链路，其中一条成功不能证明另外两条可用。不要把完整 `.env`、带认证头的请求或未脱敏日志贴入工单。

### DeepSeek 请求被 SSRF 检查拒绝

本次本机 DNS 将 `api.deepseek.com` 解析为 `198.18.0.70`，属于受限制的 `198.18.0.0/15` 地址段，第一次真实模型调用因此被拒绝。已在 `.env` 将 `SSRF_WHITELIST_EXTRA` 设置为 `ollama,api.deepseek.com`，重新创建 app 后完整问答通过。该设置仅追加两个精确主机名，保留 SSRF 检查，不放行整个地址段；其他主机无需照搬与其 DNS 环境无关的例外。

### 端口占用或容器启动失败

本机前端和 API 默认使用 8088、8080。如有冲突，在 `.env` 调整对应端口并重新 `up`。改变 API 端口时，脚本还需设置 `SMOKE_BASE_URL` 为新的本机 API 地址。查看 `docker compose ps` 定位未健康的服务；在本机按需查看该服务日志，先处理 PostgreSQL、Redis、docreader 或 Ollama 的失败，再重试 app。

### 如何暂停或重新启动

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml stop
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build
```

不要用 `docker compose down -v` 排查普通启动问题，它会删除本方案的命名卷。删除数据库、模型卷、上传文件卷或其他持久化数据前，按项目要求另行确认。
