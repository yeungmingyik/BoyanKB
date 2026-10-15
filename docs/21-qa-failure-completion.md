# 快速问答异常结束处理

日期：2026-10-15。分支：`feat/qa-failure-completion`。

本次处理快速问答在模型服务故障、超时或流异常关闭后仍显示“思考中／正在准备回答”的问题。供应商恢复情况与系统自身的失败处理分别验证，不能将容器健康等同于模型服务可用。

## 行为

- 快速问答整轮（包括改写、检索及生成）总期限为 120 秒。复杂智能体工作流的原有超时不变。
- 同一个主模型在改写阶段返回 HTTP 429 或 5xx 时立即结束，不继续请求这个故障模型。独立的可选改写模型仍可降级。
- 流式模型错误、没有完成标记的 EOF、模型兜底回答失败均向统一结束处理返回；正常完成后取消模型子 context，释放连接。
- 页面明确提示超时、服务繁忙／不可用、连接中断或请求失败。只展示固定文案，不展示供应商原始响应、内部地址或凭据。
- 保留已生成正文和思考；失败原因写入既有 `messages.agent_steps` JSON，`is_completed=true`，刷新仍显示失败。没有新增表、列或数据库迁移。
- 失败或用户停止的部分回答不会写入会话知识库、生成追问建议或提取记忆。用户停止不会被标记为模型故障。
- 新连接和刷新重连都识别终止错误，结束 SSE。失败不触发旧沙箱的文件收集。即使消息保存失败，也尽力发送结束提示；数据库不可用时不能保证刷新后的持久化。

## 验证

本机模拟故障，避免依赖真实供应商事故或在正式资料中插入测试文本。

```bash
go test -ldflags='-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn' \
  ./internal/application/service/chat_pipeline ./internal/application/service ./internal/handler/session \
  -count=1 -timeout=6m
cd frontend
npm run type-check
npm test -- src/composables/useChatStreamHandler.integration.test.ts \
  src/views/chat/components/RagPipelineProgress.test.ts src/i18n/embedLocale.test.ts
cd ..
python -m unittest discover -s scripts -p 'test_tencent_update_frontend.py'
```

关键回归包括：HTTP 503／429、超时且通道未关闭、输出部分正文后 EOF、空 EOF、正常 Done 后通道不关闭、重复 Done、thinking 与正文解码尾部、模型兜底、取消、持久化后重载及 SSE 终止。新测试已在旧流实现和旧前端上复现失败。

已执行结果：

| 检查 | 结果 |
| --- | --- |
| `chat_pipeline` 整包，含步骤错误脱敏 | 通过，7.215 秒 |
| `handler/session` 整包，含实时与重连 SSE 结束、保存失败、120 秒范围 | 通过，4.003 秒 |
| `application/service` 整包 | 通过，295.695 秒 |
| 前端相关回归 | 106 项通过；旧实现的 24 项集成测试中 13 项失败，修复后全部通过 |
| TypeScript 类型检查 | 通过 |
| 发布脚本 Python 单元测试 | 33 项通过，另在隔离 Linux/Python 3.12 中复核通过 |
| Linux 前端正式镜像 | 构建通过，附带 i18n 审计、既有 38 项检查和 favicon 对比通过 |

完整 service 首轮有 40 个环境相关失败：本机代理将 `example.com`、`api.e2b.app` 解析到 `198.18.0.0/15`，被 SSRF 正确阻止。只在一次性测试容器使用 DoH 查询的真实公网地址覆盖这两个名字后整包通过；未关闭 SSRF，也未修改宿主或云端 DNS。Windows 前端本地构建在原基线和本次修改上都存在共享外置 LESS 的 Vite 缓存问题，Linux 正式构建通过，未为此修改无关样式。

详细日志留在本机忽略目录 `temp/qa-failure-completion/`，不包含真实凭据。源码提交为 `19262e15c` 和 `77516e949`；前端构建标记为 `qa-failure-20261015`，最终后端标记为 `77516e949`。

## 发布与回滚

本次使用锁定基础镜像的 `docker/Dockerfile.app-boyankb` 和 `docker/Dockerfile.frontend-boyankb`；保留 `GO_BUILD_TAGS=anydoc`、品牌资源与 LICENSE。镜像使用单一 `linux/amd64` 平台及固定摘要。

构建命令（对应源码 `77516e949`，其前端与 `19262e15c` 相同）：

```bash
docker build --platform linux/amd64 --provenance=false --sbom=false \
  --build-arg BRANDING_COMMIT=77516e949 -f docker/Dockerfile.app-boyankb \
  -t boyankb/app:v0.8.2-qa-failure-v1 .
docker build --platform linux/amd64 --provenance=false --sbom=false \
  --build-arg VITE_FRONTEND_COMMIT=qa-failure-20261015 -f docker/Dockerfile.frontend-boyankb \
  -t boyankb/frontend:v0.8.2-qa-failure-v1 .
```

