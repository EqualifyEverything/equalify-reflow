import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { JobCard } from './JobCard'
import type { JobStatus } from '@/types/api'
import { api } from '@/lib/api'

interface JobListProps {
  latestJobId?: string | null
}

export function JobList({ latestJobId }: JobListProps) {
  const [jobs, setJobs] = useState<JobStatus[]>([])

  // Poll for latest job status if provided
  useEffect(() => {
    if (!latestJobId) return

    const interval = setInterval(async () => {
      try {
        const status = await api.getJobStatus(latestJobId)
        setJobs((prev) => {
          const existing = prev.find((j) => j.job_id === latestJobId)
          if (existing) {
            return prev.map((j) => (j.job_id === latestJobId ? status : j))
          }
          return [status, ...prev]
        })
      } catch (error) {
        console.error('Failed to fetch job status:', error)
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [latestJobId])

  if (jobs.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy">Recent Jobs</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-center py-8">
            No jobs yet. Upload a PDF to get started.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-uic-navy">Recent Jobs</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {jobs.map((job) => (
          <JobCard key={job.job_id} job={job} />
        ))}
      </CardContent>
    </Card>
  )
}
