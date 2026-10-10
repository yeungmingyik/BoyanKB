import assert from 'node:assert/strict'
import test from 'node:test'
import { createGraphAvailabilityLoader, type GraphAvailability } from './graphAvailability.ts'

test('a failed request is an error and a forced retry can enable graph controls', async () => {
  const states: GraphAvailability[] = []
  const forces: boolean[] = []
  const load = createGraphAvailabilityLoader(async force => {
    forces.push(force)
    if (!force) throw new Error('network unavailable')
    return { graph_database_engine: 'Neo4j' }
  }, state => states.push(state))
  await load()
  await load(true)
  assert.deepEqual(states, ['loading', 'error', 'loading', 'enabled'])
  assert.deepEqual(forces, [false, true])
})

test('only an explicit server disabled state reports graph as disabled', async () => {
  for (const info of [null, {}, { graph_database_engine: '' }, { graph_database_engine: 'unexpected' }]) {
    const states: GraphAvailability[] = []
    await createGraphAvailabilityLoader(async () => info, state => states.push(state))()
    assert.deepEqual(states, ['loading', 'error'])
  }
  const states: GraphAvailability[] = []
  await createGraphAvailabilityLoader(async () => ({ graph_database_engine: 'Not Enabled' }), state => states.push(state))()
  assert.deepEqual(states, ['loading', 'disabled'])
})

test('an older response cannot override a newer successful check', async () => {
  let resolve!: (value: { graph_database_engine: string }) => void
  const first = new Promise<{ graph_database_engine: string }>(done => { resolve = done })
  const states: GraphAvailability[] = []
  const load = createGraphAvailabilityLoader(async force => force ? { graph_database_engine: 'Neo4j' } : first, state => states.push(state))
  const old = load()
  await load(true)
  resolve({ graph_database_engine: 'Not Enabled' })
  await old
  assert.deepEqual(states, ['loading', 'loading', 'enabled'])
})
