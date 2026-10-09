import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { loadFaviconAssets } from './check-branding.mjs'

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
const layoutObservations = []
const faviconRequests = new Map()
let faviconAssets = []
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
    this.observers = new Map()
    socket.addEventListener('message', (event) => {
      let message
      try { message = JSON.parse(event.data) } catch { return }
      if (message.method) {
        const key = `${message.sessionId || ''}:${message.method}`
        for (const observe of this.observers.get(key) || []) observe(message.params)
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
      this.observers.clear()
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

  observe(method, sessionId, listener) {
    const key = `${sessionId || ''}:${method}`
    const listeners = this.observers.get(key) || []
    listeners.push(listener)
    this.observers.set(key, listeners)
  }
}

async function observeFavicons() {
  const paths = new Set(faviconAssets.flatMap(icon => icon.format === 'ico' ? [icon.href, icon.stableUrl] : [icon.href]))
  cdp.observe('Network.requestWillBeSent', session, event => {
    let url
    try { url = new URL(event.request.url) } catch { return }
    if (url.origin !== base || !paths.has(url.pathname)) return
    faviconRequests.set(event.requestId, { path: url.pathname, resourceType: event.type || '', initiatorType: event.initiator?.type || '' })
  })
  cdp.observe('Network.responseReceived', session, event => {
    const request = faviconRequests.get(event.requestId)
    if (request) Object.assign(request, { status: event.response.status, mimeType: event.response.mimeType, fromDiskCache: Boolean(event.response.fromDiskCache) })
  })
  cdp.observe('Network.loadingFailed', session, event => {
    const request = faviconRequests.get(event.requestId)
    if (request) request.failed = true
  })
  await cdp.send('Network.enable', {}, session)
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true }, session)
}

async function faviconResourceChecks() {
  const expected = faviconAssets.flatMap(icon => [icon.stableUrl, icon.href].map(url => ({ url, type: icon.type, format: icon.format, sha256: icon.sha256 })))
  const checked = await evaluate(`Promise.all(${JSON.stringify(expected)}.map(async icon => {
    const response = await fetch(icon.url, { cache: 'no-store', redirect: 'error' })
    const mime = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase()
    if (!response.ok || !(mime === icon.type || icon.format === 'ico' && mime === 'image/vnd.microsoft.icon')) return { url: icon.url, ok: false }
    const data = new Uint8Array(await response.arrayBuffer())
    const view = new DataView(data.buffer)
    let signature = true
    if (icon.format === 'png') signature = data.length >= 33 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => data[index] === value) && view.getUint32(16) === 32 && view.getUint32(20) === 32
    if (icon.format === 'ico') {
      signature = data.length >= 54 && view.getUint16(0, true) === 0 && view.getUint16(2, true) === 1 && view.getUint16(4, true) === 3
      if (signature) {
        const sizes = []
        for (let index = 0; index < 3; index++) {
          const offset = 6 + index * 16
          const width = data[offset] || 256
          const length = view.getUint32(offset + 8, true)
          const start = view.getUint32(offset + 12, true)
          signature = signature && width === (data[offset + 1] || 256) && length > 0 && start >= 54 && start + length <= data.length
          sizes.push(width)
        }
        signature = signature && sizes.sort((a, b) => a - b).join(',') === '16,32,48'
      }
    }
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(value => value.toString(16).padStart(2, '0')).join('')
    return { url: icon.url, ok: signature && hash === icon.sha256 }
  }))`)
  requireCheck(Array.isArray(checked) && checked.length === expected.length && checked.every(icon => icon.ok), 'FAVICON_BYTES_SIGNATURE_OR_TYPE_FAILED')
  result('favicon-hashed-and-stable-resources')
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
  if (url.pathname.startsWith('/platform/')) await dismissGuides()
}

async function click(selector, code) {
  const position = await evaluate(`(() => {
    const element = [...document.querySelectorAll(${JSON.stringify(selector)})].find(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
    if (!element) return null
    element.scrollIntoView({ block: 'center', inline: 'nearest' })
    const bounds = element.getBoundingClientRect()
    return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 }
  })()`)
  requireCheck(position, code)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...position }, session)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...position }, session)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...position }, session)
  await delay(300)
}