本次产物固定引用：

```text
boyankb/app:v0.8.2-qa-failure-v1@sha256:07eaa5f40986159341486cc780ab0de02c3cf471e5d01bcb45ecf9eda3567f21
boyankb/frontend:v0.8.2-qa-failure-v1@sha256:20aab77f8b769ee7e99f18fd81166be3026d6002a055af3944b7637c7cc31607
```

`scripts/tencent-update-frontend.py` 保持原前端命令兼容，新增可选 `--app-image`，用于同一次发布中更新两个服务。默认只预览，`--apply` 执行；脚本验证部署标记、镜像摘要、九服务清单，备份 Compose 与镜像锁，核对环境、端口及其余七个容器不变。启动或校验失败自动恢复旧配置和两份旧镜像。环境文件、数据卷、飞书同步计划均不更改。

发布完成后记录新镜像摘要、九容器健康、HTTPS 登录页和 API 认证边界，确认没有意外重启其余服务。旧镜像保留可供回滚。本次任务用户已明确授权测试通过后更新云端。

需要回退时，在 `/opt/boyankb` 先预览以下命令，核对只修改两个镜像；执行时加 `--apply`。它会为回退操作也创建备份，并保留当前业务数据：

```bash
python3 scripts/tencent-update-frontend.py \
  --image boyankb/frontend:v0.8.2-skills-unified-v2@sha256:2cc82fd44eca6e421ad5a20a0803f1c89133d386a025f913d91cd61177764bf6 \
  --app-image boyankb/app:v0.8.2-trial-graph-v3@sha256:ac806ca72aa54f18c73b5a4140cb18c75aa0f3a41fe1a1cd2cb5b5f7da04d756
```

人工复核：在快速问答中重新发送问题；出现模型故障时应看到明确红色提示，刷新仍保留，输入框可继续发送。模型服务正常时确认回答完整且有引用。这项真实供应商端到端结果以发布记录为准，不以模拟测试替代。

## 云端执行记录

2026-10-15 已在腾讯云执行配套镜像更新。预览和应用均只修改 `services.app.image`、`services.frontend.image`。发布脚本返回 `VERIFIED`，九服务健康，环境文件摘要不变；HTTPS 登录页为 200、匿名知识库和技能目录为 401、公开注册为 403、内部管理接口为 404。本机另通过公网 IP 核验登录页 200，TLS 校验结果为 0（未跳过证书验证）。登录后 `/api/v1/system/info` 返回后端提交 `77516e949`。

配置备份与发布报告位于 `/opt/boyankb/backups/frontend-20261015T042256Z-72fc753b/`，报告文件为 `report.json`，旧镜像继续保留。私有传输归档大小为 93,604,593 字节，SHA256 为 `73e6a4fcb02f3c6cddd52fcad8da6849ce1ab01cf48f9b02d2a5c7e382a1db8c`；导入前验证了 22 个新 blob 与 27 个复用层的内容摘要。

本机发布证据位于忽略目录 `temp/tencent-cloud/qa-failure-release-{preview,apply}.jsonl` 和 `qa-failure-images-import.jsonl`；未在 Git 中保存真实凭据。

独立前后对比通过：只有 app/frontend 的容器 ID 和镜像改变，其余七服务不变；全部九容器的环境摘要和端口绑定均不变。结果为 `temp/tencent-cloud/qa-failure-release-verification.json`，完整前后元数据为同目录的 `qa-release-metadata-{before,after}.json`。

北京时间 12:24，经可信 HTTPS 网关和前端代理发起一次实际企业知识库快速问答，21.060 秒正常结束：544 个正文事件、1 个引用事件、1 个完成事件，持久化正文与流式正文同为 2,652 字符；重新读取消息历史时 `is_completed=true`，无故障码、无数据库迁移错误。会话“企业知识库使用说明”保留在管理员账号，未创建测试命名或测试资料。测试脚本只记录事件数量、完成状态等元数据，不打印答案正文或凭据。

本次实际模型请求成功，不能据此声称生产环境已复现并验证 503 或超时；这些异常及刷新后的错误恢复由上述自动回归覆盖，没有为测试故意中断云端模型服务。真实问答结果在本机 `temp/tencent-cloud/qa-failure-live-verification.jsonl`，云端为私有 `qa-failure-verification-*.json`。需要人工验证：刷新浏览器后进入快速问答，确认正常回答展示、引用和输入框可用；将来遇到供应商故障时应显示明确错误，刷新不恢复为等待。

## 升级重新应用

优先采用上游对应修复。若仍需补丁，检查普通生成和模型兜底是否都同步向外返回失败，handler 是否仅完成一次，错误码是否跨 SSE 与消息历史保持一致。重新运行上述故障回归及正常回答／主动停止测试；不要仅复制页面提示而遗漏持久化和后端期限。
