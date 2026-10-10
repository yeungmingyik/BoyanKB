# 标签图标、登录布局与品牌交互色

日期：2026-10-08。分支：`feat/branding-polish`。继承透明矢量 Logo 与品牌定制。

2026-10-10 已撤销下述登录紧凑间距，恢复宽松布局并处理校验提示遮挡，见 [登录布局恢复](09-login-layout.md)。本页截图和镜像结果为 2026-10-08 的历史记录。

## 标签图标

修复前首页及新建聊天页都正确引用新 SVG，但 `/favicon.ico` 实际返回了 SPA HTML。此前检查器只确认该地址没有旧图像，错误地将 HTML 回退视为通过；同时 favicon URL 没有随素材更新而变化。

修复后，两个 HTML 入口均声明 SVG、32×32 PNG 和 16/32/48 多尺寸 ICO。资源名追加文件内容 SHA-256 的前 12 位，浏览器刷新页面后得到新的图标地址。传统 `/favicon.ico` 返回真实 ICO，使用正确类型与重新验证缓存策略；Docker 构建会比较产物和源 ICO 字节，阻止上游 public 图标覆盖新素材。

新增素材 `branding/favicon-32.png`、`branding/favicon.ico` 均由 `branding/logo.svg` 原生渲染，保留透明背景。共享配置新增 `faviconPngUrl`、`faviconIcoUrl`；这些字段只影响前端资源，无需重建后端。以后更新 Logo 时，应一并重新导出 `logo.png`、`favicon-32.png` 与多尺寸 `favicon.ico`，再构建前端。

本次未读取用户日常浏览器的缓存数据库。服务端缺失 ICO 已确认；用户既有标签是否保留了旧缓存、是否由特定浏览器的 SVG 图标兼容性导致，仍未验证。HTTP 资源与隔离浏览器请求记录用于验证修复，不能冒充用户当前标签栏的实际外观。

## 登录布局

紧凑品牌布局收紧顶部、卡片内边距、表头及按钮间距；三条功能说明横向排列并允许换行。原间距规则使用 `.t-form-item`，未命中 TDesign 实际的 `.t-form__item`；紧凑布局改用实际字段及顶部标签选择器。注册页返回链接按完整控件换行，避免日文链接单字折到下一行。输入框和按钮的操作尺寸保留。移除登录根容器的 `overflow:hidden`，注册、小屏、错误内容增加时允许自然滚动，不通过裁切隐藏表单。

目标尺寸为 1366×680、1440×900 的浏览器内容区，普通登录无需纵向滚动；移动端 390×640 允许必要滚动，必须无横向溢出、能到达登录及注册操作。200% 浏览器缩放另列为未验证，不以缩小视口冒充缩放测试。

## 绿色残留

语言菜单的选中项、勾选、悬停与焦点原先误用了绿色成功色，现改用共享品牌选择、文字和焦点 token，分别适配明暗主题。静态功能说明图标也采用品牌色。

同时检查并处理聊天模式菜单、知识库选择框与焦点、FAQ/文档操作、资源标签、设置导航和内置解析器中的同类误用。侧栏与共享资源的部分图标原先直接加载绿色 SVG；现在复用现有 `ResourceIcon`，通过 CSS mask 和 `currentColor` 使用品牌色，原始 SVG 仅作为形状。

真实成功/失败状态、上传完成、连接状态、内容差异、终端 ANSI 色及第三方品牌图标保留其原本含义，未全局重定义 success token。

## 构建和检查

```bash
bash scripts/build-image.sh
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml up -d --no-deps --no-build frontend
node --test scripts/check-branding.test.mjs
node scripts/check-branding.mjs --url http://127.0.0.1:8088 --embed-channel-id 321397ef-a518-4337-ae3e-c70af72a207e
node scripts/verify-branding.mjs --embed-channel-id 321397ef-a518-4337-ae3e-c70af72a207e
```

