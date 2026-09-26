# 技术架构

## 基线与扩展范围

源代码固定为 `upstream.lock.json` 指定的 LibreChat 提交。BoyanKB 采用单实例、单企业共享知识池，复用原生账号、角色、Agent 分享权限、个人会话、模型连接和文件检索。

新增能力限于飞书同步、系统内资料目录与阅读、同步管理、有效版本过滤及必要的授权补强。不建立伙伴组织系统、独立登录系统、独立检索服务或独立管理后台。

```mermaid
flowchart LR
    F[飞书知识空间] --> W[同步 Worker]
    W --> M[(MongoDB 同步台账)]
    W --> S[资料快照存储]
    W --> I[原生文件入库]
    I --> R[RAG API]
    R --> V[(PostgreSQL / pgvector)]
    U[员工与授权伙伴浏览器] --> A[LibreChat API 与原生授权]
    A --> M
    A --> S
    A --> C[企业知识 Agent 授权与资料]
    C --> R
    A --> K[统一知识检索上下文]
    K --> R
    K --> L[用户选定供应商与个人密钥]
```

## 账号与授权

| 能力 | 采用方式 |
|---|---|
| 创建账号 | 原生 `create-user`；不要求 SMTP |
| 邮件邀请 | 原生 `invite-user`；需要可用 SMTP，邀请绑定邮箱 |
| 注册 | `ALLOW_REGISTRATION=false`；原生有效邀请仍可注册 |
| 登录 | `ALLOW_EMAIL_LOGIN=true`；首期不启用社交登录及社交自动注册 |
| 用户角色 | `ADMIN` 和 `USER`；员工和伙伴均可使用 USER |
| 知识授权 | ADMIN 持有一个企业知识 Agent，通过原生分享界面逐账号授予 VIEW |
| 知识撤权 | 删除该账号的 Agent VIEW；所有知识入口检查同一原生权限 |
| 临时封禁 | 原生 `ban-user`；补齐敏感入口的 `checkBan` 检查 |
| 管理操作 | ADMIN 使用现有分享界面、原生命令和新增同步页面 |

不向 USER 角色整体授予知识 Agent VIEW，避免逐用户撤权后仍通过角色继承权限。管理员账号不用于伙伴登录。创建用户、授予 VIEW、撤销 VIEW 是明确的授权动作；邮箱域名本身不构成合作伙伴授权。

系统入口开放前先建立并验证 ADMIN。禁止访客访问、公开注册、公开会话分享、USER 自建或再分享持久化 Agent、用户上传入库、联网搜索及代码执行。原生各供应商模型选择、个人供应商密钥和临时会话保留，全部经过知识 VIEW 门禁；不得通过平台 API Key、其他 Agent 或工具绕开知识访问校验。

目录、正文、附件、关键词搜索、语义检索、引用、会话读取、导出及下一轮问答必须经过：有效登录 → 原生封禁检查 → 企业 Agent VIEW → 资源归属和有效版本检查。管理同步额外要求 ADMIN。引用 ID 和文件 ID 不是授权凭证。

原生 `ban-user` 为有限期限封禁，会清理刷新会话，但旧访问 JWT 和已建立流并非在所有入口立即失效。实现阶段补齐统一鉴权、缓存失效和流终止；不得仅隐藏按钮。永久停止知识访问使用撤销 Agent VIEW，不以一个超长封禁期限替代。

撤权后的下一次受保护请求必须拒绝；已打开的问答流在 5 秒内终止输出，使用权限变更信号或最大 5 秒的原生权限复检。再次授权通过原生分享操作恢复。已经发送到浏览器或由用户保存的内容无法追回。

个人会话由原生 `user_id` 限定，管理员不通过产品界面读取他人会话。共享 Agent 不共享会话。撤销知识权限后，历史知识会话和引用同样不可读取；历史文本不自动重写。

## 模块落点

保留上游 workspace 结构。账号授权已落地，资料同步与检索模块按后续版本交付。

