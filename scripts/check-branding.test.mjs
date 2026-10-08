import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
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
  const license = await readFile(new URL('../LICENSE', import.meta.url))
  const template = await readFile(new URL('../frontend/public/weknora-widget.js', import.meta.url), 'utf8')
  const tokens = { __BRAND_NAME__: brand.productName, __BRAND_SLUG__: brand.productSlug, __BRAND_PRIMARY_COLOR__: brand.colors.action, __BRAND_LOGO_URL__: brand.logoUrl }
  const widget = template.replace(/__BRAND_[A-Z_]+__/g, token => JSON.stringify(tokens[token]).slice(1, -1).replace(/'/g, "\\'").replace(/</g, '\\u003c'))
  const html = `<html><head><title>${brand.productName}</title><meta name="description" content="${brand.productName} ${brand.description}"><link rel="icon" href="${brand.faviconUrl}"><link rel="apple-touch-icon" href="${brand.appleTouchIconUrl}"></head></html>`
  const routes = new Map([
    ['/', ['text/html', html]], ['/login', ['text/html', html]],
    ['/embed/branding-check', ['text/plain', 'Forbidden', 403]],
    [`/embed/${channelId}`, ['text/html', html, 200, "frame-ancestors 'self'"]],
    [brand.logoUrl, ['image/svg+xml', logo]], [brand.faviconUrl, ['image/svg+xml', logo]],
    [brand.appleTouchIconUrl, ['image/png', png]],
    [`/${brand.productSlug}-widget.js`, ['application/javascript', widget]],
    ['/weknora-widget.js', ['application/javascript', widget]], ['/licenses/LICENSE', ['text/plain', license]],
    ...Object.entries(override),
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
