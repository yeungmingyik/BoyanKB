# 伙伴远程访问

伙伴使用浏览器访问 HTTPS 子域名，以 LibreChat 原生账号登录。管理员逐用户授予知识 Agent 的 `VIEW`，公开注册、社交注册和会话公开分享保持关闭。入口开放不授予知识权限，模型仍使用各用户的个人供应商配置。

## 网络配置

本地 PC 通过 Cloudflare 命名 Tunnel 向外建立连接，无需发布路由器端口。Tunnel 连接应用的 `http://app:3080`，应用继续仅在宿主机回环地址监听。Tunnel 使用独立 `partner-edge` 网络，不加入数据库网络。

域名须在 Cloudflare 上处于可用状态。Free 方案使用完整 DNS 托管；保留其他权威 DNS 的 CNAME/partial 模式需要相应付费方案。临时 Quick Tunnel 不支持 SSE，不适用流式聊天。

现有域名可同时承载邮箱与知识库。例如 `kb.example.com` 用于知识库，MX、SPF、DKIM 及邮箱相关 CNAME 保持原值。切换权威 DNS 前导出并核对全部记录；邮箱相关记录使用 DNS only。域名注册商可以保持不变。

Cloudflare 中准备以下配置：

| 项目 | 配置 |
| --- | --- |
| Tunnel | 专属命名 Tunnel，远程管理配置 |
| 主机名 | `kb.example.com` |
| 服务 | `http://app:3080` |
| 兜底路由 | `http_status:404` |
| DNS | 代理 CNAME，目标为 `<tunnel-id>.cfargotunnel.com` |
| HTTPS | 针对知识库主机名将 HTTP 跳转为 HTTPS |
| 缓存 | 不为认证、知识 API、会话或流式响应设置缓存规则 |

## 准备与启用

将 Tunnel 凭据保存为本机文件，勿放入命令参数、仓库或聊天记录。初始化本地实例后执行：

```powershell
pwsh -NoProfile -File scripts/boyankb/configure-access.ps1 -Action Prepare -Origin https://kb.example.com -TokenFile D:/Private/tunnel-token
```

`Prepare` 在实例私有目录生成 `access/plan.json`、`access/remote-config.json` 和 `access/tunnel-token`，不会启用公网入口。将 `remote-config.json` 中的配置应用到对应 Tunnel，核对 DNS 与证书可用后执行：

```powershell
pwsh -NoProfile -File scripts/boyankb/configure-access.ps1 -Action Enable
pwsh -NoProfile -File scripts/boyankb/configure-access.ps1 -Action Status
```

启用时设置 `DOMAIN_CLIENT`、`DOMAIN_SERVER`、`SESSION_COOKIE_SECURE=true` 和 `TRUST_PROXY=1`，重建应用并等待 Tunnel 健康。配置文件按次备份；启用失败会尝试恢复此前的访问配置。凭据只读挂载，固定版本与镜像摘要见 `deploy/boyankb/compose.tunnel.yaml`。

常规 `start-local.ps1` 和 `stop-local.ps1` 会包含已启用的 Tunnel。PC 和 Docker Desktop 必须保持运行。`Status` 的健康状态仅表示连接器状态，不能代替公网验收。

## 验收

1. 从伙伴实际网络访问子域名，检查有效证书和 HTTP 到 HTTPS 跳转。
2. 验证公开注册不可用，匿名、未授予 `VIEW` 与被封禁用户不能读取知识。
3. 授权用户完成登录、资料阅读、搜索、问答、引用跳转和刷新会话；Cookie 启用 Secure 与 HttpOnly。
4. 检查流式连接、同源请求校验、个人会话隔离，以及撤权后新请求和在途输出停止。
5. 在桌面与移动尺寸完成关键流程；记录实际网络、设备、模型和耗时。

## 停用与恢复

```powershell
pwsh -NoProfile -File scripts/boyankb/configure-access.ps1 -Action Disable
```

先停止 Tunnel，再恢复启用前的域名、Cookie 和代理字段并重启应用。其他模型配置、账号与数据保留。停用不会删除 DNS 或 Cloudflare Tunnel；重新启用可复用已准备的配置。需要改域名或轮换凭据时，先停用，再运行 `Prepare`。

## 配置验证

```powershell
pwsh -NoProfile -File scripts/boyankb/test-access-config.ps1
```

使用独立合成实例验证配置、错误恢复和 Compose 网络范围。生命周期验证模拟 Docker 操作，不能替代真实 Tunnel、公网登录或流式验收。

协议与前提以 [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/get-started/)、[DNS 托管模式](https://developers.cloudflare.com/dns/zone-setups/partial-setup/)和 [Quick Tunnel 限制](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)为准。
