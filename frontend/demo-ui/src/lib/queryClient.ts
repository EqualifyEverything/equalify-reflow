import { QueryClient } from '@tanstack/react-query'

/**
 * React Query client instance.
 * Configured with sensible defaults for the demo UI.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't retry on error in demo (fails fast)
      retry: 1,
      // Refetch on window focus for fresh data
      refetchOnWindowFocus: true,
      // Consider data stale after 30 seconds
      staleTime: 30 * 1000,
    },
    mutations: {
      // Don't retry mutations
      retry: 0,
    },
  },
})
