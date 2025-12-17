import { Badge } from '@/components/ui/badge'
import { AlertCircle, CheckCircle2, Clock, XCircle, ArrowRight } from 'lucide-react'
import type { JobStatus } from '@/types/api'
import { formatDate } from '@/lib/utils'
import { Link } from 'react-router-dom'

const statusConfig: Record<string, { color: string; bgColor: string; icon: typeof Clock; label: string }> = {
  pending: { color: 'text-gray-700', bgColor: 'bg-gray-100', icon: Clock, label: 'Pending' },
  pii_scanning: { color: 'text-blue-700', bgColor: 'bg-blue-100', icon: Clock, label: 'PII Scanning' },
  processing: { color: 'text-blue-700', bgColor: 'bg-blue-100', icon: Clock, label: 'Processing' },
  awaiting_approval: { color: 'text-amber-700', bgColor: 'bg-amber-100', icon: AlertCircle, label: 'Awaiting PII Review' },
  awaiting_correction_approval: { color: 'text-purple-700', bgColor: 'bg-purple-100', icon: AlertCircle, label: 'Awaiting Correction Review' },
  completed: { color: 'text-green-700', bgColor: 'bg-green-100', icon: CheckCircle2, label: 'Completed' },
  failed: { color: 'text-red-700', bgColor: 'bg-red-100', icon: XCircle, label: 'Failed' },
  denied: { color: 'text-red-800', bgColor: 'bg-red-200', icon: XCircle, label: 'Denied' },
}

interface JobCardProps {
  job: JobStatus
}

export function JobCard({ job }: JobCardProps) {
  const config = statusConfig[job.status] || statusConfig.pending
  const Icon = config.icon

  return (
    <Link to={`/job/${job.job_id}`} className="block group">
      <div className="bg-white border border-gray-200 rounded-xl p-4 transition-all duration-300 hover:shadow-lg hover:border-uic-blue/30 hover:scale-[1.01]">
        <div className="flex items-center justify-between">
          <div className="space-y-1 flex-1 min-w-0">
            <p className="font-mono text-sm text-gray-600 truncate">
              {job.job_id}
            </p>
            <p className="text-xs text-gray-400">
              {formatDate(job.created_at)}
            </p>
            {job.error && (
              <p className="text-xs text-red-600 mt-2 truncate">
                Error: {job.error}
              </p>
            )}
          </div>

          <div className="flex items-center gap-2 ml-4">
            {job.pii_findings && job.pii_findings.length > 0 && (
              <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700 text-xs">
                PII ({job.pii_findings.length})
              </Badge>
            )}
            {job.correction_summary && (
              <Badge variant="outline" className="border-purple-300 bg-purple-50 text-purple-700 text-xs">
                {job.correction_summary.total_corrections} fixes
              </Badge>
            )}
            <Badge className={`${config.bgColor} ${config.color} flex items-center gap-1 text-xs font-medium`}>
              <Icon className="h-3 w-3" />
              {config.label}
            </Badge>
            <ArrowRight className="h-4 w-4 text-gray-400 group-hover:text-uic-blue group-hover:translate-x-1 transition-all" />
          </div>
        </div>
      </div>
    </Link>
  )
}