静态部署检查验证三格式哈希链接、MIME、图片签名、尺寸和实际字节，覆盖首页、登录页、新建聊天页及测试嵌入页。浏览器脚本记录桌面/移动尺寸、语言菜单实际颜色、关键控件滚动可达性和图标网络请求；不创建业务数据或发送付费问答。

样式守卫的字号数量和动画数量两项已有超基线，修改前后分别均为 30 和 67，未由本次改动增加。该历史基线问题未在本次修复。

## 本机验证结果

最终前端镜像为 `boyankb/frontend:v0.8.2`，镜像 ID 为 `sha256:723c9c143c701ea536ed06981b6b875cb2c5b42e638025a53e64f0ab56ed41ae`，构建版本 `6b23dd8b`。仅重建并替换 frontend；app 镜像仍为 `sha256:e54849dbe6894af518c17f833786b8c4bd42541909ba163bd34213f6b3f32cd2`。frontend、app、docreader、PostgreSQL、Redis、Ollama 六个容器均为 healthy。

| 检查 | 结果 |
| --- | --- |
| Docker 内构建 | 13 项 i18n 检查、38 项相关测试、Vite 构建及源/产物 ICO 比较通过 |
| 品牌检查器回归 | 9 项通过，包含拒绝 ICO 地址返回 HTML、伪造文件签名及旧图标链接 |
| 部署资源与源码 | 15 个 HTTP URL 通过；515 个前端文件及 13 个后端品牌接入文件的未批准残留为 0 |
| 桌面登录 | 中、英、俄、韩、日五语言 × 明暗主题 × 两种桌面尺寸，共 20 组无横向/纵向溢出 |
| 移动登录与注册 | 390×640，五语言 × 明暗主题下无横向溢出或隐藏裁切；登录、切注册、注册提交及返回登录控件均可滚动到达 |
| 语言菜单 | 10 组实际计算颜色匹配品牌 token，无成功绿误用 |
| 侧栏 | 明暗主题选中图标的颜色、遮罩资源加载及折叠/展开通过 |
| 既有品牌功能 | 隐藏帮助入口、未使用供应商、导出文件名、新旧 widget 与有效嵌入页通过 |
| 浏览器总结果 | 133 项通过、0 失败，保存 39 张截图 |

隔离 Chrome 自动请求 `/favicon-d0536d3d2bf3.ico`，响应为 HTTP 200、`image/x-icon`，未命中磁盘缓存。此记录只统计图标地址，普通页面 Logo 请求不计入 favicon 证据。浅色语言菜单选中背景为 `rgb(234, 246, 253)`，勾选为 `rgb(45, 100, 176)`；深色选中文字与勾选为 `rgb(131, 201, 242)`。

本机产物目录为 `temp/branding-verification/run-NW0MjH/`，该临时目录不提交 Git，可用上方命令重新生成：

- [完整检查报告](../temp/branding-verification/run-NW0MjH/report.json)
- [短屏登录及蓝色语言菜单](../temp/branding-verification/run-NW0MjH/language-menu-en-us-light.png)
- [短屏日文登录](../temp/branding-verification/run-NW0MjH/login-ja-jp-light-1366x680.png)
- [深色侧栏](../temp/branding-verification/run-NW0MjH/sidebar-dark.png)
- [移动注册及返回链接](../temp/branding-verification/run-NW0MjH/mobile-register-en-us-light.png)

部署后刷新已有标签页，让浏览器加载新的 HTML 与图标地址；若旧页面仍未更新，可使用 `Ctrl+Shift+R`。本次未验证用户既有标签栏外观、其他浏览器及 200% 缩放。

升级上游时先重放品牌和矢量 Logo 定制，再重放 `feat/logo-vector..feat/branding-polish`，检查 TDesign 的字段类名是否变化。保留三个 favicon 素材/配置、两处 HTML 注入、ICO 路由及 Docker 字节比较，随后执行本页构建与验证命令。LICENSE 和上游版权声明保持不变。