async function dismissGuides() {
  const closeSelector = '.guide__close, .driver-popover-close-btn'
  const deadline = Date.now() + 12000
  let quietSince = Date.now()
  let dismissed = 0
  while (Date.now() < deadline) {
    const state = await evaluate(`(() => {
      const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden'
      return {
        close: [...document.querySelectorAll(${JSON.stringify(closeSelector)})].some(visible),
        overlay: [...document.querySelectorAll('.guide[role="dialog"], .driver-overlay')].some(visible),
      }
    })()`)
    if (state.close) {
      await click(closeSelector, 'GUIDE_CLOSE_CONTROL_MISSING')
      dismissed++
      quietSince = Date.now()
    } else if (state.overlay) {
      quietSince = Date.now()
    } else if (Date.now() - quietSince >= 1200) {
      if (dismissed) result(`${phase}-guides-dismissed`, 'passed', String(dismissed))
      return
    }
    await delay(150)
  }
  throw new VerificationError('GUIDE_DISMISS_TIMEOUT')
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

async function setViewport(width, height, mobile = false) {
  await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile }, session)
  await delay(150)
}

async function loginLayoutCheck(name, desktop) {
  const metrics = await evaluate(`(() => {
    const selectors = ['html', 'body', '#app', '.login-layout', '.form-section', '.form-panel', '.form-card']
    const elements = selectors.map(selector => {
      const element = document.querySelector(selector)
      if (!element) return { selector, missing: true }
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return { selector, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, overflowY: style.overflowY }
    })
    return { width: innerWidth, height: innerHeight, scrollX, scrollY, scale: visualViewport?.scale || 1, elements }
  })()`)
  layoutObservations.push({ name, ...metrics })
  requireCheck(metrics.elements.every(element => !element.missing), 'LOGIN_LAYOUT_ELEMENT_MISSING')
  requireCheck(metrics.elements.every(element => element.scrollWidth <= element.clientWidth + 1 && element.left >= -1 && element.right <= metrics.width + 1), 'LOGIN_HORIZONTAL_OVERFLOW')
  if (desktop) {
    requireCheck(metrics.elements.every(element => element.scrollHeight <= element.clientHeight + 1), 'DESKTOP_LOGIN_VERTICAL_OVERFLOW')
    const card = metrics.elements.find(element => element.selector === '.form-card')
    requireCheck(card.top >= -1 && card.bottom <= metrics.height + 1, 'DESKTOP_LOGIN_CARD_OUTSIDE_VIEWPORT')
  } else {
    requireCheck(metrics.elements.every(element => !['hidden', 'clip'].includes(element.overflowY) || element.scrollHeight <= element.clientHeight + 1), 'MOBILE_LOGIN_CONTENT_CLIPPED')
  }
  result(name, 'passed', `${metrics.width}x${metrics.height}`)
}

async function languageMenuCheck(name, capture) {
  await click('.language-switch button', 'LANGUAGE_MENU_BUTTON_MISSING')
  await waitFor(`!!document.querySelector('.language-option.active .check-icon')`, 'LANGUAGE_MENU_TIMEOUT')
  const colors = await evaluate(`(() => {
    const active = document.querySelector('.language-option.active')
    const check = active.querySelector('.check-icon')
    const probe = document.createElement('span')
    probe.style.cssText = 'position:fixed;visibility:hidden;pointer-events:none'
    document.documentElement.appendChild(probe)
    const resolved = (token, property = 'color') => {
      probe.style.setProperty(property, 'var(' + token + ')')
      return getComputedStyle(probe).getPropertyValue(property)
    }
    const expectedText = resolved('--app-selection-text')
    const expectedBackground = resolved('--app-selection-bg', 'background-color')
    const expectedCheck = resolved('--td-text-color-brand')
    const success = ['--td-success-color', '--td-success-color-5', '--td-success-color-6'].map(token => resolved(token))
    const successBackground = resolved('--td-success-color-light', 'background-color')
    const state = { color: getComputedStyle(active).color, background: getComputedStyle(active).backgroundColor, check: getComputedStyle(check).color, expectedText, expectedBackground, expectedCheck, success, successBackground }
    probe.remove()
    return state
  })()`)
  layoutObservations.push({ name, colors })
  requireCheck(colors.color === colors.expectedText && colors.background === colors.expectedBackground && colors.check === colors.expectedCheck, 'LANGUAGE_MENU_BRAND_COLOR_MISMATCH')
  requireCheck(!colors.success.includes(colors.color) && !colors.success.includes(colors.check) && colors.background !== colors.successBackground, 'LANGUAGE_MENU_SUCCESS_GREEN_REMAINED')
  if (capture) await screenshot(name)
  await click('.language-switch button', 'LANGUAGE_MENU_BUTTON_MISSING')
  await waitFor(`!document.querySelector('.language-dropdown')`, 'LANGUAGE_MENU_CLOSE_FAILED')
  result(name)
}

