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
        <CardHeader className="bg-gradient-to-r from-gray-50 to-gray-100 border-b border-gray-100">
          <CardTitle className="flex items-center gap-3">
            <div className="p-2 bg-uic-blue/10 rounded-lg">
              <Layers className="h-5 w-5 text-uic-blue" />
            </div>
            Recent Jobs
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
          <p className="text-gray-500 text-center py-8">
            Loading saved jobs...
          </p>
        </CardContent>
      </Card>
    )
  }

  if (jobs.length === 0) {
    return (
      <Card>
        <CardHeader className="bg-gradient-to-r from-gray-50 to-gray-100 border-b border-gray-100">
          <CardTitle className="flex items-center gap-3">
            <div className="p-2 bg-uic-blue/10 rounded-lg">
              <Layers className="h-5 w-5 text-uic-blue" />
            </div>
            Recent Jobs
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
          <p className="text-gray-500 text-center py-8">
            No jobs yet. Upload a PDF to get started.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 bg-gradient-to-r from-gray-50 to-gray-100 border-b border-gray-100">
        <CardTitle className="flex items-center gap-3">
          <div className="p-2 bg-uic-blue/10 rounded-lg">
            <Layers className="h-5 w-5 text-uic-blue" />
          </div>
          Recent Jobs
          <span className="text-sm font-normal text-gray-500">({jobs.length})</span>
        </CardTitle>
        <Button
          variant="ghost"
          size="sm"
          onClick={clearAllJobs}
          className="text-gray-500 hover:text-uic-red hover:bg-red-50"
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
