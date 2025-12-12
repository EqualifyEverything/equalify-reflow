import { useParams, Link } from 'react-router-dom'
import { JobDetail } from '@/components/document/JobDetail'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Layers } from 'lucide-react'

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()

  if (!jobId) {
    return (
      <div className="max-w-4xl mx-auto">
        <p className="text-red-600">Invalid job ID</p>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="outline" asChild>
            <Link to="/">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Dashboard
            </Link>
          </Button>
          <div>
            <h1 className="text-3xl font-bold text-uic-navy mb-2">Job Details</h1>
            <p className="text-muted-foreground font-mono text-sm">{jobId}</p>
          </div>
        </div>
        <Button variant="outline" asChild>
          <Link to={`/job/${jobId}/phases`}>
            <Layers className="h-4 w-4 mr-2" />
            View Phases
          </Link>
        </Button>
      </div>

      <JobDetail jobId={jobId} />
    </div>
  )
}
