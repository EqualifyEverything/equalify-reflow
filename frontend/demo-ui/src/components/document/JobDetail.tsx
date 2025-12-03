import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Shield,
  FileCheck,
  Download,
  Clock,
  AlertTriangle,
  CheckCircle2
} from 'lucide-react'
import { formatDate, cn } from '@/lib/utils'
import { useJob } from '@/hooks/useJob'
import { WorkflowStepper } from '@/components/dev/WorkflowStepper'
import { JsonViewer } from '@/components/dev/JsonViewer'
import { PIIReviewModal } from '@/components/dev/PIIReviewModal'
import { CorrectionReviewModal } from '@/components/dev/CorrectionReviewModal'

interface JobDetailProps {
  jobId: string
}

export function JobDetail({ jobId }: JobDetailProps) {
  const { data: job, isLoading, error } = useJob(jobId)
  const [piiModalOpen, setPiiModalOpen] = useState(false)
  const [correctionModalOpen, setCorrectionModalOpen] = useState(false)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="p-8 text-center">
          <Clock className="h-8 w-8 text-slate-400 mx-auto mb-2 animate-pulse" />
          <p className="text-muted-foreground">Loading job details...</p>
        </CardContent>
      </Card>
    )
  }

  if (error || !job) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-red-600">
          Failed to load job details
          {error instanceof Error && (
            <p className="text-sm mt-2">{error.message}</p>
          )}
        </CardContent>
      </Card>
    )
  }

  // Determine available actions based on status
  const showPiiApproval = job.status === 'awaiting_approval' && job.approval_token
  const showCorrectionReview = job.status === 'awaiting_correction_approval' && job.approval_token
  const showResults = job.status === 'completed'
  const isTerminal = ['completed', 'failed', 'denied'].includes(job.status)

  return (
    <div className="space-y-6">
      {/* Workflow stepper */}
      <Card>
        <CardContent className="pt-6">
          <WorkflowStepper status={job.status} />
        </CardContent>
      </Card>

      {/* Job info */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-uic-navy">Job Information</CardTitle>
            <Badge className={cn(
              'text-white',
              {
                'bg-green-500': job.status === 'completed',
                'bg-blue-500': ['pii_scanning', 'processing'].includes(job.status),
                'bg-yellow-500': ['awaiting_approval', 'awaiting_correction_approval'].includes(job.status),
                'bg-red-500': ['failed', 'denied'].includes(job.status),
                'bg-gray-500': job.status === 'pending',
              }
            )}>
              {job.status.replace(/_/g, ' ')}
            </Badge>
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
              <p className="text-sm capitalize">{job.status.replace(/_/g, ' ')}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Created</p>
              <p className="text-sm">{formatDate(job.created_at)}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Updated</p>
              <p className="text-sm">{formatDate(job.updated_at)}</p>
            </div>
            {job.estimated_completion_minutes && !isTerminal && (
              <div>
                <p className="text-sm font-medium text-muted-foreground">Est. Completion</p>
                <p className="text-sm">{job.estimated_completion_minutes} minutes</p>
              </div>
            )}
          </div>

          {/* LLM cost info */}
          {job.llm_cost && (
            <div className="bg-slate-50 rounded-lg p-3">
              <p className="text-sm font-medium text-slate-700 mb-1">LLM Cost</p>
              <p className="text-lg font-bold text-uic-navy">
                ${job.llm_cost.total_cost_dollars.toFixed(4)}
                <span className="text-sm font-normal text-muted-foreground ml-2">
                  ({job.llm_cost.total_cost_cents.toFixed(2)}¢)
                </span>
              </p>
            </div>
          )}

          {/* Correction confidence */}
          {job.correction_summary && (
            <div className="bg-purple-50 rounded-lg p-3">
              <p className="text-sm font-medium text-purple-700 mb-1">
                AI Corrections
              </p>
              <div className="flex items-center gap-4">
                <div>
                  <span className="text-2xl font-bold text-purple-600">
                    {job.correction_summary.total_corrections}
                  </span>
                  <span className="text-sm text-purple-600 ml-1">corrections</span>
                </div>
                <div>
                  <span className={cn(
                    'text-2xl font-bold',
                    job.correction_summary.confidence_score >= 0.9
                      ? 'text-green-600'
                      : job.correction_summary.confidence_score >= 0.7
                      ? 'text-yellow-600'
                      : 'text-red-600'
                  )}>
                    {(job.correction_summary.confidence_score * 100).toFixed(0)}%
                  </span>
                  <span className="text-sm text-slate-600 ml-1">confidence</span>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Action buttons */}
      {(showPiiApproval || showCorrectionReview || showResults) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-uic-navy">Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {showPiiApproval && (
              <Button
                className="w-full bg-yellow-500 hover:bg-yellow-600"
                onClick={() => setPiiModalOpen(true)}
              >
                <Shield className="h-4 w-4 mr-2" />
                Review PII Findings ({job.pii_findings?.length || 0})
              </Button>
            )}

            {showCorrectionReview && (
              <Button
                className="w-full bg-purple-600 hover:bg-purple-700"
                onClick={() => setCorrectionModalOpen(true)}
              >
                <FileCheck className="h-4 w-4 mr-2" />
                Review AI Corrections ({job.correction_summary?.total_corrections || 0})
              </Button>
            )}

            {showResults && job.markdown_url && (
              <Button
                className="w-full bg-uic-red hover:bg-uic-red/90"
                asChild
              >
                <a
                  href={job.markdown_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Download className="h-4 w-4 mr-2" />
                  Download Markdown
                </a>
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {/* Status-specific alerts */}
      {job.status === 'awaiting_approval' && job.pii_findings && job.pii_findings.length > 0 && (
        <Alert className="border-yellow-300 bg-yellow-50">
          <AlertTriangle className="h-4 w-4 text-yellow-600" />
          <AlertDescription className="text-yellow-800">
            <span className="font-medium">PII Detected: </span>
            {job.pii_findings.length} entities found.
            Review and approve or deny to continue processing.
          </AlertDescription>
        </Alert>
      )}

      {job.status === 'awaiting_correction_approval' && (
        <Alert className="border-purple-300 bg-purple-50">
          <FileCheck className="h-4 w-4 text-purple-600" />
          <AlertDescription className="text-purple-800">
            <span className="font-medium">AI Corrections Ready: </span>
            {job.correction_summary?.total_corrections} corrections with{' '}
            {((job.correction_summary?.confidence_score || 0) * 100).toFixed(0)}% confidence.
            Review and approve or reject.
          </AlertDescription>
        </Alert>
      )}

      {job.status === 'completed' && (
        <Alert className="border-green-300 bg-green-50">
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          <AlertDescription className="text-green-800">
            <span className="font-medium">Processing Complete! </span>
            Your markdown file is ready for download.
          </AlertDescription>
        </Alert>
      )}

      {job.error && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            <span className="font-medium">Error: </span>
            {job.error}
          </AlertDescription>
        </Alert>
      )}

      {/* Raw API response */}
      <JsonViewer
        data={job}
        title="API Response"
        keyFields={['job_id', 'status', 'error']}
      />

      {/* PII Review Modal */}
      {job.approval_token && job.pii_findings && (
        <PIIReviewModal
          open={piiModalOpen}
          onClose={() => setPiiModalOpen(false)}
          jobId={job.job_id}
          token={job.approval_token}
          filename={job.filename || `Job ${job.job_id.slice(0, 8)}`}
          piiFindings={job.pii_findings}
        />
      )}

      {/* Correction Review Modal */}
      {job.approval_token && job.correction_summary && job.original_markdown_url && job.corrected_markdown_url && (
        <CorrectionReviewModal
          open={correctionModalOpen}
          onClose={() => setCorrectionModalOpen(false)}
          jobId={job.job_id}
          token={job.approval_token}
          filename={job.filename || `Job ${job.job_id.slice(0, 8)}`}
          correctionSummary={job.correction_summary}
          originalMarkdownUrl={job.original_markdown_url}
          correctedMarkdownUrl={job.corrected_markdown_url}
          pageImageUrls={job.page_image_urls}
        />
      )}
    </div>
  )
}
