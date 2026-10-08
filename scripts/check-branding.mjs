import { readFile, readdir } from 'node:fs/promises'
import { dirname, extname, join, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const markers = /weknora|tencent|腾讯|(?:developers|work|qyapi|openws)\.weixin\.qq\.com|tdesign\.gtimg\.com/i
const textExtensions = new Set(['.vue', '.ts', '.tsx', '.js', '.mjs', '.html', '.css', '.less', '.json', '.md', '.txt'])
const categories = new Set(['copyright', 'protocol_id', 'storage_key', 'internal_symbol', 'real_provider', 'hidden_integration', 'diagnostics', 'developer_comment', 'upstream_artifact'])
const labels = {
  copyright: '版权与许可证', protocol_id: '协议、API及环境变量', storage_key: '持久存储键',
  internal_symbol: '内部符号与样式名称', real_provider: '真实第三方服务名称',
  hidden_integration: '已隐藏的上游集成说明', diagnostics: '开发诊断信息',
  developer_comment: '开发注释', upstream_artifact: '原始上游制品标识',
}

async function walk(directory) {
  const files = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) files.push(...await walk(path))
    else if (entry.isFile() && (textExtensions.has(extname(path)) || entry.name === 'LICENSE') && !/\.(?:test|spec)\.[^.]+$/.test(entry.name)) files.push(path)
  }
  return files
}

export async function scanSource() {
  const files = [...await walk(join(root, 'frontend/src')), join(root, 'frontend/index.html'), join(root, 'frontend/embed.html'), join(root, 'frontend/public/weknora-widget.js')].sort()
  const findings = []
  for (const path of files) {
    const file = relative(root, path).replaceAll('\\', '/')
    const lines = (await readFile(path, 'utf8')).split(/\r?\n/)
    for (let index = 0; index < lines.length; index++) {
      const text = lines[index].trim()
      if (markers.test(text)) findings.push({ file, line: index + 1, text })
    }
  }
  return { findings, files: files.length }
}

export function validateAllowlist(findings, entries) {
  const allowed = new Map()
  for (const entry of entries) {
    if (!entry.file?.startsWith('frontend/') || entry.file.includes('..') || entry.file.includes('\\') || !entry.text || !markers.test(entry.text) || !categories.has(entry.category) || !Number.isInteger(entry.count) || entry.count < 1) throw new Error('Invalid allowed-upstream entry')
    const key = `${entry.file}\n${entry.text}`
    if (allowed.has(key)) throw new Error(`Duplicate allowed-upstream entry: ${entry.file}: ${entry.text}`)
    allowed.set(key, { ...entry, used: 0 })
  }
  const unknown = []
  for (const finding of findings) {
    const entry = allowed.get(`${finding.file}\n${finding.text}`)
    if (!entry || ++entry.used > entry.count) unknown.push(finding)
  }
  const stale = [...allowed.values()].filter(entry => entry.used < entry.count)
  const counts = Object.fromEntries([...categories].map(category => [category, [...allowed.values()].filter(entry => entry.category === category).reduce((sum, entry) => sum + Math.min(entry.used, entry.count), 0)]))
  return { unknown, stale, counts }
}

async function checkBackendWiring() {
  const checks = [
    ['internal/config/config.go', ['branding.LoadFromEnv()', 'applyPromptBranding(cfg.PromptTemplates, branding.Current())']],
    ['internal/config/branding.go', ['templates.SystemPrompt', 'templates.AgentSystemPrompt', 'templates.IntentPrompts', 'templates.Fallback', 'brand.PromptText(group[i].Content)']],
    ['internal/handler/memory.go', ['branding.Slug()+`-memories.json"`']],
    ['internal/mcpserver/server.go', ['branding.Slug()', 'branding.ProductText(serverInstructions)']],
    ['internal/mcpserver/tools_ask.go', ['branding.Name()+" retrieves']],
    ['internal/mcp/client.go', ['Name:    branding.Name()']],
    ['internal/mcp/oauth_manager.go', ['RegisterClient(ctx, branding.Name())']],
    ['internal/im/feishu/adapter.go', ['"content": branding.Name()']],
    ['internal/im/service.go', ['name, branding.Name()']],
    ['internal/router/swagger_branding.go', ['branding.Current().SwaggerJSON(docs.SwaggerInfo.ReadDoc())']],
    ['internal/branding/swagger.go', ['info["title"] = c.ProductName + " API"', 'delete(info, "contact")', 'delete(info, "termsOfService")']],
    ['internal/agent/tools/todo_write.go', ['base.description = branding.ProductText(base.description)']],
    ['internal/application/service/tenant_skill_install.go', ['Install this %s skill', 'branding.Name(), skillDir']],
  ]
  for (const [file, required] of checks) {
    const source = await readFile(join(root, file), 'utf8')
    for (const token of required) if (!source.includes(token)) throw new Error(`Backend brand wiring missing: ${file}: ${token}`)
  }
  return checks.length
}

