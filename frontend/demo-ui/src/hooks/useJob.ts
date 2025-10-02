import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

/**
 * Poll for job status with automatic refetching.
 *
 * Refetch every 2 seconds while job is pending/processing.
 * Stop refetching when complete/failed.
 */
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.getJobStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      // Stop polling if job is terminal state
      const data = query.state.data
      if (!data) return false
      const terminalStates = ['completed', 'failed', 'denied']
      return terminalStates.includes(data.status) ? false : 2000
    },
  })
}
