import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

const args = process.argv.slice(2)
if (args.some((value) => value !== '--apply') || args.length > 1) throw new Error('Usage: node scripts/migrate-branding-smoke.mjs [--apply]')
const brand = JSON.parse(readFileSync(new URL('../branding/brand.json', import.meta.url), 'utf8'))
if (!/^[a-z][a-z0-9-]+$/.test(brand.productSlug)) throw new Error('Invalid brand slug')
const before = 'weknora-local-smoke-v1'
const after = `${brand.productSlug}-local-smoke-v1`
const targets = [
  ['custom_agents', 'name', 'local-smoke-rag'],
  ['knowledge_bases', 'name', 'local-smoke-stardust'],
  ['sessions', 'title', 'local-smoke-stardust'],
]
const queries = targets.map(([table, field, value]) => {
  const where = `${field}='${value}' AND description='${before}' AND deleted_at IS NULL`
  return args.includes('--apply')
    ? `WITH changed AS (UPDATE ${table} SET description='${after}' WHERE ${where} RETURNING 1) SELECT '${table}', count(*) FROM changed;`
    : `SELECT '${table}', count(*) FROM ${table} WHERE ${where};`
})
const sql = ['BEGIN;', ...queries, args.includes('--apply') ? 'COMMIT;' : 'ROLLBACK;'].join('\n')
const container = process.env.BRANDING_POSTGRES_CONTAINER || 'WeKnora-postgres'
const output = execFileSync('docker', ['exec', '-i', container, 'sh', '-c', 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'], { input: sql, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] })
console.log(args.includes('--apply') ? 'Applied smoke metadata branding:' : 'Preview smoke metadata branding:')
console.log(output.trim())
