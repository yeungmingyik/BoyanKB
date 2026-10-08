# BoyanKB 品牌定制

用户选择：不显示 Powered by；隐藏官方帮助、官网、GitHub 链接；隐藏未使用的上游服务和配套工具入口。产品名、颜色与开关统一读取 `branding/brand.json`。本次范围是当前 Compose 部署的 Web 主站、嵌入组件、服务端产品文案及系统生成文件。

## 配置与素材

`productName` 为显示名称，`productSlug` 用于新 widget 文件名、示例和导出文件名。`links` 为空时隐藏对应入口。`hiddenProviders` 过滤新建服务目录，已有配置仍使用真实服务名称。

浅蓝主色 `#60B9E8`，按钮和链接 `#2D64B0`，背景 `#EAF6FD`。颜色由同一 JSON 经 Vite 注入覆盖样式，保留成功、警告和错误的语义颜色。

`branding/logo.svg` 保留用户原始文件。该 SVG 内嵌 PNG，构建时原样复制 SVG，并提取未修改的 PNG 供触屏图标使用。favicon 使用真实 SVG 地址，不将 SVG 伪装成 ICO。旧登录宣传图默认隐藏。

## 第三方入口用途

| 入口 | 用途 | 本机处理 |
| --- | --- | --- |
| WeKnora Cloud | 使用上游托管模型服务 | 未配置，隐藏新建入口 |
| 腾讯混元、LKEAP | 接入腾讯云模型 API | 未配置，隐藏新建入口 |
| 腾讯云 COS | 将文件存储到腾讯云对象存储 | 本机仅 local 存储，隐藏新建选项 |
| 腾讯向量库 | 使用腾讯云检索后端 | 本机使用 PostgreSQL/ParadeDB，隐藏新建选项 |
| IMA | 导入腾讯 IMA 内容 | 数据源仅飞书，隐藏新建选项 |
| 上游 CLI、Claw Skill | 安装上游配套客户端 | 暂无企业发行物，隐藏入口 |
| 浏览器扩展 | 连接浏览器执行任务 | 本机无已连接设备，隐藏安装入口 |

这些开关控制界面展示，不是安全授权机制。它们不撤销真实服务权限、不替换供应商 API 地址，也不将第三方服务冒称为自有服务。

## 构建与部署

需要 Docker Desktop 与 Docker Compose。Windows 使用 Git Bash；PowerShell 7 可调用 `& 'D:/Git/bin/bash.exe' scripts/build-image.sh --with-app`。构建脚本不自动启动服务。

```bash
bash scripts/build-image.sh --with-app
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml up -d --no-deps app frontend
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml ps
```

单独构建前端使用 `bash scripts/build-image.sh`。默认镜像为 `boyankb/frontend:v0.8.2` 与 `boyankb/app:v0.8.2`。可通过 `BOYANKB_FRONTEND_IMAGE`、`BOYANKB_APP_IMAGE` 覆盖标签，构建和部署须使用一致的值。

前端专属 Dockerfile 使用仓库根目录上下文，配套 `.dockerignore` 只放行前端、品牌素材和许可证，并排除 `.env`、密钥、依赖与临时文件。依赖安装和编译在容器内执行。

后端镜像在原 v0.8.2 运行时上编译替换二进制，保留飞书范围检查与凭据脱敏修复。`BRANDING_CONFIG=/app/branding/brand.json` 指向镜像内共享配置；显式路径错误会阻止启动。修改 JSON 后重建两端镜像，保证名称一致。

## 兼容与版权

`LICENSE` 与上游版权声明保留。前端镜像提供 `/licenses/LICENSE`，两个镜像均保存 `/usr/share/licenses/upstream/LICENSE`。

新入口为 `/boyankb-widget.js` 与 `window.BoyanKB`。旧 `/weknora-widget.js`、`window.WeKnora` 继续使用同一实现。通信消息、JWT audience、HTTP 协议头、数据库 ID、浏览器存储键、Go module 与真实供应商标识维持兼容。

