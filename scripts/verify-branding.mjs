import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const base = 'http://127.0.0.1:8088'
const args = process.argv.slice(2)
const publicOnly = args.includes('--public-only')
const prepareEmbed = args.includes('--prepare-embed')
const channelIndex = args.indexOf('--embed-channel-id')
let channelId = channelIndex < 0 ? '' : args[channelIndex + 1]
const recognized = new Set(['--public-only', '--embed-channel-id', '--prepare-embed'])
const results = []
const screenshots = []
let browser
let cdp
let session
let profile
let output
let phase = 'initialization'

class VerificationError extends Error {}

function requireCheck(condition, code) {
  if (!condition) throw new VerificationError(code)
}

function result(name, status = 'passed', detail) {
  const item = { name, status }
  if (detail) item.detail = detail
  results.push(item)
  process.stdout.write(`${status.toUpperCase()} ${name}${detail ? ` ${detail}` : ''}\n`)
}

class CDP {
  constructor(socket) {
    this.socket = socket
    this.next = 1
    this.pending = new Map()
    this.events = new Map()
    socket.addEventListener('message', (event) => {
      let message
      try { message = JSON.parse(event.data) } catch { return }
      if (message.method) {
        const key = `${message.sessionId || ''}:${message.method}`
        const waiting = this.events.get(key)
        if (waiting) {
          clearTimeout(waiting.timer)
          this.events.delete(key)
          waiting.resolve(message.params)
        }
      }
      const pending = this.pending.get(message.id)
      if (!pending) return
      this.pending.delete(message.id)
      clearTimeout(pending.timer)
      if (message.error) pending.reject(new VerificationError('CDP_REQUEST_FAILED'))
      else pending.resolve(message.result)
    })
    socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer)
        pending.reject(new VerificationError('BROWSER_CONNECTION_CLOSED'))
      }
      this.pending.clear()
      for (const waiting of this.events.values()) {
        clearTimeout(waiting.timer)
        waiting.reject(new VerificationError('BROWSER_CONNECTION_CLOSED'))
      }
      this.events.clear()
    })
  }

  static async connect(url) {
    const socket = new WebSocket(url)
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new VerificationError('BROWSER_CONNECTION_TIMEOUT')), 10000)
      socket.addEventListener('open', () => { clearTimeout(timer); resolve() }, { once: true })
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new VerificationError('BROWSER_CONNECTION_FAILED')) }, { once: true })
    })
    return new CDP(socket)
  }

  send(method, params = {}, sessionId) {
    const id = this.next++
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new VerificationError('CDP_REQUEST_TIMEOUT')) }, 20000)
      this.pending.set(id, { resolve, reject, timer })
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }))
    })
  }

  event(method, sessionId) {
    const key = `${sessionId || ''}:${method}`
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.events.delete(key); reject(new VerificationError('PAGE_LOAD_TIMEOUT')) }, 25000)
      this.events.set(key, { resolve, reject, timer })
    })
  }
}

async function evaluate(expression) {
  const response = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, session)
  requireCheck(!response.exceptionDetails, 'BROWSER_EVALUATION_FAILED')
  return response.result.value
}

async function waitFor(expression, code, timeout = 25000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { if (await evaluate(expression)) return } catch {}
    await delay(150)
  }
  throw new VerificationError(code)
}

async function navigate(route, selector) {
  const url = new URL(route, base)
  requireCheck(url.origin === base, 'NONLOCAL_NAVIGATION_REJECTED')
  const [response] = await Promise.all([
    cdp.send('Page.navigate', { url: url.href }, session),
    cdp.event('Page.loadEventFired', session),
  ])
  requireCheck(!response.errorText, 'PAGE_NAVIGATION_FAILED')
  await waitFor(`document.readyState === 'complete' && !!document.querySelector(${JSON.stringify(selector)})`, 'PAGE_CONTENT_TIMEOUT')
  await delay(450)
}

async function click(selector, code) {
  const clicked = await evaluate(`(() => {
    const element = [...document.querySelectorAll(${JSON.stringify(selector)})].find(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
    if (!element) return false
    element.click()
    return true
  })()`)
  requireCheck(clicked, code)
  await delay(300)
}

