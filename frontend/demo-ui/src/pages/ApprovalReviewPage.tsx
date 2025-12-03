import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { CheckCircle2 } from 'lucide-react'

export function ApprovalReviewPage() {
  const { token } = useParams<{ token: string }>()
  const [decision, setDecision] = useState<'approved' | 'denied'>('approved')
  const [justification, setJustification] = useState('')
  const [reviewedBy, setReviewedBy] = useState('')

  const { data: reviewData, isLoading, error } = useQuery({
    queryKey: ['review', token],
    queryFn: () => api.getReviewData(token!),
    enabled: !!token,
  })

  const submitMutation = useMutation({
    mutationFn: () =>
      api.submitApproval(token!, { decision, justification, reviewed_by: reviewedBy }),
  })

  const handleSubmit = () => {
    if (!justification.trim() || !reviewedBy.trim()) {
      alert('Please fill in all fields')
      return
    }
    submitMutation.mutate()
  }

  if (isLoading) {
    return (
      <div className="max-w-4xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center text-muted-foreground">
            Loading review data...
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !reviewData) {
    return (
      <div className="max-w-4xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center text-red-600">
            Invalid or expired review token
          </CardContent>
        </Card>
      </div>
    )
  }

  if (submitMutation.isSuccess) {
    return (
      <div className="max-w-4xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center">
            <CheckCircle2 className="h-16 w-16 text-green-500 mx-auto mb-4" />
            <h2 className="text-2xl font-bold text-uic-navy mb-2">
              Review Submitted
            </h2>
            <p className="text-muted-foreground">
              Your decision has been recorded and the document has been {decision}.
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-uic-navy mb-2">PII Review</h1>
        <p className="text-muted-foreground">
          Review and approve/deny document with detected PII
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy">Document Information</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div>
            <p className="text-sm font-medium text-muted-foreground">Filename</p>
            <p className="font-mono text-sm">{reviewData.document_filename || 'Unknown'}</p>
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">Detected</p>
            <p className="text-sm">{reviewData.detected_at ? formatDate(reviewData.detected_at) : formatDate(reviewData.created_at)}</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy">
            PII Findings ({reviewData.pii_findings.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {reviewData.pii_findings.map((finding, idx) => (
              <div
                key={idx}
                className="border rounded-lg p-3 flex items-center justify-between"
              >
                <div>
                  <Badge variant="outline">{finding.entity_type}</Badge>
                  <p className="font-mono text-sm mt-1">{finding.text}</p>
                </div>
                <Badge
                  className={
                    finding.score > 0.8
                      ? 'bg-red-500 text-white'
                      : 'bg-yellow-500 text-white'
                  }
                >
                  {(finding.score * 100).toFixed(0)}%
                </Badge>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy">Decision</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Reviewer Name</Label>
            <input
              type="text"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={reviewedBy}
              onChange={(e) => setReviewedBy(e.target.value)}
              placeholder="Your name"
            />
          </div>

          <div className="space-y-2">
            <Label>Decision</Label>
            <RadioGroup value={decision} onValueChange={(v) => setDecision(v as any)}>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="approved" id="approved" />
                <Label htmlFor="approved">Approve - Process document</Label>
              </div>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="denied" id="denied" />
                <Label htmlFor="denied">Deny - Reject document</Label>
              </div>
            </RadioGroup>
          </div>

          <div className="space-y-2">
            <Label>Justification (10-1000 characters)</Label>
            <Textarea
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="Explain your decision..."
              rows={4}
            />
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
            disabled={submitMutation.isPending}
            className="bg-uic-red hover:bg-uic-red/90 w-full"
          >
            {submitMutation.isPending ? 'Submitting...' : 'Submit Decision'}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