function digest(value) {
  return createHash('sha256').update(value).digest('hex')
}

function requireValue(condition, message) {
  if (!condition) throw new Error(message)
}

function attribute(tag, name) {
  const match = tag.match(new RegExp(`\\b${name}\\s*=\\s*(["'])(.*?)\\1`, 'i'))
  return match?.[2] ?? ''
}

function htmlDecode(value) {
  return value.replace(/&(amp|lt|gt|quot|#39);/g, entity => ({ '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" })[entity])
}

export async function checkDeployment(base, brand, { embedChannelId } = {}) {
  const origin = new URL(base)
  requireValue(['http:', 'https:'].includes(origin.protocol) && !origin.username && !origin.password && origin.pathname === '/' && !origin.search && !origin.hash, '--url must be an HTTP(S) origin without credentials')
  if (embedChannelId !== undefined) requireValue(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(embedChannelId), '--embed-channel-id must be a UUID')
  const downloaded = new Map()
  const get = async (path, statuses = [200]) => {
    const url = new URL(path, origin)
    requireValue(url.origin === origin.origin, `Cross-origin asset is not allowed: ${path}`)
    const response = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(15000) })
    requireValue(statuses.includes(response.status), `${url.pathname}: unexpected HTTP ${response.status}`)
    const data = Buffer.from(await response.arrayBuffer())
    requireValue(data.length <= 8 * 1024 * 1024, `${url.pathname}: response too large`)
    const result = { data, status: response.status, type: response.headers.get('content-type') || '', csp: response.headers.get('content-security-policy') || '' }
    downloaded.set(url.pathname, result)
    return result
  }
  const htmlPaths = ['/', '/login']
  if (embedChannelId) htmlPaths.push(`/embed/${embedChannelId}`)
  for (const path of htmlPaths) {
    const { data, type, csp } = await get(path)
    const html = data.toString('utf8')
    requireValue(type.includes('text/html'), `${path}: expected HTML`)
    requireValue(htmlDecode(html.match(/<title[^>]*>([^<]*)<\/title>/i)?.[1] || '') === brand.productName, `${path}: incorrect initial title`)
    requireValue(!/__BRAND_[A-Z_]+__/.test(html), `${path}: unresolved brand token`)
    if (path.startsWith('/embed/')) requireValue(/(?:^|;)\s*frame-ancestors\s+/.test(csp), `${path}: missing frame-ancestors policy`)
    const links = html.match(/<link\b[^>]*>/gi) || []
    requireValue(links.some(tag => attribute(tag, 'rel') === 'icon' && htmlDecode(attribute(tag, 'href')) === brand.faviconUrl), `${path}: incorrect favicon`)
    if (path === '/') {
      const metas = html.match(/<meta\b[^>]*>/gi) || []
      requireValue(metas.some(tag => attribute(tag, 'name') === 'description' && htmlDecode(attribute(tag, 'content')) === `${brand.productName} ${brand.description}`), 'Incorrect initial description')
      requireValue(links.some(tag => attribute(tag, 'rel') === 'apple-touch-icon' && attribute(tag, 'href') === brand.appleTouchIconUrl), 'Incorrect Apple touch icon')
    }
  }
  const denied = await get('/embed/branding-check', [401, 403, 404, 500])
  requireValue(!/id\s*=\s*["']embed-app["']/.test(denied.data.toString('utf8')), 'Invalid embed entry returned the embed application')
  const logo = await readFile(join(root, 'branding/logo.svg'))
  const png = await readFile(join(root, 'branding/logo.png'))
  for (const path of new Set([brand.logoUrl, brand.faviconUrl, brand.appleTouchIconUrl])) {
    const { data, type } = await get(path)
    const expected = path.endsWith('.svg') ? logo : png
    requireValue(type.includes(path.endsWith('.svg') ? 'image/svg+xml' : 'image/png'), `${path}: incorrect image Content-Type`)
    requireValue(digest(data) === digest(expected), `${path}: deployed logo bytes differ from branding assets`)
  }
  const template = await readFile(join(root, 'frontend/public/weknora-widget.js'), 'utf8')
  const tokens = { __BRAND_NAME__: brand.productName, __BRAND_SLUG__: brand.productSlug, __BRAND_PRIMARY_COLOR__: brand.colors.action, __BRAND_LOGO_URL__: brand.logoUrl }
  const jsEscape = value => JSON.stringify(value).slice(1, -1).replace(/'/g, "\\'").replace(/</g, '\\u003c')
  const expectedWidget = template.replace(/__BRAND_[A-Z_]+__/g, token => {
    requireValue(token in tokens, `Unknown widget token: ${token}`)
    return jsEscape(tokens[token])
  }).replaceAll('\r\n', '\n')
  for (const path of [`/${brand.productSlug}-widget.js`, '/weknora-widget.js']) {
    const { data, type } = await get(path)
    requireValue(/(?:javascript|ecmascript)/.test(type), `${path}: incorrect script Content-Type`)
    requireValue(data.toString('utf8').replaceAll('\r\n', '\n') === expectedWidget, `${path}: deployed widget differs from branded template`)
    requireValue(expectedWidget.includes('global.WeKnora = api') && expectedWidget.includes('global[BRAND_NAME] = api'), 'Widget compatibility API missing')
  }
  const license = await readFile(join(root, 'LICENSE'))
  const servedLicense = await get('/licenses/LICENSE')
  requireValue(servedLicense.type.includes('text/plain'), 'License endpoint returned non-text content')
  requireValue(digest(license) === digest(servedLicense.data), 'Deployed LICENSE differs from preserved source')
  console.log(`通过：已部署初始 HTML、Logo、favicon、Apple 图标、新旧 widget 和 LICENSE（${downloaded.size} 个 URL）。`)
  console.log(`无效嵌入入口未返回应用页面：HTTP ${denied.status}${denied.status === 500 ? '（服务器错误；不据此判断有效渠道可用）' : ''}。`)
  console.log(embedChannelId ? '通过：指定既有渠道的嵌入 HTML 品牌及 frame-ancestors 响应头。' : '未验证有效嵌入 HTML；传入 --embed-channel-id UUID 可检查既有启用渠道。')
  console.log('部署检查范围：公开静态资源；不登录、不修改数据、不验证已有用户内容或交互后的页面状态。')
}

export async function main(args = process.argv.slice(2)) {
  let url
  let embedChannelId
  let list = false
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--url' && args[i + 1]) url = args[++i]
    else if (args[i] === '--embed-channel-id' && args[i + 1]) embedChannelId = args[++i]
    else if (args[i] === '--list') list = true
    else if (args[i] === '--help') {
      console.log('node scripts/check-branding.mjs [--list] [--url http://127.0.0.1:8088 [--embed-channel-id UUID]]')
      return
    } else throw new Error(`Unknown or incomplete option: ${args[i]}`)
  }
  requireValue(!embedChannelId || Boolean(url), '--embed-channel-id requires --url')
  const brand = JSON.parse(await readFile(join(root, 'branding/brand.json'), 'utf8'))
  const allowed = JSON.parse(await readFile(join(root, 'branding/allowed-upstream.json'), 'utf8'))
  requireValue(allowed.version === 1 && Array.isArray(allowed.entries), 'Invalid allowed-upstream format')
  for (const feature of ['upstreamCli', 'upstreamSkill', 'browserExtension']) requireValue(brand.features?.[feature] === false, `Re-audit hidden integration exceptions before enabling ${feature}`)
  const { findings, files } = await scanSource()
  const result = validateAllowlist(findings, allowed.entries)
  for (const item of result.unknown) console.error(`未批准残留 ${item.file}:${item.line}: ${item.text}`)
  for (const item of result.stale) console.error(`失效例外 ${item.file}: ${item.text}（${item.used}/${item.count}）`)
  if (result.unknown.length || result.stale.length) throw new Error(`${result.unknown.length} 个未批准残留，${result.stale.length} 个失效例外`)
  const backendCount = await checkBackendWiring()
  console.log(`通过：${files} 个前端源码文件、${backendCount} 个后端品牌接入文件；未批准残留为 0。`)
  for (const [category, count] of Object.entries(result.counts)) if (count) console.log(`例外 ${category}（${labels[category]}）：${count}`)
  if (list) for (const entry of allowed.entries) console.log(`${entry.category}\t${entry.file}\t${entry.count}\t${entry.text}`)
  console.log('源码范围：frontend/src 文本、两个 HTML 入口、widget 模板；测试文件、二进制及图像素材不参与文本扫描。Go 检查为品牌接入断言，输出行为由 Go 测试验证。')
  if (url) await checkDeployment(url, brand, { embedChannelId })
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => {
    console.error(`失败：${error.message}`)
    process.exitCode = 1
  })
}
