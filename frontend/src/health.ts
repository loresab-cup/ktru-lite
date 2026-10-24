import { createContext, useContext } from 'react'
import type { Health } from './api'

/** null: not asked yet; 'offline': the service does not answer. */
export type HealthState = Health | null | 'offline'

export const HealthContext = createContext<HealthState>(null)

export function useHealth(): HealthState {
  return useContext(HealthContext)
}
