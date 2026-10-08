import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { checkDeployment, validateAllowlist } from './check-branding.mjs'

const entry = { file: 'frontend/src/api/sample.ts', text: "const audience = 'weknora'", category: 'protocol_id', count: 1 }
const finding = { file: entry.file, text: entry.text, line: 7 }
const channelId = '00000000-0000-4000-8000-000000000001'

test('exceptions only approve exact file, text and occurrence count', () => {
  assert.equal(validateAllowlist([finding], [entry]).unknown.length, 0)
  assert.equal(validateAllowlist([finding, { ...finding, line: 8 }], [entry]).unknown.length, 1)
  assert.equal(validateAllowlist([{ ...finding, file: 'frontend/src/views/Login.vue' }], [entry]).unknown.length, 1)
  assert.equal(validateAllowlist([{ ...finding, text: "const title = 'WeKnora'" }], [entry]).unknown.length, 1)
})

test('removed occurrences make exceptions stale', () => {
  assert.equal(validateAllowlist([], [entry]).stale.length, 1)
  assert.equal(validateAllowlist([finding], [{ ...entry, count: 2 }]).stale.length, 1)
})

test('wildcards, duplicate entries and unknown categories are rejected', () => {
  assert.throws(() => validateAllowlist([], [entry, entry]), /Duplicate/)
  assert.throws(() => validateAllowlist([], [{ ...entry, category: 'anything' }]), /Invalid/)
  assert.throws(() => validateAllowlist([], [{ ...entry, file: '../frontend/src/test.ts' }]), /Invalid/)
  assert.throws(() => validateAllowlist([], [{ ...entry, count: '*' }]), /Invalid/)
})