API 文档通过原有 Swagger 路由运行时覆盖品牌信息，保留 schema 与 `$ref`。release 模式仍按上游规则不开放 Swagger。四类内置提示词在配置加载时替换助手身份句；显式保存的用户提示词保持原内容。

历史对话、用户知识正文和自定义名称可能包含上游名称，不属于产品界面固定文案。测试工具曾写入的旧品牌标记可定点迁移：

```bash
node scripts/migrate-branding-smoke.mjs
node scripts/migrate-branding-smoke.mjs --apply
```

该脚本只修改固定名称的冒烟测试 agent、知识库和会话，且要求 description 精确匹配旧测试标记；不修改正文、名称或其他业务记录。

## 验证与升级

```bash
node --test scripts/check-branding.test.mjs frontend/src/api/embed/widgetBranding.test.mjs frontend/src/views/agent/AgentEditorModal.test.mjs
node scripts/check-branding.mjs --url http://127.0.0.1:8088
node scripts/verify-branding.mjs --prepare-embed
bash scripts/smoke-test.sh
```

浏览器验收要求 Node.js 24+ 和本机 Chrome/Edge。脚本启动隔离的临时用户目录，只在进程内读取 `.env` 中的冒烟账号，不接入日常浏览器配置。`--prepare-embed` 幂等创建或复用标记为 `managed-branding-verification-v1` 的专用 agent 与嵌入渠道，只关联 `local-smoke-stardust` 测试知识库；不读取飞书正文、不调用付费模型。该渠道仅允许本机 origin，使用安全 token 模式，token 不写入截图、报告或 URL。再次执行相同命令可复验；测试渠道保留以供复验。

已创建的专用验收渠道可用 `node scripts/verify-branding.mjs --embed-channel-id UUID` 指定，该模式不创建或修改渠道。静态检查同样支持 `--embed-channel-id UUID`，验证有效嵌入 HTML 与 `frame-ancestors`；未指定时会明确标记该项未验证。浏览器脚本退出 0 为通过、1 为失败、2 为部分未验证。截图与 JSON 结果保存在 `temp/branding-verification/`。

本次发现原创建渠道 API 将 `show_suggested_questions:false` 保存为 `true`。验收脚本仅在 `--prepare-embed` 且专用 agent、知识库、渠道名称与 origin 全部匹配时，将该测试渠道的开关更新为 false 并读回确认；未修改原渠道业务实现。业务渠道的此项创建行为仍按上游实现，创建后应检查保存值。

构建运行五语言 key 与消息编译检查、后端品牌与配置测试，以及原飞书安全回归测试。静态检查扫描 515 个前端文本文件及 13 个后端接入断言。`branding/allowed-upstream.json` 按文件、完整行和次数精确登记保留项；扫描结果为未批准残留 0，不代表整个 Git 仓库的原始文本为 0。版权、兼容协议、存储键、内部符号、隐藏的真实供应商说明与用户内容仍可包含上游名称。可用 `node scripts/check-branding.mjs --list` 逐项核查。

页面验收检查产品固定文案；账号邮箱中的历史域名、用户知识正文、历史会话和真实第三方名称不进行自动替换。原生 `rg -ni 'weknora|tencent|腾讯' frontend/src` 可用于原始检索，但结果需要与精确例外清单比对，不能直接批量替换。

冒烟脚本会先检查现有 app 的品牌镜像标签，保留第三层 Compose 配置后再刷新环境，已停止的容器也参与检测。首次部署或容器完全不存在时，先执行本页三层 Compose 启动命令，再运行冒烟脚本。

上游升级时先在新的 `feat/` 分支应用既有飞书安全修复，再重放品牌分支的提交。`git log --reverse --oneline ae660ff4..feat/branding-boyankb` 列出本次品牌提交，按顺序 cherry-pick。保留 `branding/`、定制 Dockerfile、Compose 覆盖文件与检查脚本，核对 `docs/CUSTOMIZATIONS.md` 中的接入位置，重新构建并运行上述检查。新增字符串不能自动加入例外清单；应先确认是版权、兼容标识还是漏改的产品文案。