async function controlReachability(selector, name) {
  let state
  for (let attempt = 0; attempt < 10; attempt++) {
    state = await evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)})
      if (!element || !element.getClientRects().length) return null
      const rect = element.getBoundingClientRect()
      const center = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      const target = document.elementFromPoint(center.x, center.y)
      return { width: innerWidth, height: innerHeight, top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, scrollY, reachable: rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth && !!target && (target === element || element.contains(target)) }
    })()`)
    requireCheck(state, 'LOGIN_CONTROL_MISSING')
    if (state.reachable) break
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: Math.round(state.width / 2), y: Math.round(state.height * 0.65), deltaX: 0, deltaY: (state.top < 0 ? -1 : 1) * Math.round(state.height * 0.7) }, session)
    await delay(220)
  }
  layoutObservations.push({ name, control: state })
  requireCheck(state?.reachable, 'LOGIN_CONTROL_UNREACHABLE_BY_SCROLL')
  result(name)
}

async function responsiveCase(name, check, captureFailure = true) {
  phase = name
  try {
    await check()
    return true
  } catch (error) {
    result(phase, 'failed', error instanceof VerificationError ? error.message : 'EXECUTION_ERROR')
    if (captureFailure) await screenshot(`${name}-failure`).catch(() => {})
    return false
  }
}

async function responsiveLoginChecks(brand) {
  const locales = ['zh-CN', 'en-US', 'ru-RU', 'ko-KR', 'ja-JP']
  const labels = { 'zh-CN': '登录', 'en-US': 'Login', 'ru-RU': 'Вход', 'ko-KR': '로그인', 'ja-JP': 'ログイン' }
  try {
    for (const [width, height] of [[1366, 680], [1440, 900]]) {
      await setViewport(width, height)
      for (const locale of locales) {
        for (const theme of ['light', 'dark']) {
          const name = `login-${locale.toLowerCase()}-${theme}-${width}x${height}`
          const ready = await responsiveCase(`${name}-navigation`, async () => {
            await preferences(locale, theme)
            await navigate('/login', '.login-layout input[autocomplete="email"]')
            requireCheck(await evaluate(`document.querySelector('.form-title')?.textContent.trim() === ${JSON.stringify(labels[locale])}`), 'LOGIN_LOCALE_NOT_APPLIED')
          })
          if (!ready) continue
          await responsiveCase(name, async () => {
            await brandingCheck(phase, brand, '.login-layout', { logo: true, title: true, theme })
            await loginLayoutCheck(`${phase}-layout`, true)
            await screenshot(phase)
          })
          if (width === 1366) await responsiveCase(`language-menu-${locale.toLowerCase()}-${theme}`, () => languageMenuCheck(`language-menu-${locale.toLowerCase()}-${theme}`, locale === 'en-US'))
        }
      }
    }
    await setViewport(390, 640, true)
    for (const locale of locales) {
      for (const theme of ['light', 'dark']) {
        await responsiveCase(`mobile-login-${locale.toLowerCase()}-${theme}`, async () => {
          await preferences(locale, theme)
          await navigate('/login', '.login-layout input[autocomplete="current-password"]')
          await loginLayoutCheck(`${phase}-layout`, false)
          await controlReachability('.submit-button', `${phase}-submit-reachable`)
          if (locale === 'en-US') await screenshot(phase)
          if (!await evaluate(`!!document.querySelector('.register-cta__button')`)) {
            result(`mobile-register-${locale.toLowerCase()}-${theme}`, 'not-applicable', 'PUBLIC_REGISTRATION_DISABLED')
            return
          }
          await controlReachability('.register-cta__button', `${phase}-register-reachable`)
          await click('.register-cta__button', 'REGISTER_MODE_BUTTON_MISSING')
          await waitFor(`!!document.querySelector('input[autocomplete="new-password"]')`, 'REGISTER_FORM_TIMEOUT')
          phase = `mobile-register-${locale.toLowerCase()}-${theme}`
          await loginLayoutCheck(`${phase}-layout`, false)
          await controlReachability('.submit-button', `${phase}-submit-reachable`)
          await controlReachability('.form-footer .link-button', `${phase}-return-reachable`)
          if (locale === 'en-US') await screenshot(phase)
        })
      }
    }
  } catch (error) {
    await screenshot('responsive-login-failure').catch(() => {})
    throw error
  } finally {
    await setViewport(1440, 1000)
    await preferences('en-US', 'light')
    await navigate('/login', '.login-layout input[autocomplete="current-password"]')
  }
}

async function sidebarMaskCheck(theme) {
  const icon = await evaluate(`(async () => {
    const element = document.querySelector('.menu_item_active .resource-icon')
    if (!element) return null
    const style = getComputedStyle(element)
    const mask = style.maskImage
    let source = mask.startsWith('url(') && mask.endsWith(')') ? mask.slice(4, -1).trim() : ''
    if ((source.startsWith('"') && source.endsWith('"')) || (source.startsWith("'") && source.endsWith("'"))) source = source.slice(1, -1)
    const probe = document.createElement('span')
    probe.style.cssText = 'position:fixed;visibility:hidden;pointer-events:none;color:var(--td-text-color-brand)'
    document.documentElement.appendChild(probe)
    const expectedColor = getComputedStyle(probe).color
    probe.remove()
    let loaded = false
    let sourceType = ''
    if (source) {
      const url = new URL(source, location.href)
      sourceType = url.protocol
      if (url.protocol === 'data:' || url.origin === location.origin) {
        loaded = await new Promise(resolve => {
          const image = new Image()
          const timer = setTimeout(() => resolve(false), 5000)
          image.onload = () => { clearTimeout(timer); resolve(image.naturalWidth > 0 && image.naturalHeight > 0) }
          image.onerror = () => { clearTimeout(timer); resolve(false) }
          image.src = url.href
        })
      }
    }
    return { color: style.color, background: style.backgroundColor, expectedColor, maskSet: Boolean(source), sourceType, loaded }
  })()`)
  layoutObservations.push({ name: `sidebar-mask-${theme}`, icon })
  requireCheck(icon?.maskSet && icon.loaded, 'SIDEBAR_ACTIVE_MASK_NOT_LOADED')
  requireCheck(icon.color === icon.expectedColor && icon.background === icon.expectedColor, 'SIDEBAR_ACTIVE_ICON_COLOR_MISMATCH')
  result(`sidebar-mask-${theme}`)
}

async function brandingCheck(name, brand, selector, { logo = false, title = false, theme } = {}) {
  const state = await evaluate(`(() => {
    const scope = document.querySelector(${JSON.stringify(selector)})
    if (!scope) return null
    const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
    const text = (scope.innerText || '').replace(/\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b/gi, '')
    const links = [...document.querySelectorAll('a[href]')].filter(visible)
    const logos = [...scope.querySelectorAll('img')].filter(el => visible(el) && new URL(el.src).pathname === ${JSON.stringify(brand.logoUrl)})
    return {
      oldBrand: /weknora|tencent|腾讯/i.test(text),
      poweredBy: /powered\\s+by/i.test(text),
      upstreamLinks: links.some(el => /github\\.com\\/(?:tencent|wechatopenai)\\/weknora|weknora\\.weixin\\.qq\\.com|weknora\\.com/i.test(el.href)),
      logo: logos.some(el => el.complete && el.naturalWidth > 0 && el.alt === ${JSON.stringify(brand.productName)}),
      title: document.title.includes(${JSON.stringify(brand.productName)}),
      theme: document.documentElement.getAttribute('theme-mode'),
      favicon: (() => {
        const links = [...document.querySelectorAll('link[rel~="icon"]')]
        const expected = ${JSON.stringify(faviconAssets.map(({ href, rel, type, sizes }) => ({ href, rel, type, sizes })))}
        return links.length === expected.length && expected.every(icon => links.some(el => new URL(el.href).pathname === icon.href && el.rel === icon.rel && el.type === icon.type && el.sizes.value === icon.sizes))
      })(),
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
  await responsiveCase('login-guide-dismissal', dismissGuides)
}

async function modelProviderChecks(brand) {
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
}

async function storageProviderChecks(brand) {
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
    const name = '品牌验收（测试专用）'
    const acceptedNames = [name, productName + ' Branding Verification']
    const api = async (url, body, method) => {
      const response = await fetch('/api/v1' + url, {
        method: method || (body ? 'POST' : 'GET'), redirect: 'error',
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
      const candidates = agents.filter(item => acceptedNames.includes(item.name) || item.description === marker)
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
      if (!acceptedNames.includes(agent?.name) || agent?.description !== marker || agent?.is_builtin || cfg?.kb_selection_mode !== 'selected' || cfg.knowledge_bases?.length !== 1 || cfg.knowledge_bases[0] !== knowledgeBaseId || cfg.agent_mode !== 'quick-answer' || cfg.mcp_selection_mode !== 'none' || cfg.skills_selection_mode !== 'none' || cfg.web_search_enabled || cfg.web_fetch_enabled || cfg.memory_enabled || cfg.question_suggestions?.starters?.enabled !== false || cfg.question_suggestions?.follow_ups?.enabled !== false) return { error: 'TEST_AGENT_SCOPE_MISMATCH' }
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
      const validChannelScope = item => item?.name === 'branding-verification-v1' && item?.agent_id === agent.id && item.enabled && item.allowed_origins?.length === 1 && item.allowed_origins[0] === location.origin && item.page_title === productName && item.welcome_message === productName + ' branding verification' && item.primary_color === ${JSON.stringify(brand.colors.action)} && item.header_title_mode === 'channel' && item.rate_limit_per_minute === 30 && item.rate_limit_per_day === 100 && item.default_locale === 'en-US' && !item.allow_web_search && !item.allow_file_upload && !item.webhook_url && !item.has_webhook_secret && (!channelId || item.id === channelId)
      if (!validChannelScope(channel)) return { error: 'TEST_CHANNEL_SCOPE_MISMATCH' }
      let suggestionsRestored = false
      if (channel.show_suggested_questions !== false) {
        if (!create || channel.show_suggested_questions !== true) return { error: 'TEST_CHANNEL_SUGGESTIONS_NOT_DISABLED' }
        await api('/embed-channels/' + encodeURIComponent(channel.id), {
          name: channel.name, enabled: channel.enabled, allowed_origins: channel.allowed_origins,
          welcome_message: channel.welcome_message, page_title: channel.page_title, primary_color: channel.primary_color,
          header_title_mode: channel.header_title_mode, widget_position: channel.widget_position,
          rate_limit_per_minute: channel.rate_limit_per_minute, rate_limit_per_day: channel.rate_limit_per_day,
          default_locale: channel.default_locale, show_suggested_questions: false, allow_web_search: false, allow_file_upload: false,
        }, 'PUT')
        const reread = await api('/agents/' + encodeURIComponent(agent.id) + '/embed-channels')
        channel = reread.find(item => item.id === channel.id)
        suggestionsRestored = true
      }
      if (!validChannelScope(channel) || channel.show_suggested_questions !== false) return { error: 'TEST_CHANNEL_SCOPE_MISMATCH' }
      return { id: channel.id, suggestionsRestored }
    } catch { return { error: 'TEST_ENTITY_API_FAILED' } }
  })()`)
  requireCheck(prepared?.id, prepared?.error || 'TEST_CHANNEL_PREPARATION_FAILED')
  channelId = prepared.id
  if (prepared.suggestionsRestored) result('test-channel-suggestions-setting', 'passed', 'RESTORED_USING_UPDATE_API')
  result('isolated-embed-channel', 'passed', channelId)
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
      favicon: (() => {
        const links = [...doc.querySelectorAll('link[rel~="icon"]')]
        const expected = ${JSON.stringify(faviconAssets.map(({ href, rel, type, sizes }) => ({ href, rel, type, sizes })))}
        return links.length === expected.length && expected.every(icon => links.some(el => new URL(el.href).pathname === icon.href && el.rel === icon.rel && el.type === icon.type && el.sizes.value === icon.sizes))
      })(),
    }
  })()`)
  requireCheck(state.title && state.welcome && state.logo && state.clean && state.privateUrl && state.favicon, 'VALID_EMBED_BRANDING_FAILED')
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
  faviconAssets = await loadFaviconAssets(brand)
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
  await observeFavicons()
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, session)
  await navigate('/login', '.login-layout')
  await responsiveLoginChecks(brand)
  await responsiveCase('logo-resources', async () => {
    const assets = await evaluate(`Promise.all(${JSON.stringify([...new Set([brand.logoUrl, brand.appleTouchIconUrl])])}.map(async url => {
      const response = await fetch(url, { cache: 'no-store', redirect: 'error' })
      return response.ok && /image\\//.test(response.headers.get('content-type')) && (await response.arrayBuffer()).byteLength > 0
    })).then(values => values.every(Boolean))`)
    requireCheck(assets, 'BRAND_ASSET_REQUEST_FAILED')
    result('logo-resources')
  })
  await responsiveCase('favicon-resources', faviconResourceChecks)
  if (!publicOnly) {
    phase = 'ui-login'
    await login()
    await responsiveCase('new-chat-favicon', async () => {
      await navigate('/platform/creatChat', '.aside_box')
      await brandingCheck(phase, brand, '.aside_box', { logo: true, title: true })
      await screenshot(phase)
    })
    await responsiveCase('memory-export-filename', async () => {
      const exportBranded = await evaluate(`(async () => {
        const response = await fetch('/api/v1/memory/export', {
          headers: { Authorization: 'Bearer ' + localStorage.getItem('weknora_token') }, redirect: 'error',
        })
        const disposition = response.headers.get('content-disposition') || ''
        const match = /(?:^|;)\\s*filename=(?:"([^"]+)"|([^;\\s]+))/i.exec(disposition)
        if (response.body) await response.body.cancel()
        return response.ok && (match?.[1] || match?.[2]) === ${JSON.stringify(brand.productSlug + '-memories.json')}
      })()`)
      requireCheck(exportBranded, 'MEMORY_EXPORT_FILENAME_NOT_BRANDED')
      result('memory-export-filename')
    })
    await responsiveCase('user-menu-help-links', async () => {
      await navigate('/platform/creatChat', '.aside_box')
      await click('[data-guide="user-menu"]', 'USER_MENU_CONTROL_MISSING')
      await waitFor(`!!document.querySelector('.user-dropdown')`, 'USER_MENU_TIMEOUT')
      const hiddenHelp = await evaluate(`!document.querySelector('.user-dropdown .menu-item .t-icon-help-circle, .user-dropdown .menu-item .t-icon-logo-github')`)
      requireCheck(hiddenHelp, 'UPSTREAM_HELP_MENU_VISIBLE')
      result('upstream-help-menu-hidden')
      await click('[data-guide="user-menu"]', 'USER_MENU_CONTROL_MISSING')
    })
    for (const theme of ['light', 'dark']) {
      const ready = await responsiveCase(`sidebar-${theme}-navigation`, async () => {
        await preferences('en-US', theme)
        await navigate('/platform/knowledge-bases', '.aside_box .logo_box img')
      })
      if (ready) {
        await responsiveCase(`sidebar-${theme}`, async () => {
          await brandingCheck(phase, brand, '.aside_box', { logo: true, theme })
          await screenshot(phase)
        })
        await responsiveCase(`sidebar-mask-${theme}`, () => sidebarMaskCheck(theme), false)
        await responsiveCase(`sidebar-collapse-${theme}`, async () => {
          await click('.sidebar-toggle', 'SIDEBAR_COLLAPSE_CONTROL_MISSING')
          await waitFor(`!!document.querySelector('.aside_box--collapsed')`, 'SIDEBAR_COLLAPSE_FAILED')
          await screenshot(`sidebar-collapsed-${theme}`)
          await click('.sidebar-toggle-item', 'SIDEBAR_EXPAND_CONTROL_MISSING')
          await waitFor(`!document.querySelector('.aside_box--collapsed')`, 'SIDEBAR_EXPAND_FAILED')
          result(`sidebar-collapse-${theme}`)
        })
      }
      await responsiveCase(`settings-${theme}`, async () => {
        await preferences('en-US', theme)
        await navigate('/platform/settings?section=general', '.settings-modal')
        await brandingCheck(phase, brand, '.settings-modal', { theme })
        await screenshot(phase)
      })
    }
    await preferences('en-US', 'light')
    await responsiveCase('model-providers', () => modelProviderChecks(brand))
    await responsiveCase('storage-providers', () => storageProviderChecks(brand))
  } else {
    result('authenticated-settings', 'unverified', 'PUBLIC_ONLY')
  }
  const embedReady = await responsiveCase('isolated-embed-channel', () => prepareEmbedChannel(brand))
  const widgetReady = await responsiveCase('widget-loaders', () => widgetChecks(brand))
  if (embedReady && widgetReady) await responsiveCase('valid-embed-page', () => validEmbedCheck(brand))
  else if (prepareEmbed || channelId) result('valid-embed-page', 'unverified', 'EMBED_PREPARATION_OR_WIDGET_FAILED')
}

