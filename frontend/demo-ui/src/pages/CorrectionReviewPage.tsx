import { useState, useEffect } from 'react'
import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { CorrectionDiff, CorrectionsSummary } from '@/components/dev/CorrectionDiff'
import { JsonViewer } from '@/components/dev/JsonViewer'
import { WorkflowStepper } from '@/components/dev/WorkflowStepper'
import {
  CheckCircle2,
  XCircle,
  ArrowLeft,
  FileText,
  Clock,
  AlertTriangle
} from 'lucide-react'

export function CorrectionReviewPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const token = searchParams.get('token') || ''

  const [decision, setDecision] = useState<'approved' | 'rejected'>('approved')
  const [justification, setJustification] = useState('')
  const [reviewedBy, setReviewedBy] = useState('')
  const [originalMarkdown, setOriginalMarkdown] = useState('')
  const [correctedMarkdown, setCorrectedMarkdown] = useState('')

  // Fetch correction review data
  const {
    data: reviewData,
    isLoading,
    error
  } = useQuery({
    queryKey: ['correction-review', jobId, token],
    queryFn: () => api.getCorrectionReview(jobId!, token),
    enabled: !!jobId && !!token,
  })

  // Fetch markdown contents
  useEffect(() => {
    if (reviewData?.urls) {
      // Fetch original markdown
      if (reviewData.urls.original_markdown) {
        fetch(reviewData.urls.original_markdown)
          .then(r => r.text())
          .then(setOriginalMarkdown)
          .catch(console.error)
      }
      // Fetch corrected markdown
      if (reviewData.urls.corrected_markdown) {
        fetch(reviewData.urls.corrected_markdown)
          .then(r => r.text())
          .then(setCorrectedMarkdown)
          .catch(console.error)
      }
    }
  }, [reviewData?.urls])

  // Submit decision mutation
  const submitMutation = useMutation({
    mutationFn: () =>
      api.submitCorrectionDecision(jobId!, {
        token,
        decision,
        reviewed_by: reviewedBy,
        justification,
      }),
    onSuccess: () => {
      // Navigate back to job detail after short delay
      setTimeout(() => navigate(`/job/${jobId}`), 2000)
    },
  })

  const handleSubmit = () => {
    if (!justification.trim() || justification.length < 10) {
      alert('Please provide a justification (at least 10 characters)')
      return
    }
    if (!reviewedBy.trim() || reviewedBy.length < 3) {
      alert('Please enter your name or email (at least 3 characters)')
      return
    }
    submitMutation.mutate()
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <Card>
          <CardContent className="p-8 text-center">
            <Clock className="h-12 w-12 text-slate-400 mx-auto mb-4 animate-pulse" />
            <p className="text-muted-foreground">Loading correction review...</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Error state
  if (error || !reviewData) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <Card>
          <CardContent className="p-8 text-center">
            <XCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
            <h2 className="text-xl font-bold text-red-600 mb-2">
              Unable to Load Review
            </h2>
            <p className="text-muted-foreground mb-4">
              {error instanceof Error ? error.message : 'Invalid or expired review token'}
            </p>
            <Button variant="outline" onClick={() => navigate(-1)}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Go Back
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Success state (after submission)
  if (submitMutation.isSuccess) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <Card>
          <CardContent className="p-8 text-center">
            <CheckCircle2 className="h-16 w-16 text-green-500 mx-auto mb-4" />
            <h2 className="text-2xl font-bold text-uic-navy mb-2">
              Review Submitted
            </h2>
            <p className="text-muted-foreground mb-4">
              Your correction decision ({decision}) has been recorded.
              {decision === 'approved'
                ? ' The corrected markdown will be used as the final output.'
                : ' The original markdown will be used as the final output.'}
            </p>
            <Button onClick={() => navigate(`/job/${jobId}`)}>
              View Job Status
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Check if token is expired
  const isExpired = new Date(reviewData.expires_at) < new Date()

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(-1)}
            className="mb-2"
          >
            <ArrowLeft className="h-4 w-4 mr-1" />
            Back
          </Button>
          <h1 className="text-3xl font-bold text-uic-navy">
            Correction Review
          </h1>
          <p className="text-muted-foreground">
            Review AI-suggested corrections for job {jobId?.slice(0, 8)}...
          </p>
        </div>
        <Badge
          className={isExpired ? 'bg-red-500' : 'bg-green-500'}
        >
          {isExpired ? 'Expired' : `Expires ${formatDate(reviewData.expires_at)}`}
        </Badge>
      </div>

      {/* Workflow stepper */}
      <Card>
        <CardContent className="pt-6">
          <WorkflowStepper status="awaiting_correction_approval" />
        </CardContent>
      </Card>

      {isExpired && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            This review token has expired. Please request a new review link.
          </AlertDescription>
        </Alert>
      )}

      {/* Corrections summary */}
      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy flex items-center gap-2">
            <FileText className="h-5 w-5" />
            Corrections Summary
          </CardTitle>
        </CardHeader>
        <CardContent>
          <CorrectionsSummary
            totalCorrections={reviewData.total_corrections}
            confidence={reviewData.overall_confidence}
            byType={reviewData.by_type}
          />
        </CardContent>
      </Card>

      {/* Diff viewer */}
      {(originalMarkdown || correctedMarkdown) && (
        <CorrectionDiff
          originalMarkdown={originalMarkdown}
          correctedMarkdown={correctedMarkdown}
          corrections={reviewData.corrections}
          pageImages={reviewData.urls.page_images}
        />
      )}

      {/* Decision form */}
      {!isExpired && (
        <Card>
          <CardHeader>
            <CardTitle className="text-uic-navy">Submit Decision</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Reviewer Name/Email</Label>
              <input
                type="text"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={reviewedBy}
                onChange={(e) => setReviewedBy(e.target.value)}
                placeholder="your@email.com"
              />
            </div>

            <div className="space-y-2">
              <Label>Decision</Label>
              <div className="grid grid-cols-2 gap-4">
                <button
                  onClick={() => setDecision('approved')}
                  className={`p-4 rounded-lg border-2 transition-all ${
                    decision === 'approved'
                      ? 'border-green-500 bg-green-50'
                      : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <CheckCircle2 className={`h-8 w-8 mx-auto mb-2 ${
                    decision === 'approved' ? 'text-green-500' : 'text-slate-400'
                  }`} />
                  <p className="font-medium">Approve Corrections</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Use the corrected markdown as the final output
                  </p>
                </button>
                <button
                  onClick={() => setDecision('rejected')}
                  className={`p-4 rounded-lg border-2 transition-all ${
                    decision === 'rejected'
                      ? 'border-red-500 bg-red-50'
                      : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <XCircle className={`h-8 w-8 mx-auto mb-2 ${
                    decision === 'rejected' ? 'text-red-500' : 'text-slate-400'
                  }`} />
                  <p className="font-medium">Reject Corrections</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Use the original markdown as the final output
                  </p>
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <Label>Justification (10-1000 characters)</Label>
              <Textarea
                value={justification}
                onChange={(e) => setJustification(e.target.value)}
                placeholder={decision === 'approved'
                  ? 'The AI corrections improve document structure and heading hierarchy...'
                  : 'The corrections do not accurately reflect the intended structure...'}
                rows={4}
              />
              <p className="text-xs text-muted-foreground text-right">
                {justification.length}/1000
              </p>
            </div>

            {submitMutation.isError && (
              <Alert variant="destructive">
                <AlertDescription>
                  {submitMutation.error instanceof Error
                    ? submitMutation.error.message
                    : 'Failed to submit review'}
                </AlertDescription>
              </Alert>
            )}

            <Button
              onClick={handleSubmit}
              disabled={submitMutation.isPending || isExpired}
              className={`w-full ${
                decision === 'approved'
                  ? 'bg-green-600 hover:bg-green-700'
                  : 'bg-red-600 hover:bg-red-700'
              }`}
            >
              {submitMutation.isPending
                ? 'Submitting...'
                : decision === 'approved'
                ? 'Approve Corrections'
                : 'Reject Corrections'}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Raw response viewer */}
      <JsonViewer
        data={reviewData}
        title="API Response (Correction Review)"
        keyFields={['job_id', 'total_corrections', 'overall_confidence']}
      />
    </div>
  )
}
