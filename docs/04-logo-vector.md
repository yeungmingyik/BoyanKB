# Logo 透明矢量版

日期：2026-10-08。分支：`feat/logo-vector`。用户提供 `博研VIS(1).pdf`，授权制作透明底或 SVG，并允许简化渐变与调整网页配色。

## 原稿与提取

原 PDF 共 28 页，第 5 页是“企业标志标准化制图及方格制图”，保留了贝塞尔曲线、辅助圆、网格和尺寸线，属于矢量构造稿。实际提取采用第 2 页不带辅助线的标志路径，不包含公司中英文文字；网页旁边继续显示独立的 BoyanKB 产品名。

原稿 SHA-256：`c8a1c6bc191171401a4f627e88be8f5fdb48dc3f0b517b3d35133b94209a309d`。原 PDF 不修改，也不复制进仓库。

从 PDF 内容流读取 8 组闭合路径，将坐标平移并转换为 SVG 的向下 Y 轴。保留原始曲线轮廓，将两个渐变裁切轮廓改为实色填充。没有自动描摹截图，也没有把 PNG 包进 SVG。

## 交付素材

| 文件 | 用途 | 内容 |
| --- | --- | --- |
| `branding/logo.svg` | 网页 Logo、favicon、嵌入页 | 8 个 path，约 3.2 KB，`viewBox="0 0 256 147"`，透明背景 |
| `branding/logo.png` | 触屏图标与 PNG 导出 | 从同一 SVG 渲染，1024×588，sRGB、RGBA，约 37 KB |

配色：浅蓝 `#60B9E8`、蓝绿 `#379CB7`、蓝 `#2D64B0`、深蓝 `#1F4884`。使用纯色分层保留书本、字母 B 和山河曲线关系。去除截图白底和大块留白，保留约 6 个 SVG 单位的边缘安全间距。

SVG 是后续维护的主文件。修改 SVG 后，使用支持透明通道的 SVG 渲染器同步导出 PNG；保持 1024×588、sRGB、RGBA，不添加背景层。本次使用已安装的 Sharp 渲染器直接渲染 SVG。构建环境不再要求 SVG 内嵌 PNG，也不新增生产依赖。

## 构建与验证

```bash
bash scripts/build-image.sh
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.branding.yml up -d --no-deps --no-build frontend
node --test scripts/check-branding.test.mjs
node scripts/check-branding.mjs --url http://127.0.0.1:8088 --embed-channel-id 321397ef-a518-4337-ae3e-c70af72a207e
node scripts/verify-branding.mjs --embed-channel-id 321397ef-a518-4337-ae3e-c70af72a207e
```

只需重建前端，品牌配置路径和后端不变。刷新页面会重新验证 `/branding/` 资源缓存。图标缓存尚未更新时，可关闭旧标签页再打开。

素材静态验证已通过：SVG 无 `<image>`、base64 或渐变元素；PNG 为 RGBA，alpha 范围 0–255，完全透明像素 193944、不透明像素 403964、半透明抗锯齿像素 4204。已在浅色和深色背景检查大图，以及 16、32、48、64 px 宽度的显示。原 8 项品牌检查器测试通过。

前端镜像已重建并部署，镜像 ID 为 `sha256:cca7597eb80040faa48db7d31ad902569790ea53f7e8226a897a48b50ec525e0`。构建中的 13 项 i18n 与 23 项定向检查通过；9 个实际 HTTP 资源校验通过；六个容器均 healthy。

浏览器复验 26 项通过、22 张截图，报告见 [run-9PBpNA/report.json](../temp/branding-verification/run-9PBpNA/report.json)。已人工检查 [暗色登录](../temp/branding-verification/run-9PBpNA/login-zh-cn-dark.png) 与 [侧栏和嵌入页](../temp/branding-verification/run-9PBpNA/valid-embed-page.png)，确认新 Logo 无截图白底、资源完整。此轮复用既有测试渠道，没有创建业务数据或调用付费问答。

浅色、深色和小尺寸素材预览见 [preview.png](../temp/logo-vector/preview.png)。临时预览及截图不纳入 Git，正式素材保存在 `branding/`。
