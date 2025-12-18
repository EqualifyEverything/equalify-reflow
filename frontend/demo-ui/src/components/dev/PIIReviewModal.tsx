import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { api, PIIEntity } from '@/lib/api'
import { CheckCircle2, Shield, AlertTriangle } from 'lucide-react'

interface PIIReviewModalProps {
  open: boolean
  onClose: () => void
  jobId: string
  token: string
  filename: string
  piiFindings: PIIEntity[]
}

export function PIIReviewModal({
  open,
  onClose,
  jobId,
  token,
  filename,
  piiFindings,
}: PIIReviewModalProps) {
  const queryClient = useQueryClient()
  const [decision, setDecision] = useState<'approved' | 'denied'>('approved')
  const [justification, setJustification] = useState('')
  const [reviewedBy, setReviewedBy] = useState('')

  const submitMutation = useMutation({
    mutationFn: () =>
      api.submitApprovalDecision(token, {
        decision,
        reviewed_by: reviewedBy,
        justification,
      }),
    onSuccess: () => {
      // Invalidate the job query to refresh status
      queryClient.invalidateQueries({ queryKey: ['job', jobId] })
      // Reset form
      setDecision('approved')
      setJustification('')
      setReviewedBy('')
    },
  })

  const handleSubmit = () => {
    if (!justification.trim() || !reviewedBy.trim()) {
      alert('Please fill in all fields')
      return
    }
    submitMutation.mutate()
  }

  const handleClose = () => {
    if (!submitMutation.isPending) {
      submitMutation.reset()
      onClose()
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-primary">
            <Shield className="h-5 w-5" />
            PII Review Required
          </DialogTitle>
        </DialogHeader>

        {submitMutation.isSuccess ? (
          <div className="text-center py-8">
            <CheckCircle2 className="h-16 w-16 text-green-500 dark:text-green-400 mx-auto mb-4" />
            <h3 className="text-xl font-semibold text-primary mb-2">
              Decision Submitted
            </h3>
            <p className="text-muted-foreground mb-4">
              Document has been {decision}. {decision === 'approved' ? 'Processing will continue.' : ''}
            </p>
            <Button onClick={handleClose}>Close</Button>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Document Info */}
            <div className="bg-muted/50 rounded-lg p-3">
              <p className="text-sm text-muted-foreground">Document</p>
              <p className="font-mono text-sm">{filename}</p>
            </div>

            {/* PII Findings */}
            <div>
              <h4 className="font-semibold flex items-center gap-2 mb-2">
                <AlertTriangle className="h-4 w-4 text-yellow-500" />
                PII Findings ({piiFindings.length})
              </h4>
              <div className="space-y-2 max-h-48 overflow-y-auto border rounded-lg p-2">
                {piiFindings.map((finding, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between bg-muted/30 rounded p-2"
                  >
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-xs">
                        {finding.entity_type}
                      </Badge>
                      <span className="font-mono text-sm">{finding.text}</span>
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
            </div>

            {/* Decision Form */}
            <div className="space-y-4 border-t pt-4">
              <div className="space-y-2">
                <Label>Reviewer Name</Label>
                <input
                  type="text"
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                  value={reviewedBy}
                  onChange={(e) => setReviewedBy(e.target.value)}
                  placeholder="Your name"
                />
              </div>

              <div className="space-y-2">
                <Label>Decision</Label>
                <RadioGroup
                  value={decision}
                  onValueChange={(v) => setDecision(v as 'approved' | 'denied')}
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="approved" id="modal-approved" />
                    <Label htmlFor="modal-approved" className="font-normal">
                      Approve - Continue processing
                    </Label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="denied" id="modal-denied" />
                    <Label htmlFor="modal-denied" className="font-normal">
                      Deny - Reject document
                    </Label>
                  </div>
                </RadioGroup>
              </div>

              <div className="space-y-2">
                <Label>Justification</Label>
                <Textarea
                  value={justification}
                  onChange={(e) => setJustification(e.target.value)}
                  placeholder="Explain your decision (10-1000 characters)..."
                  rows={3}
                />
                <p className="text-xs text-muted-foreground">
                  {justification.length}/1000 characters
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

              <div className="flex gap-2 pt-2">
                <Button
                  variant="outline"
                  onClick={handleClose}
                  disabled={submitMutation.isPending}
                  className="flex-1"
                >
                  Cancel
                </Button>
                <Button
                  onClick={handleSubmit}
                  disabled={submitMutation.isPending || justification.length < 10}
                  className={`flex-1 ${
                    decision === 'approved'
                      ? 'bg-green-600 hover:bg-green-700'
                      : 'bg-red-600 hover:bg-red-700'
                  }`}
                >
                  {submitMutation.isPending
                    ? 'Submitting...'
                    : decision === 'approved'
                    ? 'Approve Document'
                    : 'Deny Document'}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
