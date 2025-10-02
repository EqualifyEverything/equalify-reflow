import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

/**
 * Poll queue metrics every 2 seconds (dev-only endpoint).
 * Returns null if endpoint not available.
 */
export function useQueueMetrics() {
  return useQuery({
    queryKey: ['queues'],
    queryFn: () => api.getQueueMetrics(),
    refetchInterval: 2000,
  })
}
