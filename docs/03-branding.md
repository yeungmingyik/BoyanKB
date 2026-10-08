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
node --test frontend/src/api/embed/widgetBranding.test.mjs
node scripts/check-branding.mjs --url http://127.0.0.1:8088
node scripts/verify-branding.mjs
bash scripts/smoke-test.sh
```

构建运行五语言 key 与消息编译检查、后端品牌与配置测试，以及原飞书安全回归测试。品牌静态检查将保留项明确分类，浏览器检查验证实际可见文字、资源和入口。截图与结果保存在 `temp/branding-verification/`。

上游升级时保留 `branding/`、定制 Dockerfile、Compose 覆盖文件与检查脚本，重放 `feat/branding-boyankb` 的定制提交，核对 `docs/CUSTOMIZATIONS.md` 中的接入位置，重新构建并运行上述检查。新增字符串不能自动加入例外清单；应先确认是版权、兼容标识还是漏改的产品文案。

独立 `website-docs/`、桌面端和小程序不在当前镜像范围。仓库内未检出邮件模板或 SMTP 发送实现；外部身份服务的邮件品牌未验证，需要在实际启用后检查模板与邀请、登录邮件。

## 本次验证记录

实现与构建进行中；完成后的检查结果将更新于此。
