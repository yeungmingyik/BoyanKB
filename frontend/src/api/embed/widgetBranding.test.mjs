import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const brand = JSON.parse(readFileSync(new URL('../../../../branding/brand.json', import.meta.url), 'utf8'))
const template = readFileSync(new URL('../../../public/weknora-widget.js', import.meta.url), 'utf8')
const values = {
  __BRAND_NAME__: brand.productName,
  __BRAND_SLUG__: brand.productSlug,
  __BRAND_PRIMARY_COLOR__: brand.colors.action,
}
const source = template.replace(/'(__BRAND_NAME__|__BRAND_SLUG__|__BRAND_PRIMARY_COLOR__)'/g, (_, key) => JSON.stringify(values[key]))

function element(tag) {
  return {
    tag,
    children: [],
    attributes: {},
    style: {},
    events: new Map(),
    contentWindow: tag === 'iframe' ? {
      messages: [],
      postMessage(message, origin) { this.messages.push({ message, origin }) },
    } : undefined,
    appendChild(child) { this.children.push(child); child.parentNode = this },
    removeChild(child) { this.children = this.children.filter((entry) => entry !== child); child.parentNode = null },
    setAttribute(name, value) { this.attributes[name] = value },
    getAttribute(name) { return this.attributes[name] ?? null },
    addEventListener(name, callback) { this.events.set(name, callback) },
  }
}

function fixture(fileName, attributes = {}, responses = []) {
  const body = element('body')
  const script = element('script')
  script.src = `https://embed.example.test/${fileName}?v=1#loader`
  script.attributes = attributes
  const listeners = new Set()
  const timers = new Map()
  const requests = []
  const window = {
    location: { origin: 'https://host.example.test', href: 'https://host.example.test/page' },
    addEventListener(name, listener) { if (name === 'message') listeners.add(listener) },
    removeEventListener(name, listener) { if (name === 'message') listeners.delete(listener) },
  }
  const document = { body, currentScript: script, createElement: element }
  const context = vm.createContext({
    window, document, URL, console,
    setTimeout(callback, delay) { const id = timers.size + 1; timers.set(id, { callback, delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    fetch: async (url, options) => {
      requests.push({ url, options })
      const data = responses.shift()
      assert.ok(data)
      return { ok: true, json: async () => data }
    },
  })
  vm.runInContext(source, context)
  return {
    window, document, context, timers, requests, listeners,
    api: window[brand.productName],
    iframe: () => body.children.find((entry) => entry.tag === 'div')?.children[0],
    launcher: () => body.children.find((entry) => entry.tag === 'button'),
    message(data, origin, sender) {
      for (const listener of listeners) listener({ data, origin, source: sender })
    },
  }
}

const settle = () => new Promise((resolve) => setImmediate(resolve))

for (const fileName of ['weknora-widget.js', `${brand.productSlug}-widget.js`]) {
  test(`${fileName} resolves its origin after asynchronous programmatic initialization`, () => {
    const f = fixture(fileName)
    f.document.currentScript = null
    const instance = f.api.init({ channel: 'channel one', token: 'test-token' })
    assert.equal(f.window.WeKnora, f.api)
    assert.equal(f.iframe().src, 'https://embed.example.test/embed/channel%20one')
    assert.equal(f.iframe().attributes.title, brand.productName)
    assert.match(f.launcher().style.cssText, new RegExp(`background:${brand.colors.action}`))
    assert.ok(f.iframe().attributes.sandbox.includes('allow-same-origin'))
    f.window.WeKnora.open()
    assert.equal(instance.isOpen(), true)
    f.api.close()
    assert.equal(instance.isOpen(), false)
  })
}

test('both loader addresses share one API and one auto-initialized widget', () => {
  const f = fixture('weknora-widget.js', { 'data-channel': 'channel', 'data-token': 'test-token' })
  const originalIframe = f.iframe()
  f.document.currentScript.src = `https://embed.example.test/${brand.productSlug}-widget.js`
  vm.runInContext(source, f.context)
  assert.equal(f.window[brand.productName], f.api)
  assert.equal(f.window.WeKnora, f.api)
  assert.equal(f.document.body.children.length, 2)
  assert.equal(f.iframe(), originalIframe)
  assert.equal(f.listeners.size, 1)
})

test('custom title, color, base URL and dimensions remain intact', () => {
  const f = fixture(`${brand.productSlug}-widget.js`)
  f.api.init({ channel: 'channel', token: 'test-token', title: 'Custom help', primaryColor: '#123456', baseUrl: 'https://custom.example.test/', width: 480, height: 720 })
  assert.equal(f.iframe().src, 'https://custom.example.test/embed/channel')
  assert.equal(f.iframe().attributes.title, 'Custom help')
  assert.match(f.launcher().style.cssText, /background:#123456/)
  assert.match(f.iframe().parentNode.style.cssText, /width:480px/)
  assert.match(f.iframe().parentNode.style.cssText, /height:720px/)
})

test('legacy bridge protocol retains channel, source and origin checks', async () => {
  const f = fixture(`${brand.productSlug}-widget.js`)
  const instance = f.api.init({ channel: 'channel', token: 'test-token' })
  const iframe = f.iframe()
  const ready = { source: 'weknora-embed', type: 'ready', channel_id: 'channel' }
  f.message(ready, 'https://untrusted.example.test', iframe.contentWindow)
  f.message(ready, 'https://embed.example.test', {})
  f.message({ ...ready, channel_id: 'another-channel' }, 'https://embed.example.test', iframe.contentWindow)
  assert.equal(instance.isReady(), false)
  f.message(ready, 'https://embed.example.test', iframe.contentWindow)
  assert.equal(instance.isReady(), true)
  f.message({ source: 'weknora-embed', type: 'bootstrap_request' }, 'https://embed.example.test', iframe.contentWindow)
  await settle()
  const sent = iframe.contentWindow.messages[0]
  assert.equal(sent.origin, 'https://embed.example.test')
  assert.equal(sent.message.source, 'weknora-host')
  assert.equal(sent.message.token, 'test-token')
  f.window.WeKnora.setContext({ page: '/guide' })
  assert.equal(iframe.contentWindow.messages.at(-1).message.type, 'set_context')
  f.api.openWithQuery('Example query')
  assert.equal(iframe.contentWindow.messages.at(-1).message.payload.query, 'Example query')
  f.api.destroy()
  assert.equal(f.listeners.size, 0)
  assert.equal(f.document.body.children.length, 0)
})

test('secure auto-init refreshes session tokens without placing them in the iframe URL', async () => {
  const endpoint = 'https://host.example.test/embed-token'
  const f = fixture(`${brand.productSlug}-widget.js`, {
    'data-channel': 'channel',
    'data-token-endpoint': endpoint,
  }, [
    { token: 'test-session-1', expiresIn: 100 },
    { data: { session_token: 'test-session-2', expires_in: 100 } },
  ])
  const iframe = f.iframe()
  iframe.events.get('load')()
  await settle()
  assert.equal(f.requests[0].url, endpoint)
  assert.equal(f.requests[0].options.credentials, 'include')
  assert.equal(iframe.src, 'https://embed.example.test/embed/channel')
  assert.equal(iframe.contentWindow.messages.at(-1).message.token, 'test-session-1')
  const timer = [...f.timers.values()][0]
  assert.equal(timer.delay, 80000)
  timer.callback()
  await settle()
  assert.equal(f.requests.length, 2)
  assert.equal(iframe.contentWindow.messages.at(-1).message.token, 'test-session-2')
})
