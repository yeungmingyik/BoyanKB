# 飞书自建应用接入

本指南用于准备 BoyanKB 自动同步所需的飞书身份和资源授权。当前 `0.1.0-alpha.2` 不包含飞书同步 Worker；完成以下配置不会启动同步。整空间同步按 [同步规格](sync.md) 在后续版本交付。

## 1. 创建企业自建应用

1. 使用知识空间所属企业的飞书账号进入 [飞书开发者后台](https://open.feishu.cn/app)，创建企业自建应用，名称填写 `BoyanKB 知识同步`。
2. 在应用的“凭证与基础信息”中取得 App ID 和 App Secret，按第 5 节保存在本机私有配置中。
3. 在“添加应用能力”中添加“机器人”，用于应用身份识别及群组授权。
4. 在“开发配置 → 权限管理”中，申请第 2 节的应用身份权限。

同步使用应用身份 `tenant_access_token`。自建应用以 App ID、App Secret 换取访问凭证，由服务端按有效期自动更新；无需运行时依赖个人飞书登录、用户 OAuth 回调或开发者桌面的 CLI 登录目录。[访问凭证接口](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)

## 2. 申请读取权限

在权限管理中按下表的权限标识搜索并开通，选择应用身份权限。

| 权限标识 | 使用范围 | 官方接口 |
| --- | --- | --- |
| `wiki:wiki:readonly` | 查询空间、解析入口节点、分页读取空间目录 | [空间列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/list)、[节点信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)、[子节点列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list) |
| `docx:document:readonly` | 读取新版文档标题、版本、纯文本及内容块 | [文档信息](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/get)、[纯文本](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content)、[文档所有块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/list) |
| `drive:drive.metadata:readonly` | 读取文件标题、创建时间和最后编辑时间 | [文件元数据](https://open.feishu.cn/document/server-docs/docs/drive-v1/file/batch_query) |
| `docs:document.media:download` | 下载文档内部图片和附件 | [下载素材](https://open.feishu.cn/document/server-docs/docs/drive-v1/media/download) |
| `drive:file:download` | 下载作为独立文件存储的 PDF、Office 文件等 | [下载文件](https://open.feishu.cn/document/server-docs/docs/drive-v1/download/download) |

接口权限与知识空间、文档的资源权限分别生效。开通这些权限后，继续完成第 4 节的空间授权。读取更新时间通过元数据查询完成，无需申请修改文档或修改元数据的权限。

出现其他原生对象时，按实际读取接口补充权限并重新发布应用：

| 对象 | 按需权限 | 官方接口 |
| --- | --- | --- |
| 飞书电子表格 | `sheets:spreadsheet:readonly` | [读取多个范围](https://open.feishu.cn/document/server-docs/docs/sheets-v3/data-operation/reading-multiple-ranges) |
| 飞书多维表格 | `bitable:app:readonly` | [列出数据表](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list)、[查询记录](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/search) |

画板、幻灯片、旧版文档、音视频和其他嵌入对象按盘点结果适配；未完成适配的对象记录为未支持。新版文档纯文本不包含这些对象的完整内容。多维表格的高级权限与独立嵌入资源须另外验证可见范围。

## 3. 发布并确认生效

1. 进入“应用发布 → 版本管理与发布”，点击“创建版本”。
2. 设置版本号，例如 `1.0.0`；填写本次能力和申请理由：“只读同步指定知识空间的目录、正文和附件至企业知识库”。
3. 核对机器人能力及读取权限。应用可用范围先仅包含配置人员；按应用搜索、资源授权及读取验证的结果，补充必要的空间管理员或资料所有者。
4. 保存并申请线上发布。企业后台显示免审时直接发布；需要审核时，由企业应用管理员在后台指定的审核入口处理。
5. 确认正式版本已生效，再进行空间授权。后续新增权限或变更应用能力时重新创建版本并发布。

发布步骤以 [自建应用发布与审核](https://open.feishu.cn/document/best-practices/intro-to-custom-app-review) 和 [权限申请](https://open.feishu.cn/document/faq/trouble-shooting/how-to-fix-the-99991672-error) 为准。应用可用范围控制哪些成员可使用应用，空间资源授权继续按下一节配置。

## 4. 授权整个知识空间

由知识空间管理员优先直接添加应用：

1. 打开目标知识空间的“知识库设置 → 成员设置”。
2. 选择“可阅读的成员 → 添加成员”。
3. 在支持搜索应用或智能体的选择框中，搜索 `BoyanKB 知识同步`，核对并选中新发布的应用。
4. 关闭“发送通知”，确认添加；在“可阅读的成员”列表核对应用及其权限。
5. 使用应用身份完成第 6 节的目录、正文及附件验证。具有独立权限的页面和嵌入资源分别核对阅读与下载权限。

若当前界面不支持直接选择应用，或核对发布状态与可用范围后仍搜索不到应用，使用专用群组授权：

1. 在飞书客户端创建内部专用群，例如 `BoyanKB 同步授权`，仅加入负责维护的内部人员。
2. 在群设置中添加已发布的 `BoyanKB 知识同步` 应用机器人。
3. 返回知识空间的“成员设置 → 可阅读的成员 → 添加成员”，搜索该群并添加。
4. 核对群的阅读权限，并使用应用身份完成第 6 节的读取验证。

群中添加的是企业应用机器人；Webhook 自定义机器人不能提供这里的应用身份。群组路径要求应用可用范围包含相关资源所有者，按实际授权对象补充必要人员。空间授权选择可阅读权限，无需授予空间管理员或编辑权限。[群组授权方式](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa)

文档菜单“添加文档应用”授权的是该节点及其子节点，不能替代整空间授权。伙伴使用 BoyanKB 的账号授权，不加入授权群或飞书知识空间。

无法使用界面授权时，可由管理员使用其 `user_access_token`，通过成员接口将应用的 `open_id` 加为知识空间成员，设置 `member_type=openid`、`member_role=member`、`need_notification=false`，并在空间设置核对最终阅读权限。该方式属于一次性管理员配置；同步应用不申请成员管理权限。应用 `open_id` 与 `cli_` 开头的 App ID 是不同标识。[成员授权方式](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa)、[成员接口](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-member/create)

## 5. 保存本机凭据

完成 [本地初始化](deployment.md#初始化与启动) 后，在 `.local/boyankb-librechat/feishu.env` 保存以下配置：

```dotenv
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_WIKI_URL=
FEISHU_SPACE_ID=
```

| 配置 | 值 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `FEISHU_WIKI_URL` | 公司指定知识空间中的入口链接，仅用于解析源空间 |
| `FEISHU_SPACE_ID` | 核验后的数字空间 ID，首次解析前留空 |

该目录由 Git 忽略，使用初始化后的私有目录访问权限。真实入口链接、资源 token、密钥和访问凭证只保存在私有配置或秘密管理器中。无需把密钥粘贴到聊天、提交记录或问题单。

`feishu.env` 为后续 Worker 预留；当前 Compose 不加载该文件，也不向飞书发起同步。访问凭证由后续 Worker 获取，不手工写入环境文件。首期采用服务端轮询，需要本机能够向飞书开放平台发起 HTTPS 请求，无需为同步开放公网回调。

## 6. 接入验收

Worker 接入时，使用上述应用身份完成以下读取验证。测试记录仅保存脱敏结果。

| 验证 | 通过条件 |
| --- | --- |
| 应用身份 | 获取 `tenant_access_token` 成功，服务端按返回的 `expire` 管理有效期 |
| 入口解析 | 通过节点信息取得 `space_id`、`obj_type`、`obj_token`，数字空间 ID 与目标空间一致 |
| 整空间目录 | 从空间根开始分页并递归所有子节点，覆盖入口的兄弟节点及其他目录；核对独立权限页面 |
| 新版文档 | 使用 `obj_token` 读取文档信息、纯文本及所有块，处理全部分页和嵌套关系 |
| 更新识别 | 能读取元数据中的 `latest_modify_time` 及文档 `revision_id` |
| 素材与附件 | 文档内素材和独立文件分别通过对应下载接口验证；只看正文不算附件验证通过 |
| 扩展格式 | 按实际对象类型验证 Sheets、Base 等内容，单列不可见或未支持的对象 |

节点列表可能返回空 `items` 且 `has_more=true`，此时继续翻页，直到 `has_more=false`。Wiki 链接中的节点 token 不直接作为 Docx 文档 ID；内容接口使用节点返回的 `obj_token`。[目录分页](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list)、[资源标识](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-qa)

Docx 内容块查询使用最新版本 `document_revision_id=-1`。历史版本读取需要文档编辑权限；只读同步在采集前后比较 `revision_id`，变化时重新采集，版本一致后发布快照。[文档版本权限](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/list)

权限不足时分别检查应用权限是否已发布、目标资源是否已授权、资源是否允许下载。完整目录枚举成功、正文读取成功和附件下载成功分别记录，不能相互替代。具体覆盖与发布门槛见 [同步规格](sync.md#格式与发布门槛)。