| 模块 | 路径 | 职责 |
|---|---|---|
| 知识授权 | `packages/api/src/knowledge/access.ts` | 逐用户 VIEW、封禁接线、操作范围与撤权会话清理 |
| 同步与发布 | `packages/api/src/knowledge/` | FeishuClient、同步任务、解析、快照、有效版本清单 |
| MongoDB 模型 | `packages/data-schemas/src/` | 源、节点、版本、任务、审计；沿用现有模型工厂 |
| 请求与响应契约 | `packages/data-provider/src/` | 知识 API 类型、客户端请求、配置 schema |
| API 接线 | `api/server/routes/knowledge.js` | 路由挂载与原生中间件连接 |
| Worker 接线 | `api/server/knowledge-worker.js` | 独立进程启动，调用 TypeScript 模块 |
| 资料与同步页面 | `client/src/components/Knowledge/` | 目录、阅读、引用、ADMIN 同步状态 |
| 检索接入 | 原生文件服务与 `packages/api/src/files/rag/` | 有效文件列表、检索、引用映射 |

接口、存储和飞书客户端通过参数注入。运行参数进入 LibreChat 配置 schema；秘密仅从环境或秘密文件读取。业务参数不散落于常量和 UI。

## 数据模型

| 实体 | 核心字段 | 唯一性与约束 |
|---|---|---|
| KnowledgeSource | id、spaceId、agentId、enabled、pollInterval、lastCompleteScan | 一个源对应一个已验证空间 |
| SourceNode | sourceId、nodeToken、parentNodeToken、documentId、title、path、lastSeenScanId | sourceId + nodeToken 唯一；记录目录与快捷方式 |
| KnowledgeDocument | id、sourceId、objType、objToken、activeRevisionId、status | sourceId + objType + objToken 唯一；同源重复节点共享正文 |
| KnowledgeRevision | id、documentId、sourceRevision、contentHash、blobKey、nativeFileIds、parserVersion、indexVersion、publishedAt | 文档 + 内容与解析版本唯一；已发布快照不可覆盖 |
| SyncRun | id、sourceId、lease、cursor、status、counts、startedAt、finishedAt | 同源只有一个有效租约；游标可恢复 |
| SyncItem | runId、documentId、step、status、errorCode、retryAt | 阶段幂等；失败可追踪 |
| KnowledgeAudit | actorId、action、resourceId、result、at | 不保存密钥、全文或问答正文 |

用户、Agent、ACL、会话与刷新会话继续使用原生模型。PartnerOrganization、PartnerGrant、第二套角色表不在首期范围内。

快照采用可替换 BlobStore 接口：首期 Docker 命名卷中的文件，后续接入 S3 兼容存储。数据库只保存相对对象键，不保存 Windows 绝对路径。

## 接口契约

| 路由 | 权限 | 结果 |
|---|---|---|
| `GET /api/knowledge/access` | 登录并通过封禁检查 | 当前用户授权状态、知识 Agent 配置状态 |
| `GET /api/knowledge/tree` | Agent VIEW | 目录、类型、可读状态；游标分页 |
| `GET /api/knowledge/documents/:id` | Agent VIEW | 生效版本与阅读内容 |
| `GET /api/knowledge/documents/:id/revisions/:revisionId` | Agent VIEW | 授权且未下线资料的被引用快照 |
| `GET /api/knowledge/assets/:id` | Agent VIEW | 鉴权后的素材流，支持 Range |
| `POST /api/knowledge/search` | Agent VIEW | 关键词或语义结果、片段、内部引用 |
| `GET /api/knowledge/sync-runs` | ADMIN | 分页任务与覆盖率 |
| `POST /api/knowledge/sync-runs` | ADMIN | 202 和任务 ID；支持幂等键 |
| `POST /api/knowledge/sync-runs/:id/retry` | ADMIN | 失败项重试任务 |

