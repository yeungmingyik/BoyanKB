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

## 发布与回滚

本次使用锁定基础镜像的 `docker/Dockerfile.app-boyankb` 和 `docker/Dockerfile.frontend-boyankb`；保留 `GO_BUILD_TAGS=anydoc`、品牌资源与 LICENSE。镜像使用单一 `linux/amd64` 平台及固定摘要。

`scripts/tencent-update-frontend.py` 保持原前端命令兼容，新增可选 `--app-image`，用于同一次发布中更新两个服务。默认只预览，`--apply` 执行；脚本验证部署标记、镜像摘要、九服务清单，备份 Compose 与镜像锁，核对环境、端口及其余七个容器不变。启动或校验失败自动恢复旧配置和两份旧镜像。环境文件、数据卷、飞书同步计划均不更改。

发布完成后记录新镜像摘要、九容器健康、HTTPS 登录页和 API 认证边界，确认没有意外重启其余服务。旧镜像保留可供回滚。本次任务用户已明确授权测试通过后更新云端。

人工复核：在快速问答中重新发送问题；出现模型故障时应看到明确红色提示，刷新仍保留，输入框可继续发送。模型服务正常时确认回答完整且有引用。这项真实供应商端到端结果以发布记录为准，不以模拟测试替代。

## 升级重新应用

优先采用上游对应修复。若仍需补丁，检查普通生成和模型兜底是否都同步向外返回失败，handler 是否仅完成一次，错误码是否跨 SSE 与消息历史保持一致。重新运行上述故障回归及正常回答／主动停止测试；不要仅复制页面提示而遗漏持久化和后端期限。