async function screenshot(name) {
  requireCheck(/^[a-z0-9-]+$/.test(name), 'INVALID_SCREENSHOT_NAME')
  const response = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, session)
  const filename = `${name}.png`
  await writeFile(path.join(output, filename), Buffer.from(response.data, 'base64'), { flag: 'wx' })
  screenshots.push(filename)
}

async function preferences(locale, theme) {
  await evaluate(`(() => {
    let userId = 'anon'
    try { userId = JSON.parse(localStorage.getItem('weknora_user'))?.id || 'anon' } catch {}
    localStorage.setItem('locale', ${JSON.stringify(locale)})
    localStorage.setItem('WeKnora_' + userId + '_theme', ${JSON.stringify(theme)})
    localStorage.setItem('weknora:new-user-guide-done:v1', '1')
  })()`)
}

async function brandingCheck(name, brand, selector, { logo = false, title = false, theme } = {}) {
  const state = await evaluate(`(() => {
    const scope = document.querySelector(${JSON.stringify(selector)})
    if (!scope) return null
    const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
    const text = scope.innerText || ''
    const links = [...document.querySelectorAll('a[href]')].filter(visible)
    const logos = [...scope.querySelectorAll('img')].filter(el => visible(el) && new URL(el.src).pathname === ${JSON.stringify(brand.logoUrl)})
    return {
      oldBrand: /weknora|tencent|腾讯/i.test(text),
      poweredBy: /powered\\s+by/i.test(text),
      upstreamLinks: links.some(el => /github\\.com\\/(?:tencent|wechatopenai)\\/weknora|weknora\\.weixin\\.qq\\.com|weknora\\.com/i.test(el.href)),
      logo: logos.some(el => el.complete && el.naturalWidth > 0 && el.alt === ${JSON.stringify(brand.productName)}),
      title: document.title.includes(${JSON.stringify(brand.productName)}),
      theme: document.documentElement.getAttribute('theme-mode'),
      favicon: [...document.querySelectorAll('link[rel~="icon"]')].some(el => new URL(el.href).pathname === ${JSON.stringify(brand.faviconUrl)}),
    }
  })()`)
  requireCheck(state, 'BRANDING_REGION_MISSING')
  requireCheck(!state.oldBrand, 'VISIBLE_UPSTREAM_BRAND')
  requireCheck(!state.poweredBy, 'POWERED_BY_VISIBLE')
  requireCheck(!state.upstreamLinks, 'UPSTREAM_HELP_LINK_VISIBLE')
  if (logo) requireCheck(state.logo, 'BRAND_LOGO_NOT_LOADED')
  if (title) requireCheck(state.title, 'PAGE_TITLE_NOT_BRANDED')
  if (theme) requireCheck(state.theme === theme, 'THEME_NOT_APPLIED')
  requireCheck(state.favicon, 'FAVICON_NOT_BRANDED')
  result(name)
}

async function credentials() {
  const names = new Set(['SMOKE_ADMIN_EMAIL', 'SMOKE_ADMIN_PASSWORD'])
  const values = {}
  const contents = await readFile(path.join(root, '.env'), 'utf8').catch(() => '')
  for (const line of contents.split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line)
    if (!match || !names.has(match[1])) continue
    let value = match[2].trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1)
    else value = value.replace(/\s+#.*$/, '').trim()
    values[match[1]] = value
  }
  for (const name of names) if (process.env[name]) values[name] = process.env[name]
  requireCheck(values.SMOKE_ADMIN_EMAIL && values.SMOKE_ADMIN_PASSWORD, 'SMOKE_LOGIN_CREDENTIALS_MISSING')
  return { email: values.SMOKE_ADMIN_EMAIL, password: values.SMOKE_ADMIN_PASSWORD }
}

async function login() {
  await preferences('en-US', 'light')
  await navigate('/login', '.login-layout input[autocomplete="email"]')
  const values = await credentials()
  const filled = await evaluate(`(() => {
    const values = ${JSON.stringify(values)}
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    for (const [selector, value] of [['input[autocomplete="email"]', values.email], ['input[autocomplete="current-password"]', values.password]]) {
      const input = document.querySelector(selector)
      if (!input) return false
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
    }
    return true
  })()`)
  requireCheck(filled, 'LOGIN_FORM_MISSING')
  await click('.submit-button', 'LOGIN_BUTTON_MISSING')
  await waitFor(`location.pathname.startsWith('/platform/') && !!document.querySelector('.aside_box')`, 'UI_LOGIN_FAILED', 40000)
  await delay(900)
  result('ui-login')
}