知识问答复用原生各供应商会话与临时 Agent 调用链。已保存的知识 Agent 维护 ACL 与资料映射，不给伙伴 EDIT 以修改供应商。alpha.4 在原生模型执行前接入统一服务端检索，向用户选择的模型传递受控上下文；不依赖模型自行触发检索工具。错误使用稳定机器码；401 表示未登录，403 表示无 Agent 或管理权限，404 避免泄露无权资源是否存在，409 表示同步冲突，429 表示限流，503 表示依赖暂不可用。响应不返回飞书令牌、源地址中的 token 或存储内部地址给伙伴。

## 检索与回答

同步产物映射到原生 `file_id`，作为企业知识 Agent 的 `file_search` 资源。复用原生文件服务、RAG API 与 pgvector，先用真实样本验证跨用户 Agent 文件检索和引用，再扩大覆盖。

所有模型入口由共同的知识请求处理器计算检索上下文，再调用原生供应商适配器。禁止用户覆盖系统注入的资料范围；普通提示文本和模型选择不携带授权依据。模型密钥仍属于当前用户。公司模型服务通过伙伴专属网关密钥接入同一原生凭据路径，实施细节见 [models.md](models.md)。

RAG API 的 owner/file 过滤不替代用户授权。调用方必须验证 Agent VIEW，并从服务端生效版本清单计算 `file_ids`；不得采信浏览器提交的 Agent、owner 或文件范围。RAG API 与向量库仅容器内部可达。

新版本先写快照与独立检索文件，验证完成后原子切换 MongoDB 的 `activeRevisionId`。检索前从该清单提取生效文件 ID；切换前的未发布文件和切换后的旧文件均不能进入新回答。旧索引清理允许异步失败，但不改变有效文件过滤。跨 MongoDB 与 pgvector 不宣称具有分布式事务。

首期精确检索覆盖标题、目录、标签和抽取正文的关键词子串；使用游标、输入长度限制和转义，不直接运行用户正则。以 18 文档起步，性能超过预算后评估专用知识全文索引。原生 Meilisearch 的消息搜索不等于企业知识全文搜索。

语义检索固定 embedding 模型与维度。模型或解析器升级创建新索引版本，验证后切换。保持上下文预算和命中片段数量可配置。业务回答只能基于命中资料；无法找到依据时返回资料不足，冲突资料并列给出来源。

引用使用系统生成的 documentId、revisionId、片段位置和内部链接，打开系统内原文。飞书 URL 仅对管理员提供运维入口。HTML、Markdown、图片和文件名按不可信内容处理：清洗脚本与事件属性、禁止任意外链自动抓取、防止路径穿越。文档内指令不获得调用工具或改变权限的能力。

## 先行验证

`0.1.0-alpha.2` 验证闭注册邀请流程、VIEW 授权与撤权；`0.1.0-alpha.3` 验证后台入库到原生 Agent 的文件生命周期；`0.1.0-alpha.4` 验证 RAG 引用与有效文件过滤。原生路径不足时，只替换相应适配层，不扩建整套账号或后台系统。

## 实现依据

| 已核实能力 | 基线路径 |
|---|---|
| 有效邀请绕过关闭注册 | `api/server/middleware/validateRegistration.js`、`checkInviteUser.js` |
| 创建用户、邮件邀请 | `config/create-user.js`、`config/invite-user.js` |
| 邀请有效期与消费 | `packages/api/src/auth/invite.ts` |
| 限时封禁与会话清理 | `config/ban-user.js`、`api/cache/banViolation.js`、`api/server/middleware/checkBan.js` |
| Agent 和文件权限 | `api/server/middleware/accessResources/canAccessAgentResource.js`、`api/server/services/Files/permissions.js` |
| 原生检索接入 | `api/server/services/Files/VectorDB/crud.js`、`packages/api/src/files/rag/search.ts` |

[RAG scope 契约](https://github.com/LibreChat-AI/rag-api/blob/c4e5cbf79fcece1df9adfff5dfed983a5990ca9f/app/scope.py)为独立组件证据，不代表本仓库已锁定或运行该服务版本。其镜像版本与兼容性在部署集成时固定。
