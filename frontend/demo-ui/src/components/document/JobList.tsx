import { useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Trash2, Layers } from 'lucide-react'
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
        <CardHeader className="bg-gradient-to-r from-muted to-muted/80 border-b border-border">
          <CardTitle className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 rounded-lg">
              <Layers className="h-5 w-5 text-primary" />
            </div>
            Recent Jobs
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
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
        <CardHeader className="bg-gradient-to-r from-muted to-muted/80 border-b border-border">
          <CardTitle className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 rounded-lg">
              <Layers className="h-5 w-5 text-primary" />
            </div>
            Recent Jobs
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
          <p className="text-muted-foreground text-center py-8">
            No jobs yet. Upload a PDF to get started.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 bg-gradient-to-r from-muted to-muted/80 border-b border-border">
        <CardTitle className="flex items-center gap-3">
          <div className="p-2 bg-primary/10 rounded-lg">
            <Layers className="h-5 w-5 text-primary" />
          </div>
          Recent Jobs
          <span className="text-sm font-normal text-muted-foreground">({jobs.length})</span>
        </CardTitle>
        <Button
          variant="ghost"
          size="sm"
          onClick={clearAllJobs}
          className="text-muted-foreground hover:text-destructive hover:bg-destructive/10"
        >
          <Trash2 className="h-4 w-4 mr-1" />
          Clear All
        </Button>
      </CardHeader>
      <CardContent className="p-4 space-y-3">
        {jobs.map((job) => (
          <JobCard key={job.job_id} job={job} />
        ))}
      </CardContent>
    </Card>
  )
}
