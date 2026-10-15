# 快速问答长时间等待排查

日期：2026-10-15。现场检查时间为北京时间 11:33–11:36。分支：`feat/qa-response-diagnosis`。

## 结论

本次长等待的主要原因是已配置的 DeepSeek 问答服务异常。云端实际使用 `api.deepseek.com` 的 `deepseek-flash`，11:16 的调用收到 HTTP 503，错误为 `Service is too busy`；随后实体抽取和另一条请求的问题理解调用分别达到约 300 秒超时。

DeepSeek [官方事件公告](https://status.deepseek.com/incidents/7116348214287)显示：北京时间 08:11 起网页/API 部分中断，影响 Flash 和 Pro API；检查时状态仍为已定位、正在修复。[错误码说明](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes/)将 503 定义为服务器繁忙。此结论由本次请求日志与官方公告共同支持，不能把没有认证的 API 可达性检查当作模型生成成功。

## 现场证据

| 检查 | 结果 |
| --- | --- |
| 九个 Compose 服务 | 全部 healthy；重启计数为 0，无 OOMKilled |
| 宿主资源 | 约 5,221 MiB 可用内存，swap 使用约 1 MiB，Docker 所在磁盘约 19,834 MiB 可用；负载约 0.05/0.15/0.17 |
| HTTPS 登录页 | 可信 TLS 返回 HTTP 200，本机总耗时约 3.9 秒 |
| 无认证 DeepSeek 连通性 | `/models` 约 0.22 秒返回 401，仅证明服务端可达；不代表配置密钥错误或生成可用 |
| 11:16 快速问答 | 问题理解调用收到 503；11:21 实体抽取超时；检索随后约 6 秒完成；11:26 出现用户停止事件，消息已结束但答案为空 |
| 11:27 快速问答 | 11:32 问题理解调用超时；11:35 数据库仍未完成、答案长度为 0；Redis 仅有初始事件和未完成的工具步骤 |
| 可用模型配置 | 一个远程问答模型 `deepseek-flash`，一个本地向量模型 `bge-m3:latest`；没有其他已配置的备用问答模型 |

检查只查询消息 ID、时间、状态、内容长度和事件类型，不以业务正文作为排查材料。没有导出密钥，没有发送额外的模型生成请求，也没有改变生产配置、停止会话、重启容器或修改数据库。

## 为什么会等待很久

快速问答在最终输出前可能先执行问题理解和图谱实体抽取。这些步骤也调用模型，并非全部在本地完成。部署代码的默认非流式模型调用超时为 300 秒、流式为 600 秒；云端未设置对应环境变量覆盖。多个阶段连续等待会累计成数分钟以上。具体时限还受上层上下文 deadline 约束，不能将这些数值相加视为所有请求的固定总超时。

前端“正在准备回答”是未完成且无已保存答案/步骤时的占位文案，不是后端仍在正常工作的证明。刷新会续接原有消息，不会自动修复模型服务。

## 另行发现的代码问题

当前源码中，普通快速问答依赖最终答案的 `Done` 事件完成消息。服务错误事件、模型流异常关闭等路径存在未持久化完成状态的可能；Redis 续传轮询也不检查执行器是否仍存活。

相关位置：

- `internal/handler/session/qa.go`：普通问答完成事件、异步执行及错误处理。
- `internal/handler/session/agent_stream_handler.go`：错误事件转发。
- `internal/application/service/chat_pipeline/chat_completion_stream.go`：模型错误与 channel 关闭处理。
- `internal/models/api/stream_assembler.go`：上下文取消后的事件发送。
- `internal/handler/session/stream.go`：刷新后的流事件续传。
- `frontend/src/views/chat/components/RagPipelineProgress.vue`：准备状态显示。

这是独立的错误收尾缺口。**未验证**：11:27 这条现场请求是否已经触发该缺口；11:35 采样仍可能处于后续模型调用等待，不能仅凭 `is_completed=false` 判断执行器已经退出。本次未修改相关代码或将其作为已修复项。

## 恢复与验证

用户可停止当前等待，待官方事件恢复后重新发送一次短问题。当前只配置一个问答服务，切换其他智能体仍可能调用同一 DeepSeek 接口，不能视为切换供应商。

恢复后验证：

1. 查看官方事件状态和云端容器健康状态。
2. 新建快速问答，先发送短问题，再发送一个可由企业知识库支持的问题，记录首个有效事件和完整回答耗时。
3. 确认没有新 503 或模型超时，消息最终 `is_completed=true`，答案非空；有知识库依据时人工核对引用。
4. 刷新已完成会话，确认答案保留且不再显示准备状态。
5. 如实施错误收尾修复，应使用模拟 503、超时、流异常关闭的回归测试，验证终止事件、失败提示与刷新后的持久化状态；不要依赖真实供应商故障作为唯一测试。

服务器只读基础检查：

```bash
docker ps --filter label=com.docker.compose.project=boyankb-tencent --format '{{.Names}} {{.Status}}'
docker stats --no-stream
free -m
```

消息状态检查可在只读事务中执行，避免读取问题或答案正文：

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT role, created_at, updated_at, is_completed, length(content) AS content_chars
FROM messages
WHERE deleted_at IS NULL AND created_at > now() - interval '1 day'
ORDER BY created_at DESC
LIMIT 10;
ROLLBACK;
```

完整问答恢复目前仍未验证，取决于外部模型服务恢复。若改用另一家服务或调整生产超时，先准备具体模型、费用、超时范围及回退配置，再依据项目约束取得生产变更确认。
