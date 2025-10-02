import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { AlertCircle, CheckCircle2, Clock, XCircle } from 'lucide-react'
import type { JobStatus } from '@/types/api'
import { formatDate } from '@/lib/utils'
import { Link } from 'react-router-dom'

const statusConfig = {
  pending: { color: 'bg-gray-500', icon: Clock, label: 'Pending' },
  pii_scanning: { color: 'bg-blue-500', icon: Clock, label: 'PII Scanning' },
  processing: { color: 'bg-blue-500', icon: Clock, label: 'Processing' },
  awaiting_approval: { color: 'bg-yellow-500', icon: AlertCircle, label: 'Awaiting Approval' },
  completed: { color: 'bg-green-500', icon: CheckCircle2, label: 'Completed' },
  failed: { color: 'bg-red-500', icon: XCircle, label: 'Failed' },
  denied: { color: 'bg-red-700', icon: XCircle, label: 'Denied' },
}

interface JobCardProps {
  job: JobStatus
}

export function JobCard({ job }: JobCardProps) {
  const config = statusConfig[job.status] || statusConfig.pending
  const Icon = config.icon

  return (
    <Link to={`/job/${job.job_id}`}>
      <Card className="hover:shadow-lg transition-shadow cursor-pointer">
        <CardContent className="p-4">
          <div className="flex items-center justify-between">
            <div className="space-y-1 flex-1">
              <p className="font-mono text-sm text-muted-foreground">
                {job.job_id}
              </p>
              <p className="text-xs text-muted-foreground">
                {formatDate(job.created_at)}
              </p>
              {job.error && (
                <p className="text-xs text-red-600 mt-2">
                  Error: {job.error}
                </p>
              )}
            </div>

            <div className="flex items-center gap-2">
              {job.pii_detected && (
                <Badge variant="outline" className="border-yellow-500 text-yellow-700">
                  PII Detected
                </Badge>
              )}
              <Badge className={`${config.color} text-white flex items-center gap-1`}>
                <Icon className="h-3 w-3" />
                {config.label}
              </Badge>
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  )
}
