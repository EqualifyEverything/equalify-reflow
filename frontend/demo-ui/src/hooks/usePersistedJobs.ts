import { useState, useEffect, useCallback, useRef } from 'react'
import { api, JobStatus } from '@/lib/api'

const STORAGE_KEY = 'equalify-recent-jobs'
const MAX_JOBS = 20 // Maximum jobs to remember
const POLL_INTERVAL = 2000 // 2 seconds
const TERMINAL_STATES = ['completed', 'failed', 'denied']

interface StoredJob {
  jobId: string
  addedAt: number // timestamp when job was added
}

/**
 * Hook that persists job IDs to localStorage and manages job polling.
 *
 * Features:
 * - Persists job IDs across page refreshes
 * - Validates stored jobs on mount (removes 404s)
 * - Polls active jobs every 2 seconds
 * - Stops polling terminal jobs (completed, failed, denied)
 * - Automatically prunes old jobs (keeps last 20)
 */
export function usePersistedJobs() {
  const [jobs, setJobs] = useState<JobStatus[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Load stored job IDs from localStorage
  const loadStoredJobIds = useCallback((): StoredJob[] => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (!stored) return []
      const parsed = JSON.parse(stored)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }, [])

  // Save job IDs to localStorage
  const saveJobIds = useCallback((jobIds: StoredJob[]) => {
    try {
      // Keep only the most recent MAX_JOBS
      const trimmed = jobIds.slice(0, MAX_JOBS)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed))
    } catch (error) {
      console.error('Failed to save jobs to localStorage:', error)
    }
  }, [])

  // Add a new job ID to storage and state
  const addJob = useCallback(async (jobId: string) => {
    // First, add to localStorage
    const stored = loadStoredJobIds()
    const alreadyExists = stored.some(j => j.jobId === jobId)

    if (!alreadyExists) {
      const newStored: StoredJob[] = [
        { jobId, addedAt: Date.now() },
        ...stored
      ]
      saveJobIds(newStored)
    }

    // Then fetch the job status and add to state
    try {
      const status = await api.getJobStatus(jobId)
      setJobs(prev => {
        const exists = prev.some(j => j.job_id === jobId)
        if (exists) {
          return prev.map(j => j.job_id === jobId ? status : j)
        }
        return [status, ...prev]
      })
    } catch (error) {
      console.error(`Failed to fetch job ${jobId}:`, error)
    }
  }, [loadStoredJobIds, saveJobIds])

  // Remove a job from storage and state
  const removeJob = useCallback((jobId: string) => {
    const stored = loadStoredJobIds()
    const filtered = stored.filter(j => j.jobId !== jobId)
    saveJobIds(filtered)
    setJobs(prev => prev.filter(j => j.job_id !== jobId))
  }, [loadStoredJobIds, saveJobIds])

  // Clear all jobs from storage and state
  const clearAllJobs = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setJobs([])
  }, [])

  // Validate and load jobs on mount
  useEffect(() => {
    let mounted = true

    async function validateAndLoadJobs() {
      const stored = loadStoredJobIds()
      if (stored.length === 0) {
        setIsLoading(false)
        return
      }

      // Fetch all stored jobs in parallel
      const results = await Promise.allSettled(
        stored.map(async ({ jobId }) => {
          const status = await api.getJobStatus(jobId)
          return status
        })
      )

      if (!mounted) return

      // Filter out failed fetches (404s, expired jobs)
      const validJobs: JobStatus[] = []
      const validStoredJobs: StoredJob[] = []

      results.forEach((result, index) => {
        if (result.status === 'fulfilled') {
          validJobs.push(result.value)
          validStoredJobs.push(stored[index])
        }
        // Jobs that returned 404 are silently removed
      })

      // Update localStorage with only valid jobs
      saveJobIds(validStoredJobs)
      setJobs(validJobs)
      setIsLoading(false)
    }

    validateAndLoadJobs()

    return () => {
      mounted = false
    }
  }, [loadStoredJobIds, saveJobIds])

  // Poll active jobs
  useEffect(() => {
    if (isLoading) return

    // Get jobs that need polling (non-terminal states)
    const getActiveJobIds = () =>
      jobs
        .filter(j => !TERMINAL_STATES.includes(j.status))
        .map(j => j.job_id)

    async function pollActiveJobs() {
      const activeIds = getActiveJobIds()
      if (activeIds.length === 0) return

      const results = await Promise.allSettled(
        activeIds.map(id => api.getJobStatus(id))
      )

      setJobs(prev => {
        const updated = [...prev]
        results.forEach((result, index) => {
          if (result.status === 'fulfilled') {
            const jobIndex = updated.findIndex(j => j.job_id === activeIds[index])
            if (jobIndex !== -1) {
              updated[jobIndex] = result.value
            }
          }
        })
        return updated
      })
    }

    // Start polling
    pollIntervalRef.current = setInterval(pollActiveJobs, POLL_INTERVAL)

    // Also poll immediately on first render after loading
    pollActiveJobs()

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
      }
    }
  }, [isLoading, jobs.length]) // Re-setup when job count changes

  return {
    jobs,
    isLoading,
    addJob,
    removeJob,
    clearAllJobs,
  }
}
