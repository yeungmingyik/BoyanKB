# 登录布局与空间切换说明

日期：2026-10-10。分支：`feat/login-layout-restore`。

## 刘伟健为何能切换星辰教育

真实成员记录中，刘伟健同时是博研总部（10000）和星辰教育有限公司（10005）的 active Owner。卡卡仅为星辰教育的 active Viewer，同时获得团队负责人邀请能力。刘伟健是系统管理员，但全局跨空间访问标志 `can_access_all_tenants` 为 false。

`internal/application/service/partner_provisioning.go` 创建伙伴空间时，显式把执行开通的管理员加入 Owner，并将其写入开通记录。`frontend/src/components/UserMenu.vue` 根据本人空间成员关系显示切换列表。因此当前操作是刘伟健切换工作空间，身份没有切换成卡卡。

Owner 拥有实际空间管理权限，包括成员、邀请、智能体及空间配置。普通网页私人会话仍按当前用户检查，业务文档读取和下载仍检查空间、用户与会话；不能由此推断 Owner 能直接读取卡卡的普通网页聊天及 Word。API、IM、embed 等渠道会话存在管理员读取例外，运维数据库权限也属于另一访问边界。

若希望日常总部账号不出现伙伴空间，建议将日常账号和负责开通、运维的 Owner 账号分开。当前刘伟健是星辰教育唯一 Owner，不能直接移除；交接前需审阅开通记录、智能体创建者和组织 Owner 绑定。仅隐藏菜单不会撤销权限，也不应为隐藏入口而将卡卡提升为 Owner。本次未修改任何成员关系或授权。

用户后续已明确要求刘伟健不能切入合作伙伴空间，待确认的完整交接方案见 [合作伙伴所有权交接与权限调整](10-partner-ownership-plan.md)。

### 截图身份复核

2026-10-10 再次只读核对数据库，成员关系仍为刘伟健在总部、星辰教育均为 active Owner，卡卡仅在星辰教育为 active Viewer。用户确认截图来自刘伟健登录后切换至星辰教育，并非使用卡卡邮箱重新登录后显示了刘伟健。

`UserMenu.vue` 第一行显示当前工作空间，第二行显示 `/auth/me` 返回的登录用户名和此用户在当前空间的角色。切换空间保留原登录令牌，只更改请求的空间上下文；不会变成另一个人的账号。直接访问 `/login` 也不能代替退出，已有登录状态会跳回工作区。

本次合作伙伴账号的公网复查已通过独立用户身份、Viewer 成员、三位智能体、九项技能、共享知识库及团队负责人检查；后续跨空间检查遇到 `transport_failed`，因此公网整套报告未通过。随后的本机只读验收 26 项全部通过，包括总部访问拒绝。证据分别为 `temp/enterprise-trial/acceptance/20261010-134741-9f85ffb1/report.json` 和 `temp/enterprise-trial/acceptance/20261010-134847-acd55275/report.json`。没有修改账号、成员关系或空间权限。

验证合作伙伴视角时，使用独立隐私窗口打开 `https://kb.boyantech.xyz/login`，以 `testpartner001@gmail.com` 登录；密码使用本机 `temp/enterprise-trial/.env` 中的 `TRIAL_PARTNER_PASSWORD`，不要发送到聊天。核对当前用户为卡卡、仅有星辰教育的成员资格，且总部数据被拒绝。姓名与工作空间是两个独立字段，切空间不能用于模拟另一个用户。多标签页跨账号切换的界面同步未实测，不将其作为本次原因。

## 登录布局修复

紧凑布局将 TDesign 实际字段 `.t-form__item` 的底部间距压到 8px。错误提示 `.t-input__extra` 使用绝对定位，高 20px，不参与父容器高度计算，因而与后续标签、输入框或按钮重叠。修复前五种视口、三种状态共 15 个案例的 20 条提示均复现重叠，并通过文字边界和命中元素确认遮挡。

`frontend/src/views/auth/Login.vue` 恢复压缩前的卡片内边距、标题与按钮间距、TDesign 默认字段和标签尺寸、纵向功能说明，以及顶部留白。登录和邀请注册的错误提示改为正常文档流并允许换行。表单随内容自然增高，小屏可纵向滚动。品牌颜色、Logo、邀请制和认证逻辑不变。