async function deploymentFixture(t, override = {}) {
  const brand = JSON.parse(await readFile(new URL('../branding/brand.json', import.meta.url), 'utf8'))
  const logo = await readFile(new URL('../branding/logo.svg', import.meta.url))
  const png = await readFile(new URL('../branding/logo.png', import.meta.url))
  const faviconPng = await readFile(new URL('../branding/favicon-32.png', import.meta.url))
  const faviconIco = await readFile(new URL('../branding/favicon.ico', import.meta.url))
  const icons = [
    { url: brand.faviconUrl, data: logo, rel: 'icon', type: 'image/svg+xml', sizes: 'any' },
    { url: brand.faviconPngUrl, data: faviconPng, rel: 'icon', type: 'image/png', sizes: '32x32' },
    { url: brand.faviconIcoUrl, data: faviconIco, rel: 'shortcut icon', type: 'image/x-icon', sizes: '16x16 32x32 48x48' },
  ].map(icon => ({ ...icon, href: icon.url.replace(/\.(svg|png|ico)$/, `-${createHash('sha256').update(icon.data).digest('hex').slice(0, 12)}.$1`) }))
  const license = await readFile(new URL('../LICENSE', import.meta.url))
  const template = await readFile(new URL('../frontend/public/weknora-widget.js', import.meta.url), 'utf8')
  const tokens = { __BRAND_NAME__: brand.productName, __BRAND_SLUG__: brand.productSlug, __BRAND_PRIMARY_COLOR__: brand.colors.action, __BRAND_LOGO_URL__: brand.logoUrl }
  const widget = template.replace(/__BRAND_[A-Z_]+__/g, token => JSON.stringify(tokens[token]).slice(1, -1).replace(/'/g, "\\'").replace(/</g, '\\u003c'))
  const html = `<html><head><title>${brand.productName}</title><meta name="description" content="${brand.productName} ${brand.description}">${icons.map(icon => `<link rel="${icon.rel}" type="${icon.type}" sizes="${icon.sizes}" href="${icon.href}">`).join('')}<link rel="apple-touch-icon" href="${brand.appleTouchIconUrl}"></head></html>`
  const routes = new Map([
    ['/', ['text/html', html]], ['/login', ['text/html', html]], ['/platform/creatChat', ['text/html', html]],
    ['/embed/branding-check', ['text/plain', 'Forbidden', 403]],
    [`/embed/${channelId}`, ['text/html', html, 200, "frame-ancestors 'self'"]],
    [brand.logoUrl, ['image/svg+xml', logo]], [brand.faviconUrl, ['image/svg+xml', logo]],
    [brand.appleTouchIconUrl, ['image/png', png]],
    ...icons.flatMap(icon => [[icon.url, [icon.type, icon.data]], [icon.href, [icon.type, icon.data]]]),
    [`/${brand.productSlug}-widget.js`, ['application/javascript', widget]],
    ['/weknora-widget.js', ['application/javascript', widget]], ['/licenses/LICENSE', ['text/plain', license]],
    ...Object.entries(override).map(([url, route]) => [url, typeof route === 'function' ? route(html) : route]),
  ])
  const server = createServer((req, res) => {
    const route = routes.get(req.url)
    const headers = { 'Content-Type': route?.[0] || 'text/plain' }
    if (route?.[3]) headers['Content-Security-Policy'] = route[3]
    res.writeHead(route ? route[2] || 200 : 404, headers)
    res.end(route?.[1] || 'missing')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve())))
  return { brand, url: `http://127.0.0.1:${server.address().port}` }
}

test('deployment checks accept matching initial HTML, exact assets, both widgets and license', async t => {
  const fixture = await deploymentFixture(t)
  await checkDeployment(fixture.url, fixture.brand)
})

test('a configured existing embed channel validates its HTML and frame policy', async t => {
  const fixture = await deploymentFixture(t)
  await checkDeployment(fixture.url, fixture.brand, { embedChannelId: channelId })
  await assert.rejects(checkDeployment(fixture.url, fixture.brand, { embedChannelId: 'branding-check' }), /must be a UUID/)
  const missingPolicy = await deploymentFixture(t, { [`/embed/${channelId}`]: ['text/html', `<title>${fixture.brand.productName}</title>`] })
  await assert.rejects(checkDeployment(missingPolicy.url, missingPolicy.brand, { embedChannelId: channelId }), /frame-ancestors/)
})

test('invalid embed entries must fail closed without serving application HTML', async t => {
  const bypass = await deploymentFixture(t, { '/embed/branding-check': ['text/html', '<html></html>'] })
  await assert.rejects(checkDeployment(bypass.url, bypass.brand), /unexpected HTTP 200/)
  const leakedApp = await deploymentFixture(t, { '/embed/branding-check': ['text/html', '<div id="embed-app"></div>', 403] })
  await assert.rejects(checkDeployment(leakedApp.url, leakedApp.brand), /returned the embed application/)
  const upstreamError = await deploymentFixture(t, { '/embed/branding-check': ['text/html', '<h1>Internal Server Error</h1>', 500] })
  await checkDeployment(upstreamError.url, upstreamError.brand)
})

test('deployment checks reject upstream title and SPA fallback masquerading as license', async t => {
  const title = await deploymentFixture(t, { '/': ['text/html', '<title>WeKnora</title>'] })
  await assert.rejects(checkDeployment(title.url, title.brand), /initial title/)
  const license = await deploymentFixture(t, { '/licenses/LICENSE': ['text/html', '<html></html>'] })
  await assert.rejects(checkDeployment(license.url, license.brand), /License endpoint/)
})

test('deployment checks reject stale widget content and cross-origin asset URLs', async t => {
  const widget = await deploymentFixture(t, { '/weknora-widget.js': ['application/javascript', 'window.WeKnora={}'] })
  await assert.rejects(checkDeployment(widget.url, widget.brand), /widget differs/)
  const crossOrigin = await deploymentFixture(t)
  await assert.rejects(checkDeployment(crossOrigin.url, { ...crossOrigin.brand, logoUrl: 'https://example.invalid/logo.svg' }), /Cross-origin asset/)
})

test('favicon checks reject stable HTML links, HTML fallback and forged image signatures', async t => {
  const stale = await deploymentFixture(t, { '/platform/creatChat': html => ['text/html', html.replace(/logo-[a-f0-9]{12}\.svg/, 'logo.svg')] })
  await assert.rejects(checkDeployment(stale.url, stale.brand), /incorrect hashed favicon link/)
  const fallback = await deploymentFixture(t, { '/favicon.ico': ['text/html', '<html><title>Application</title></html>'] })
  await assert.rejects(checkDeployment(fallback.url, fallback.brand), /favicon Content-Type/)
  const forged = await deploymentFixture(t, { '/favicon.ico': ['image/x-icon', '<html>not an icon</html>'] })
  await assert.rejects(checkDeployment(forged.url, forged.brand), /invalid favicon signature/)
})
