import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import { brand, productDescription } from './src/config/branding'

const root = fileURLToPath(new URL('../', import.meta.url))
const htmlEscape = (value: string) => value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!)
const jsEscape = (value: string) => JSON.stringify(value).slice(1, -1).replace(/'/g, "\\'").replace(/</g, '\\u003c')

export function brandingPlugin(): Plugin {
  if (!/^[A-Za-z][A-Za-z0-9]{1,63}$/.test(brand.productName)) throw new Error('Invalid brand productName')
  if (!/^[a-z][a-z0-9-]{1,63}$/.test(brand.productSlug)) throw new Error('Invalid brand productSlug')
  for (const value of Object.values(brand.colors)) {
    if (!/^#[0-9a-f]{6}$/i.test(value)) throw new Error('Invalid brand color')
  }
  for (const value of [brand.logoUrl, brand.faviconUrl]) {
    if (!/^\/branding\/[A-Za-z0-9._-]+\.svg$/.test(value)) throw new Error('Invalid brand SVG URL')
  }
  if (!/^\/branding\/[A-Za-z0-9._-]+\.png$/.test(brand.appleTouchIconUrl)) throw new Error('Invalid brand PNG URL')
  const assets = () => {
    const logo = readFileSync(`${root}/branding/logo.svg`)
    const png = readFileSync(`${root}/branding/logo.png`)
    return new Map<string, string | Buffer>([
      [brand.logoUrl.slice(1), logo],
      [brand.faviconUrl.slice(1), logo],
      [brand.appleTouchIconUrl.slice(1), png],
      ['licenses/LICENSE', readFileSync(`${root}/LICENSE`)],
    ])
  }
  const widget = () => {
    const replacements: Record<string, string> = {
      __BRAND_NAME__: brand.productName,
      __BRAND_SLUG__: brand.productSlug,
      __BRAND_PRIMARY_COLOR__: brand.colors.action,
      __BRAND_LOGO_URL__: brand.logoUrl,
    }
    return readFileSync(`${root}/frontend/public/weknora-widget.js`, 'utf8')
      .replace(/__BRAND_[A-Z_]+__/g, (token) => {
        if (!(token in replacements)) throw new Error(`Unknown widget token ${token}`)
        return jsEscape(replacements[token])
      })
  }
  return {
    name: 'product-branding',
    enforce: 'pre',
    load(id) {
      const pathname = id.split('?')[0].replace(/\\/g, '/')
      if (pathname.endsWith('/assets/img/weknora.png')) return `export default ${JSON.stringify(brand.logoUrl)}`
      if (!brand.features.showcase && /\/assets\/img\/screenshot-[1-4]\.svg$/.test(pathname)) return 'export default ""'
    },
    transformIndexHtml(html) {
      const replacements: Record<string, string> = {
        __BRAND_NAME__: brand.productName,
        __BRAND_DESCRIPTION__: productDescription,
        __BRAND_FAVICON_URL__: brand.faviconUrl,
        __BRAND_APPLE_ICON_URL__: brand.appleTouchIconUrl,
      }
      return html.replace(/__BRAND_[A-Z_]+__/g, (token) => {
        if (!(token in replacements)) throw new Error(`Unknown HTML token ${token}`)
        return htmlEscape(replacements[token])
      })
    },
    transform(code, id) {
      if (id.split('?')[0].replace(/\\/g, '/').endsWith('/assets/theme/branding.css')) {
        const variables = Object.entries(brand.colors).map(([key, value]) => `--bk-${key}: ${value};`).join('')
        return { code: `:root {${variables}}\n${code}`, map: null }
      }
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = (request.url || '').split('?')[0]
        if ([`/${brand.productSlug}-widget.js`, '/weknora-widget.js'].includes(pathname)) {
          response.setHeader('Content-Type', 'application/javascript; charset=utf-8')
          response.end(widget())
          return
        }
        const asset = assets().get(pathname.slice(1))
        if (asset !== undefined) {
          response.setHeader('Content-Type', pathname.endsWith('.svg') ? 'image/svg+xml' : pathname.endsWith('.png') ? 'image/png' : 'text/plain; charset=utf-8')
          response.end(asset)
          return
        }
        next()
      })
    },
    generateBundle(_options, bundle) {
      delete bundle['favicon.ico']
      for (const [fileName, source] of assets()) this.emitFile({ type: 'asset', fileName, source })
      for (const fileName of [`${brand.productSlug}-widget.js`, 'weknora-widget.js']) {
        this.emitFile({ type: 'asset', fileName, source: widget() })
      }
    },
  }
}