async function providerChecks(brand) {
  phase = 'model-providers'
  await navigate('/platform/settings?section=models', '.model-settings .model-card--add')
  await brandingCheck('model-settings', brand, '.settings-modal')
  await screenshot('model-settings')
  await click('.model-card--add', 'MODEL_ADD_CONTROL_MISSING')
  await waitFor(`!!document.querySelector('.setting-drawer__body .source-options')`, 'MODEL_DRAWER_TIMEOUT')
  await click('.setting-drawer__body .source-options .source-option:first-child', 'REMOTE_MODEL_CONTROL_MISSING')
  await click('.setting-drawer__body .t-select', 'MODEL_PROVIDER_CONTROL_MISSING')
  await waitFor(`document.querySelectorAll('.provider-select-popup .provider-name').length > 0`, 'MODEL_PROVIDERS_TIMEOUT')
  const providers = await evaluate(`[...document.querySelectorAll('.provider-select-popup .provider-name')].map(el => el.textContent.trim())`)
  requireCheck(!providers.some(value => /weknora|hunyuan|lkeap|tencent|腾讯|混元|知识引擎/i.test(value)), 'UNUSED_MODEL_PROVIDER_VISIBLE')
  requireCheck(providers.some(value => /deepseek/i.test(value)), 'ACTIVE_MODEL_PROVIDER_MISSING')
  await screenshot('model-provider-options')
  result('unused-model-providers-hidden')

  phase = 'storage-providers'
  await navigate('/platform/settings?section=storage', '.storage-backend-settings .backend-card--add')
  await brandingCheck('storage-settings', brand, '.settings-modal')
  await screenshot('storage-settings')
  await click('.backend-card--add', 'STORAGE_ADD_CONTROL_MISSING')
  await waitFor(`!!document.querySelector('.storage-backend-drawer .t-select')`, 'STORAGE_DRAWER_TIMEOUT')
  await click('.storage-backend-drawer .t-select', 'STORAGE_PROVIDER_CONTROL_MISSING')
  await waitFor(`[...document.querySelectorAll('.t-select-option')].some(el => el.getClientRects().length)`, 'STORAGE_PROVIDERS_TIMEOUT')
  const storageProviders = await evaluate(`[...document.querySelectorAll('.t-select-option')].filter(el => el.getClientRects().length).map(el => el.textContent.trim())`)
  requireCheck(storageProviders.length > 0, 'STORAGE_PROVIDERS_EMPTY')
  requireCheck(!storageProviders.some(value => /\bCOS\b|tencent|腾讯/i.test(value)), 'UNUSED_STORAGE_PROVIDER_VISIBLE')
  await screenshot('storage-provider-options')
  result('unused-storage-providers-hidden')
}

async function widgetChecks(brand) {
  phase = 'widget-loaders'
  await navigate(publicOnly ? '/login' : '/platform/knowledge-bases', publicOnly ? '.login-layout' : '.aside_box')
  const state = await evaluate(`(async () => {
    const urls = ['/weknora-widget.js', '/' + ${JSON.stringify(brand.productSlug)} + '-widget.js']
    const resources = await Promise.all(urls.map(async url => {
      const response = await fetch(url, { cache: 'no-store', redirect: 'error' })
      return { ok: response.ok, type: response.headers.get('content-type'), cache: response.headers.get('cache-control'), text: await response.text() }
    }))
    for (const url of urls) {
      await new Promise((resolve, reject) => {
        const script = document.createElement('script')
        script.src = url
        script.onload = resolve
        script.onerror = reject
        document.head.appendChild(script)
      })
    }
    const api = window[${JSON.stringify(brand.productName)}]
    return {
      assets: resources.every(item => item.ok && /javascript/.test(item.type) && /max-age=3600/.test(item.cache)),
      identical: resources[0].text === resources[1].text,
      resolved: resources.every(item => !/__BRAND_[A-Z_]+__/.test(item.text)),
      alias: !!api && api === window.WeKnora && typeof api.init === 'function',
    }
  })()`)
  requireCheck(state.assets && state.identical && state.resolved && state.alias, 'WIDGET_LOADER_CONTRACT_FAILED')
  result('new-and-legacy-widget-assets')
  const widget = await evaluate(`(() => {
    const api = window[${JSON.stringify(brand.productName)}]
    const widget = api.init({ channel: '00000000-0000-0000-0000-000000000000', token: 'branding-verification-invalid-token' })
    api.open()
    const iframe = [...document.querySelectorAll('iframe')].find(el => el.src.includes('/embed/00000000-0000-0000-0000-000000000000'))
    return { open: widget?.isOpen(), title: iframe?.title, origin: iframe ? new URL(iframe.src).origin : '', clean: iframe ? !new URL(iframe.src).hash && !new URL(iframe.src).search : false }
  })()`)
  requireCheck(widget.open && widget.title === brand.productName && widget.origin === base && widget.clean, 'WIDGET_BROWSER_INITIALIZATION_FAILED')
  await delay(500)
  await screenshot('widget-invalid-channel')
  await evaluate(`window[${JSON.stringify(brand.productName)}].destroy()`)
  const status = await evaluate(`fetch('/embed/00000000-0000-0000-0000-000000000000', { redirect: 'error' }).then(response => response.status)`)
  requireCheck([401, 403, 404].includes(status), 'INVALID_EMBED_CHANNEL_NOT_REJECTED')
  result('widget-browser-initialization-and-invalid-channel-rejection')
  if (!channelId) result('valid-embed-page', 'unverified', 'NO_TEST_CHANNEL')
}

