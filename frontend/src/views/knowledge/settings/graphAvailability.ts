export type GraphAvailability = 'loading' | 'enabled' | 'disabled' | 'error'

export function createGraphAvailabilityLoader(
  read: (force: boolean) => Promise<{ graph_database_engine?: string } | null>,
  update: (state: GraphAvailability) => void,
) {
  let version = 0
  return async (force = false): Promise<void> => {
    const current = ++version
    update('loading')
    try {
      const info = await read(force)
      if (current !== version) return
      const engine = info?.graph_database_engine?.trim()
      update(engine === 'Neo4j' ? 'enabled' : engine === 'Not Enabled' ? 'disabled' : 'error')
    } catch {
      if (current === version) update('error')
    }
  }
}