仅回退界面与应用镜像时，确认原发布前端和飞书定制 app 镜像仍在本机，再使用基础与本机两层 Compose 重新创建 app/frontend。回退不会还原数据，也不会自动改回三个测试记录的 description。保留数据库、文件与模型卷，不使用 `down -v`。

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-deps --no-build app frontend
```

独立 `website-docs/`、桌面端和小程序不在当前镜像范围。仓库内未检出邮件模板或 SMTP 发送实现；外部身份服务的邮件品牌未验证，需要在实际启用后检查模板与邀请、登录邮件。

## 本次验证记录

日期：2026-10-08。部署入口：`http://127.0.0.1:8088`。

- 前后端定制镜像构建通过；后端 5 个 Go 包测试通过；前端与脚本共 65 项定向检查通过。
- 真实 DeepSeek 冒烟退出 0：管理员登录、文档解析、向量/关键词/混合检索、回答内容与来源引用通过。运行结束后 app 镜像仍为 `boyankb/app:v0.8.2`，品牌标签为 `1`。
- 原有六个容器均 healthy；飞书知识库仍为 19 篇解析完成。
- 原仓库 LICENSE 未修改；仓库、前端静态许可证与两个镜像许可证 SHA-256 均为 `889cfdf724aa5cdf542c5e14c3e3edbea9a71f07aa78faaa4f2d331d8f516a95`。
- 三条固定测试记录的旧 description 已迁移为 `boyankb-local-smoke-v1`；重复预览为 0 条待修改。

最终前端部署后，浏览器检查退出 0，共 26 项通过、22 张截图，报告为 [run-Eonfnu/report.json](../temp/branding-verification/run-Eonfnu/report.json)。覆盖五语言明暗登录、侧栏与收展、设置页、Logo/favicon、隐藏帮助与未用模型/存储选项、记忆导出名称、新旧 widget、无效渠道拒绝和有效渠道页面。已人工查看登录、暗色可读性、存储选项及嵌入截图。

代表截图：[中文登录](../temp/branding-verification/run-Eonfnu/login-zh-cn-light.png)、[暗色登录](../temp/branding-verification/run-Eonfnu/login-en-us-dark.png)、[存储选项](../temp/branding-verification/run-Eonfnu/storage-provider-options.png)、[真实嵌入](../temp/branding-verification/run-Eonfnu/valid-embed-page.png)。临时证据不纳入 Git；可用上文脚本重新生成。

专用测试渠道为 `321397ef-a518-4337-ae3e-c70af72a207e`。带此 ID 的静态检查退出 0，9 个 HTTP 资源通过，有效嵌入 HTML 与 CSP 检查通过，无效渠道返回 403。测试渠道第二次运行直接复用，无新增 agent 或渠道。

```bash
node scripts/check-branding.mjs --url http://127.0.0.1:8088 --embed-channel-id 321397ef-a518-4337-ae3e-c70af72a207e
```

容器当前引用镜像：

| 服务 | 标签 | 镜像 ID |
| --- | --- | --- |
| frontend | `boyankb/frontend:v0.8.2` | `sha256:e8644c38d2c65f8270241482b1186ada84757609531773249321d6ea8d3e2340` |
| app | `boyankb/app:v0.8.2` | `sha256:e54849dbe6894af518c17f833786b8c4bd42541909ba163bd34213f6b3f32cd2` |

Swagger 的规范与品牌覆盖已通过 Go 测试；当前 release 部署未开放该页面。IM 消息外发、外部身份邮件和真实第三方凭据失效后的维护流程未验证；本次没有发送外部消息或配置第三方账号，已有配置维护行为由 7 项设置导航回归检查覆盖。