## 验证方法

1. 使用未登录的独立浏览器打开本机或公网 `/login`。
2. 输入不完整邮箱 `liuwei`，分别检查保持焦点和失焦后的错误提示。
3. 清空表单并点击登录，确认邮箱和密码提示完整可见，按钮不覆盖文字；该操作只应触发前端校验，不应提交认证请求。
4. 分别检查 1440×900、1366×768、1366×560、390×844、390×568；短屏滚动后能到达按钮，且没有横向溢出。
5. 检查长提示的自动换行，核对下一项和按钮随提示高度下移。模拟文案只用于隔离浏览器布局验证，不写入应用数据或翻译资源。

修复前证据位于 `temp/login-layout-review/2026-10-10T04-24-05-182Z/`。

## 部署与构建检查

源代码提交 `225571a5`。前端镜像为 `boyankb/frontend:v0.8.2-trial-login-v2@sha256:38f5da3a66a2695aeb0bf33229aafab31787d79b841637d27bf62b1dcbb68fbb`，已在 `docker-compose.trial.yml` 和 `deployment/trial/images.lock.json` 固定。实际更新 `weknora-local` 的 frontend，8 个服务均健康；公网网关 6 项检查通过，HTTPS 证书验证保持开启。

构建中的 13 项 i18n 检查、38 项既有测试、Vite 构建及 favicon 字节比对通过。构建仍提示部分既有 JavaScript 包较大，这不影响本次样式验证。

本次在 PowerShell 7 执行的构建和更新命令如下。重新构建后须先核对产物摘要，不能假设它与本次摘要相同；摘要变化时先更新试用配置和锁文件再启动。

```powershell
docker build --file docker/Dockerfile.frontend-boyankb --build-arg VITE_FRONTEND_COMMIT=225571a5 --tag boyankb/frontend:v0.8.2-trial-login-v2 .
docker compose -p weknora-local --env-file .env -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml -f docker-compose.presets.yml -f docker-compose.delivery-local.yml -f docker-compose.trial.yml up -d --no-deps --no-build --wait --wait-timeout 120 frontend
```

后续源代码有变化时使用新标签，核对新摘要并更新锁文件，不覆盖本次版本。旧前端 `boyankb/frontend:v0.8.2-trial-v1@sha256:8da66e76fbce6e6e8df4d4512830667a243013755a753a868f6b9b30b1537af4` 仍保留，需要回滚时在经过审阅的试用配置中恢复该摘要，并仅重建 frontend。

## 实际浏览器结果

新镜像下的五种尺寸、三种真实错误状态，共 15 个案例通过；另有 1 个明确标注的合成多行提示探针通过。共检查 22 条错误：无裁切、重叠或横向溢出，真实提示的文字采样点未被后续控件遮挡。五种尺寸输入完整有效邮箱后，错误均消失。已查看桌面双错误、短屏多行和有效邮箱清除截图。

公网匿名桌面 1366×768、手机 390×844 两个案例通过；保持严格 TLS 校验，使用 `BoyanKB-DeploymentCheck/1.0` User-Agent。复验未注入 CSS，未登录、未提交认证请求或修改账号数据。多行探针只在独立浏览器内替换提示文字，不修改业务数据或产品文案。

本机报告为 `temp/login-layout-review/2026-10-10T04-30-13-428Z/report.json`，公网报告为 `temp/login-layout-review/public-2026-10-10T04-30-13-423Z/report.json`。截图位于对应目录，可查看 [桌面错误提示](../temp/login-layout-review/2026-10-10T04-30-13-428Z/1366x768-empty-submit.png) 和 [公网手机登录](../temp/login-layout-review/public-2026-10-10T04-30-13-423Z/anonymous-390x844.png)。这些证据使用临时独立浏览器，不代表已验证所有浏览器或用户现有标签的缓存；已有页面可使用 `Ctrl+Shift+R` 加载新资源。

## 成员关系最终决定

2026-10-10：用户确认刘伟健可以作为 Owner 切换到星辰教育。取消后续所有权交接方案；此前未执行交接，无需回滚成员数据。登录布局和访问恢复修复保留。