async function prepareEmbedChannel(brand) {
  if (!prepareEmbed && !channelId) return
  phase = 'isolated-embed-channel'
  requireCheck(!publicOnly, 'EMBED_PREVIEW_REQUIRES_LOGIN')
  const prepared = await evaluate(`(async () => {
    const productName = ${JSON.stringify(brand.productName)}
    const productSlug = ${JSON.stringify(brand.productSlug)}
    const channelId = ${JSON.stringify(channelId)}
    const create = ${JSON.stringify(prepareEmbed)}
    const marker = 'managed-branding-verification-v1'
    const name = productName + ' Branding Verification'
    const api = async (url, body) => {
      const response = await fetch('/api/v1' + url, {
        method: body ? 'POST' : 'GET', redirect: 'error',
        headers: { Authorization: 'Bearer ' + localStorage.getItem('weknora_token'), 'Content-Type': 'application/json' },
        ...(body ? { body: JSON.stringify(body) } : {}),
      })
      if (!response.ok) throw new Error('TEST_ENTITY_API_FAILED')
      const result = await response.json()
      if (result.success === false) throw new Error('TEST_ENTITY_API_FAILED')
      return result.data
    }
    try {
      const bases = await api('/knowledge-bases')
      if (!Array.isArray(bases)) return { error: 'SMOKE_KB_LIST_INVALID' }
      const matches = bases.filter(item => item.name === 'local-smoke-stardust' || item.description === productSlug + '-local-smoke-v1')
      if (matches.length !== 1 || matches[0].name !== 'local-smoke-stardust' || matches[0].description !== productSlug + '-local-smoke-v1') return { error: 'SMOKE_KB_SCOPE_NOT_UNIQUE' }
      const knowledgeBaseId = matches[0].id
      const agents = await api('/agents')
      if (!Array.isArray(agents)) return { error: 'TEST_AGENT_LIST_INVALID' }
      const candidates = agents.filter(item => item.name === name || item.description === marker)
      if (candidates.length > 1) return { error: 'TEST_AGENT_NOT_UNIQUE' }
      let agent = candidates[0]
      if (!agent) {
        if (!create) return { error: 'TEST_AGENT_NOT_FOUND' }
        agent = await api('/agents', {
          name, description: marker, avatar: '',
          config: {
            agent_mode: 'quick-answer', kb_selection_mode: 'selected', knowledge_bases: [knowledgeBaseId],
            mcp_selection_mode: 'none', skills_selection_mode: 'none', web_search_enabled: false,
            web_fetch_enabled: false, memory_enabled: false, image_upload_enabled: false, audio_upload_enabled: false,
            question_suggestions: { starters: { enabled: false, mode: 'curated', count: 3, items: [] }, follow_ups: { enabled: false, mode: 'generated', count: 3 } },
          },
        })
      }
      const cfg = agent?.config
      if (agent?.name !== name || agent?.description !== marker || agent?.is_builtin || cfg?.kb_selection_mode !== 'selected' || cfg.knowledge_bases?.length !== 1 || cfg.knowledge_bases[0] !== knowledgeBaseId || cfg.agent_mode !== 'quick-answer' || cfg.mcp_selection_mode !== 'none' || cfg.skills_selection_mode !== 'none' || cfg.web_search_enabled || cfg.web_fetch_enabled || cfg.memory_enabled || cfg.question_suggestions?.starters?.enabled !== false || cfg.question_suggestions?.follow_ups?.enabled !== false) return { error: 'TEST_AGENT_SCOPE_MISMATCH' }
      const channels = await api('/agents/' + encodeURIComponent(agent.id) + '/embed-channels')
      if (!Array.isArray(channels)) return { error: 'TEST_CHANNEL_LIST_INVALID' }
      const matchingChannels = channels.filter(item => item.name === 'branding-verification-v1' || (channelId && item.id === channelId))
      if (matchingChannels.length > 1) return { error: 'TEST_CHANNEL_NOT_UNIQUE' }
      let channel = matchingChannels[0]
      if (!channel) {
        if (!create || channelId) return { error: 'TEST_CHANNEL_NOT_FOUND' }
        channel = await api('/agents/' + encodeURIComponent(agent.id) + '/embed-channels', {
          name: 'branding-verification-v1', enabled: true, allowed_origins: [location.origin],
          page_title: productName, welcome_message: productName + ' branding verification', primary_color: ${JSON.stringify(brand.colors.action)},
          header_title_mode: 'channel', show_suggested_questions: false, allow_web_search: false, allow_file_upload: false,
          rate_limit_per_minute: 30, rate_limit_per_day: 100, default_locale: 'en-US',
        })
      }
      if (channel?.name !== 'branding-verification-v1' || channel?.agent_id !== agent.id || !channel.enabled || channel.allowed_origins?.length !== 1 || channel.allowed_origins[0] !== location.origin || channel.page_title !== productName || channel.welcome_message !== productName + ' branding verification' || channel.show_suggested_questions !== false || channel.allow_web_search || channel.allow_file_upload || (channelId && channel.id !== channelId)) return { error: 'TEST_CHANNEL_SCOPE_MISMATCH' }
      return { id: channel.id }
    } catch { return { error: 'TEST_ENTITY_API_FAILED' } }
  })()`)
  requireCheck(prepared?.id, prepared?.error || 'TEST_CHANNEL_PREPARATION_FAILED')
  channelId = prepared.id
  result('isolated-embed-channel')
}

