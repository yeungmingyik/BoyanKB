# 企业定制登记

## 飞书指定空间同步

日期：2026-10-08。基线：v0.8.2，提交 `3e8b0bfc`。分支：`feat/feishu-space-sync`。

| 文件 | 原因 | 改动 |
| --- | --- | --- |
| `internal/middleware/logger.go` | 数据源请求中的 `app_secret` 未被原脱敏规则覆盖 | 补充 App ID、App Secret、飞书前缀字段、tenant token 的脱敏；覆盖转义引号和截断字符串 |
| `internal/middleware/logger_test.go` | 防止正常及错误响应泄漏凭据 | 测试嵌套字段、请求/响应、201/400/500 等路径 |
| `internal/datasource/connector/feishu/core/client.go` | 原获取 token 日志输出 token 前后片段 | 日志仅记录有效期，不记录 token 或片段 |
| `internal/datasource/connector/feishu/core/client_credentials_test.go` | 防止 token 日志回归 | 用内存 HTTP 响应验证凭据及片段不会进入日志 |
| `internal/datasource/connector/feishu/wiki/connector.go` | 原连接器不校验节点空间和快捷方式来源 | 可选 `settings.allowed_space_id`；在获取正文前预检所有选中目录树，拒绝异空间、快捷方式、缺失或未知范围信息；拒绝显式空范围，锁定对应地区的官方 API origin；仅允许 export 模式，资源选择器也限制在目标空间 |
| `internal/datasource/connector/feishu/wiki/connector_scope_test.go` | 证明范围拒绝发生在正文读取前 | 模拟跨空间、快捷方式、分页异常、多根目录、默认兼容、空范围和非官方 origin 等场景，检查不安全配置在发出请求前被拒绝 |
| `docker/Dockerfile.feishu-local` | 本机需要运行修复后的二进制 | 在 v0.8.2 发布镜像上替换 app 二进制，保留原运行时、AnyDoc 功能及上游许可证；镜像标记 `io.weknora.feishu-guard=1` |
| `docker-compose.local.yml` | 启用本机定制镜像与 export 模式 | 固定本机 app 镜像，精确追加 `open.feishu.cn` 的 SSRF 白名单，适配本机 fake-IP DNS |
| `scripts/feishu-space-sync.py` | 可重复执行权限检查、目录预检及同步对账 | 凭据仅由进程读取，不输出真实值；目标空间固定，默认关闭定时与删除；复用数据源时原子更新凭据，增加受限快照、指定 probe 的关键词与基线验证、Wiki 可见空间及用户指定拒绝样本审计；`audit-documents` 只读检查目标 docx 的阅读与导出权限 |
| `scripts/test_feishu_space_sync.py` | 防止权限申请越界、凭据泄漏或验收误判 | 模拟权限申请、目录分页与范围、外部拒绝判定、快照路径与不覆盖、真实分块标记及更新证据、运行镜像检查、HTTP 地址与错误数值处理、文档权限布尔值及范围校验 |

未开启 `allowed_space_id` 的其他数据源继续使用原连接器路径。该开关约束本连接器的同步流程，不会撤销飞书应用原有权限，也不限制拥有数据源管理权限的 WeKnora 管理员另建数据源。飞书端 ACL 仍须单独收窄。

新增文档权限诊断后的完整 19 项 helper 模拟测试通过；定制镜像构建中的 middleware、飞书 core、wiki 三包 Go 测试全部通过，包含最新官方 origin 修复。镜像已部署，6 个容器均 healthy。首次同步创建 14 篇，用户补充阅读授权后第二次新增 1 篇，第三次仍有 4 篇导出失败；累计 15 篇解析完成，V1 probe 局部关键词检索通过。`audit-documents` 全 19 篇实测为 view 19 true、export 15 true 和 4 false，退出码 1 符合预期。用户已确认界面允许阅读者导出，当前正在定位四篇的界面与 API 差异，尚未完成 19/19、完整快照与增量验收。原冒烟脚本使用 testdata 的独立知识库运行成功，不涉及飞书正文的远程模型问答。

核心安全修复的六个源码及测试文件已提交为 `5af368ad`；脚本、部署配置和文档的提交状态另行记录。

进一步只读诊断确认四篇失败文档均有当前应用的只读协作者记录；历史 v1 public 接口在 docx、wiki 两种查询中均返回 `security_entity=anyone_can_edit`，probe 为 `anyone_can_view`。v2 public 对四篇及 probe 均返回 HTTP 403、`1063002`，`copy_entity` 与 `lock_switch` 未验证，不能据此确定现代继承或企业策略。当前仅等待《01.1项目产品手册》设置选项全文及继承、锁定、待保存状态，不重复添加应用或阅读授权。诊断未输出 AppID、成员 ID 或凭据。

验证命令、镜像 config ID、同步记录和后续实测状态见 [02-feishu.md](02-feishu.md)。不以模拟测试替代真实授权、数量和增量验收。