try {
  await run()
} catch (error) {
  result(phase, 'failed', error instanceof VerificationError ? error.message : 'EXECUTION_ERROR')
  if (cdp && output && phase !== 'ui-login') await screenshot('failure').catch(() => {})
} finally {
  const faviconNetwork = [...faviconRequests.values()].map(request => ({ ...request, automatic: request.initiatorType !== 'script' && !['Fetch', 'XHR'].includes(request.resourceType) }))
  const automaticFaviconRequests = faviconNetwork.filter(request => request.automatic)
  const faviconObservation = { automaticRequestObserved: automaticFaviconRequests.length > 0, requests: faviconNetwork, browserChromeAppearance: 'not-verified' }
  process.stdout.write(`OBSERVATION favicon-browser-network ${automaticFaviconRequests.length ? 'AUTOMATIC_REQUEST_RECORDED' : 'NO_AUTOMATIC_REQUEST_OBSERVED'}; browser chrome appearance not verified\n`)
  await cleanup().catch(() => result('isolated-profile-cleanup', 'failed', 'CLEANUP_FAILED'))
  const failed = results.some(item => item.status === 'failed')
  const partial = results.some(item => item.status === 'unverified')
  const status = failed ? 'failed' : partial ? 'partial' : 'passed'
  if (output) {
    await writeFile(path.join(output, 'report.json'), JSON.stringify({ status, results, screenshots, faviconObservation, layoutObservations, browserZoom200Percent: 'not-verified' }, null, 2) + '\n', { flag: 'wx' })
    process.stdout.write(`ARTIFACTS ${path.relative(root, output).split(path.sep).join('/')}\n`)
  }
  process.stdout.write(`RESULT ${status.toUpperCase()}\n`)
  process.exitCode = failed ? 1 : partial ? 2 : 0
}