async function validEmbedCheck(brand) {
  if (!channelId) return
  phase = 'valid-embed-page'
  requireCheck(!publicOnly, 'EMBED_PREVIEW_REQUIRES_LOGIN')
  const preview = await evaluate(`(async () => {
    const response = await fetch('/api/v1/embed-channels/' + ${JSON.stringify(channelId)} + '/preview-session', {
      method: 'POST', headers: { Authorization: 'Bearer ' + localStorage.getItem('weknora_token'), 'Content-Type': 'application/json' }, body: '{}', redirect: 'error',
    })
    if (!response.ok) return null
    const body = await response.json()
    if (!body.success || !body.data?.session_token) return false
    const api = window[${JSON.stringify(brand.productName)}]
    api.init({ channel: ${JSON.stringify(channelId)}, token: body.data.session_token, width: 600, height: 760 })
    api.open()
    return true
  })()`)
  requireCheck(preview, 'EMBED_PREVIEW_TOKEN_UNAVAILABLE')
  const frameSelector = `iframe[src="${base}/embed/${channelId}"]`
  await waitFor(`!!document.querySelector(${JSON.stringify(frameSelector)})?.contentDocument?.querySelector('.embed-header')`, 'EMBED_CHAT_TIMEOUT')
  const state = await evaluate(`(() => {
    const iframe = document.querySelector(${JSON.stringify(frameSelector)})
    const doc = iframe.contentDocument
    const image = doc.querySelector('.embed-header__logo')
    const text = doc.querySelector('.embed-page').innerText
    return {
      title: doc.title === ${JSON.stringify(brand.productName)},
      welcome: text.includes(${JSON.stringify(brand.productName + ' branding verification')}),
      logo: image?.complete && image?.naturalWidth > 0 && new URL(image.src).pathname === ${JSON.stringify(brand.logoUrl)},
      clean: !/weknora|tencent|腾讯|powered\\s+by/i.test(text),
      privateUrl: !new URL(iframe.src).hash && !new URL(iframe.src).search,
    }
  })()`)
  requireCheck(state.title && state.welcome && state.logo && state.clean && state.privateUrl, 'VALID_EMBED_BRANDING_FAILED')
  await screenshot('valid-embed-page')
  await evaluate(`window[${JSON.stringify(brand.productName)}].destroy()`)
  result('valid-embed-page')
}

