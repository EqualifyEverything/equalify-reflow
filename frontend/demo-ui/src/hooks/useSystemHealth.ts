import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

/**
 * Poll system health every 5 seconds.
 */
export function useSystemHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 5000,
  })
}
