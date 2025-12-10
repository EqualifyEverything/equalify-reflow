import { useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Trash2 } from 'lucide-react'
import { JobCard } from './JobCard'
import { usePersistedJobs } from '@/hooks/usePersistedJobs'

interface JobListProps {
  latestJobId?: string | null
}

export function JobList({ latestJobId }: JobListProps) {
  const { jobs, isLoading, addJob, clearAllJobs } = usePersistedJobs()

  // When a new job is uploaded, add it to the persisted list
  useEffect(() => {
    if (latestJobId) {
      addJob(latestJobId)
    }
  }, [latestJobId, addJob])

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy">Recent Jobs</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-center py-8">
            Loading saved jobs...
          </p>
        </CardContent>
      </Card>
    )
  }

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
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-uic-navy">Recent Jobs</CardTitle>
        <Button
          variant="ghost"
          size="sm"
          onClick={clearAllJobs}
          className="text-muted-foreground hover:text-destructive"
        >
          <Trash2 className="h-4 w-4 mr-1" />
          Clear
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {jobs.map((job) => (
          <JobCard key={job.job_id} job={job} />
        ))}
      </CardContent>
    </Card>
  )
}