async function cleanup() {
  if (cdp) {
    await cdp.send('Browser.close').catch(() => {})
    cdp.socket.close()
  }
  if (browser && browser.exitCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(5000)])
    if (browser.exitCode === null) browser.kill()
  }
  if (profile) {
    const parent = await realpath(path.join(root, 'temp/branding-verification'))
    const target = await realpath(profile).catch(() => '')
    requireCheck(target && path.dirname(target) === parent && path.basename(target).startsWith('.profile-'), 'PROFILE_CLEANUP_PATH_REJECTED')
    await rm(target, { recursive: true, force: true, maxRetries: 10, retryDelay: 500 })
  }
}

async function run() {
  for (let index = 0; index < args.length; index++) {
    requireCheck(recognized.has(args[index]), 'UNKNOWN_ARGUMENT')
    if (args[index] === '--embed-channel-id') {
      requireCheck(args[index + 1] && !args[index + 1].startsWith('--'), 'EMBED_CHANNEL_ID_MISSING')
      index++
    }
  }
  requireCheck(!channelId || /^[0-9a-f-]{36}$/i.test(channelId), 'INVALID_EMBED_CHANNEL_ID')
  requireCheck(!(publicOnly && (prepareEmbed || channelId)), 'EMBED_PREVIEW_REQUIRES_LOGIN')
  const brand = JSON.parse(await readFile(path.join(root, 'branding/brand.json'), 'utf8'))
  const candidates = [
    process.env.BRANDING_BROWSER_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean)
  const executable = candidates.find(candidate => existsSync(candidate))
  requireCheck(executable, 'CHROME_OR_EDGE_NOT_FOUND')
  const outputRoot = path.join(root, 'temp/branding-verification')
  await mkdir(outputRoot, { recursive: true })
  const outputRelative = path.relative(await realpath(root), await realpath(outputRoot))
  requireCheck(outputRelative && !outputRelative.startsWith('..') && !path.isAbsolute(outputRelative), 'ARTIFACT_DIRECTORY_OUTSIDE_REPOSITORY')
  output = await mkdtemp(path.join(outputRoot, 'run-'))
  profile = await mkdtemp(path.join(outputRoot, '.profile-'))
  browser = spawn(executable, [
    '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-background-networking', '--disable-component-update',
    '--disable-sync', '--disable-features=Translate,OptimizationHints', '--window-size=1440,1000',
    'about:blank',
  ], { stdio: 'ignore', windowsHide: true })
  let spawnFailed = false
  browser.once('error', () => { spawnFailed = true })
  const portFile = path.join(profile, 'DevToolsActivePort')
  const deadline = Date.now() + 20000
  while (!existsSync(portFile) && Date.now() < deadline && !spawnFailed && browser.exitCode === null) await delay(100)
  requireCheck(existsSync(portFile), 'BROWSER_START_FAILED')
  const [port, browserPath] = (await readFile(portFile, 'utf8')).trim().split(/\r?\n/)
  requireCheck(/^\d+$/.test(port) && browserPath.startsWith('/devtools/browser/'), 'INVALID_DEBUG_ENDPOINT')
  cdp = await CDP.connect(`ws://127.0.0.1:${port}${browserPath}`)
  const target = await cdp.send('Target.createTarget', { url: 'about:blank' })
  session = (await cdp.send('Target.attachToTarget', { targetId: target.targetId, flatten: true })).sessionId
  await cdp.send('Page.enable', {}, session)
  await cdp.send('Runtime.enable', {}, session)
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, session)
  await navigate('/login', '.login-layout')
  for (const locale of ['zh-CN', 'en-US', 'ru-RU', 'ko-KR', 'ja-JP']) {
    for (const theme of ['light', 'dark']) {
      phase = `login-${locale.toLowerCase()}-${theme}`
      await preferences(locale, theme)
      await navigate('/login', '.login-layout input[autocomplete="email"]')
      const loginLabels = { 'zh-CN': '登录', 'en-US': 'Login', 'ru-RU': 'Вход', 'ko-KR': '로그인', 'ja-JP': 'ログイン' }
      requireCheck(await evaluate(`document.querySelector('.form-title')?.textContent.trim() === ${JSON.stringify(loginLabels[locale])}`), 'LOGIN_LOCALE_NOT_APPLIED')
      await brandingCheck(phase, brand, '.login-layout', { logo: true, title: true, theme })
      await screenshot(phase)
    }
  }
  const assets = await evaluate(`Promise.all(${JSON.stringify([...new Set([brand.logoUrl, brand.faviconUrl, brand.appleTouchIconUrl])])}.map(async url => {
    const response = await fetch(url, { cache: 'no-store', redirect: 'error' })
    return response.ok && /image\\//.test(response.headers.get('content-type')) && (await response.arrayBuffer()).byteLength > 0
  })).then(values => values.every(Boolean))`)
  requireCheck(assets, 'BRAND_ASSET_REQUEST_FAILED')
  const legacyIconRemoved = await evaluate(`fetch('/favicon.ico', { cache: 'no-store', redirect: 'error' }).then(response => !response.ok || !/image\\//.test(response.headers.get('content-type') || ''))`)
  requireCheck(legacyIconRemoved, 'LEGACY_FAVICON_IMAGE_STILL_SERVED')
  result('logo-and-favicon-resources')
  if (!publicOnly) {
    phase = 'ui-login'
    await login()
    phase = 'user-menu-help-links'
    await click('[data-guide="user-menu"]', 'USER_MENU_CONTROL_MISSING')
    await waitFor(`!!document.querySelector('.user-dropdown')`, 'USER_MENU_TIMEOUT')
    const hiddenHelp = await evaluate(`!document.querySelector('.user-dropdown .menu-item .t-icon-help-circle, .user-dropdown .menu-item .t-icon-logo-github')`)
    requireCheck(hiddenHelp, 'UPSTREAM_HELP_MENU_VISIBLE')
    result('upstream-help-menu-hidden')
    await click('[data-guide="user-menu"]', 'USER_MENU_CONTROL_MISSING')
    for (const theme of ['light', 'dark']) {
      phase = `sidebar-${theme}`
      await preferences('en-US', theme)
      await navigate('/platform/knowledge-bases', '.aside_box .logo_box img')
      await brandingCheck(phase, brand, '.aside_box', { logo: true, theme })
      await screenshot(phase)
      await click('.sidebar-toggle', 'SIDEBAR_COLLAPSE_CONTROL_MISSING')
      await waitFor(`!!document.querySelector('.aside_box--collapsed')`, 'SIDEBAR_COLLAPSE_FAILED')
      await screenshot(`sidebar-collapsed-${theme}`)
      await click('.sidebar-toggle-item', 'SIDEBAR_EXPAND_CONTROL_MISSING')
      await navigate('/platform/settings?section=general', '.settings-modal')
      await brandingCheck(`settings-${theme}`, brand, '.settings-modal', { theme })
      await screenshot(`settings-${theme}`)
    }
    await preferences('en-US', 'light')
    await providerChecks(brand)
  } else {
    result('authenticated-settings', 'unverified', 'PUBLIC_ONLY')
  }
  await prepareEmbedChannel(brand)
  await widgetChecks(brand)
  await validEmbedCheck(brand)
}

try {
  await run()
} catch (error) {
  result(phase, 'failed', error instanceof VerificationError ? error.message : 'EXECUTION_ERROR')
  if (cdp && output && phase !== 'ui-login') await screenshot('failure').catch(() => {})
} finally {
  await cleanup().catch(() => result('isolated-profile-cleanup', 'failed', 'CLEANUP_FAILED'))
  const failed = results.some(item => item.status === 'failed')
  const partial = results.some(item => item.status === 'unverified')
  const status = failed ? 'failed' : partial ? 'partial' : 'passed'
  if (output) {
    await writeFile(path.join(output, 'report.json'), JSON.stringify({ status, results, screenshots }, null, 2) + '\n', { flag: 'wx' })
    process.stdout.write(`ARTIFACTS ${path.relative(root, output).split(path.sep).join('/')}\n`)
  }
  process.stdout.write(`RESULT ${status.toUpperCase()}\n`)
  process.exitCode = failed ? 1 : partial ? 2 : 0
}
