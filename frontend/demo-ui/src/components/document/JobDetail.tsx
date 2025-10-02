import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ExternalLink } from 'lucide-react'
import type { JobStatus, JobResult } from '@/types/api'
import { formatDate } from '@/lib/utils'
import { useJob } from '@/hooks/useJob'

interface JobDetailProps {
  jobId: string
}

export function JobDetail({ jobId }: JobDetailProps) {
  const { data: job, isLoading, error } = useJob(jobId)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-muted-foreground">
          Loading job details...
        </CardContent>
      </Card>
    )
  }

  if (error || !job) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-red-600">
          Failed to load job details
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-uic-navy">Job Details</CardTitle>
          <Badge>{job.status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-sm font-medium text-muted-foreground">Job ID</p>
            <p className="font-mono text-sm">{job.job_id}</p>
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">Status</p>
            <p className="text-sm capitalize">{job.status.replace('_', ' ')}</p>
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">Created</p>
            <p className="text-sm">{formatDate(job.created_at)}</p>
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">Updated</p>
            <p className="text-sm">{formatDate(job.updated_at)}</p>
          </div>
        </div>

        {job.pii_detected && (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-sm font-medium text-yellow-900">
              ⚠️ PII Detected - Awaiting Approval
            </p>
          </div>
        )}

        {job.error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-sm font-medium text-red-900">Error</p>
            <p className="text-sm text-red-700">{job.error}</p>
          </div>
        )}

        {job.status === 'completed' && (
          <div className="pt-4">
            <Button className="bg-uic-red hover:bg-uic-red/90 w-full" asChild>
              <a href={`/api/documents/${job.job_id}/result`} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-4 w-4 mr-2" />
                View Results
              </a>
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
