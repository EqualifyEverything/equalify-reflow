import { useState, useEffect } from 'react'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api } from '@/lib/api'
import {
  CheckCircle2,
  FileCheck,
  Eye,
  ArrowLeftRight,
  Loader2,
} from 'lucide-react'

interface CorrectionReviewModalProps {
  open: boolean
  onClose: () => void
  jobId: string
  token: string
  filename: string
  correctionSummary: {
    total_corrections: number
    auto_applied_count: number
    manual_review_count: number
    confidence_score: number
    corrections_by_type: Record<string, number>
  }
  originalMarkdownUrl: string
  correctedMarkdownUrl: string
  pageImageUrls?: string[]
}

export function CorrectionReviewModal({
  open,
  onClose,
  jobId,
  token,
  filename,
  correctionSummary,
  originalMarkdownUrl,
  correctedMarkdownUrl,
  pageImageUrls,
}: CorrectionReviewModalProps) {
  const queryClient = useQueryClient()
  const [decision, setDecision] = useState<'approved' | 'rejected'>('approved')
  const [justification, setJustification] = useState('')
  const [reviewedBy, setReviewedBy] = useState('')
  const [originalContent, setOriginalContent] = useState<string | null>(null)
  const [correctedContent, setCorrectedContent] = useState<string | null>(null)
  const [loadingContent, setLoadingContent] = useState(false)
  const [activeTab, setActiveTab] = useState('summary')

  // Load markdown content when modal opens
  useEffect(() => {
    if (open && !originalContent && !correctedContent) {
      setLoadingContent(true)
      Promise.all([
        api.fetchMarkdown(originalMarkdownUrl),
        api.fetchMarkdown(correctedMarkdownUrl),
      ])
        .then(([original, corrected]) => {
          setOriginalContent(original)
          setCorrectedContent(corrected)
        })
        .catch(console.error)
        .finally(() => setLoadingContent(false))
    }
  }, [open, originalMarkdownUrl, correctedMarkdownUrl, originalContent, correctedContent])

  const submitMutation = useMutation({
    mutationFn: () =>
      api.submitCorrectionDecision(jobId, {
        token,
        decision,
        reviewed_by: reviewedBy,
        justification,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', jobId] })
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

  const confidenceColor =
    correctionSummary.confidence_score >= 0.9
      ? 'text-green-600 dark:text-green-400'
      : correctionSummary.confidence_score >= 0.7
      ? 'text-yellow-600 dark:text-yellow-400'
      : 'text-red-600 dark:text-red-400'

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-primary">
            <FileCheck className="h-5 w-5" />
            Review AI Corrections
          </DialogTitle>
        </DialogHeader>

        {submitMutation.isSuccess ? (
          <div className="text-center py-8">
            <CheckCircle2 className="h-16 w-16 text-green-500 dark:text-green-400 mx-auto mb-4" />
            <h3 className="text-xl font-semibold text-primary mb-2">
              Decision Submitted
            </h3>
            <p className="text-muted-foreground mb-4">
              Corrections have been {decision}.
              {decision === 'approved' && ' The corrected markdown is now final.'}
            </p>
            <Button onClick={handleClose}>Close</Button>
          </div>
        ) : (
          <div className="flex flex-col flex-1 overflow-hidden">
            {/* Document info bar */}
            <div className="flex items-center justify-between bg-muted/50 rounded-lg p-3 mb-4">
              <div>
                <p className="text-sm text-muted-foreground">Document</p>
                <p className="font-mono text-sm">{filename}</p>
              </div>
              <div className="flex items-center gap-4">
                <div className="text-center">
                  <span className="text-2xl font-bold text-purple-600 dark:text-purple-400">
                    {correctionSummary.total_corrections}
                  </span>
                  <p className="text-xs text-muted-foreground">total</p>
                </div>
                <div className="text-center">
                  <span className="text-2xl font-bold text-green-600 dark:text-green-400">
                    {correctionSummary.auto_applied_count}
                  </span>
                  <p className="text-xs text-muted-foreground">auto-applied</p>
                </div>
                <div className="text-center">
                  <span className="text-2xl font-bold text-amber-600 dark:text-amber-400">
                    {correctionSummary.manual_review_count}
                  </span>
                  <p className="text-xs text-muted-foreground">need review</p>
                </div>
                <div className="text-center">
                  <span className={`text-2xl font-bold ${confidenceColor}`}>
                    {(correctionSummary.confidence_score * 100).toFixed(0)}%
                  </span>
                  <p className="text-xs text-muted-foreground">confidence</p>
                </div>
              </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
              <TabsList className="grid w-full grid-cols-3">
                <TabsTrigger value="summary">Summary</TabsTrigger>
                <TabsTrigger value="diff">
                  <ArrowLeftRight className="h-4 w-4 mr-1" />
                  Compare
                </TabsTrigger>
                <TabsTrigger value="preview">
                  <Eye className="h-4 w-4 mr-1" />
                  Pages
                </TabsTrigger>
              </TabsList>

              <div className="flex-1 overflow-y-auto mt-4">
                <TabsContent value="summary" className="mt-0">
                  <div className="space-y-4">
                    {/* Corrections by type */}
                    <div>
                      <h4 className="font-semibold mb-2">Corrections by Type</h4>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(correctionSummary.corrections_by_type).map(
                          ([type, count]) => (
                            <Badge key={type} variant="outline" className="text-sm">
                              {type.replace(/_/g, ' ')}: {count}
                            </Badge>
                          )
                        )}
                      </div>
                    </div>

                    {/* Quick links */}
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" asChild>
                        <a href={originalMarkdownUrl} target="_blank" rel="noopener noreferrer">
                          View Original
                        </a>
                      </Button>
                      <Button variant="outline" size="sm" asChild>
                        <a href={correctedMarkdownUrl} target="_blank" rel="noopener noreferrer">
                          View Corrected
                        </a>
                      </Button>
                    </div>
                  </div>
                </TabsContent>

                <TabsContent value="diff" className="mt-0">
                  {loadingContent ? (
                    <div className="flex items-center justify-center py-8">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                      <span className="ml-2 text-muted-foreground">Loading content...</span>
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <h4 className="font-semibold mb-2 text-sm">Original</h4>
                        <pre className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded p-3 text-xs overflow-auto max-h-64 whitespace-pre-wrap">
                          {originalContent || 'Failed to load'}
                        </pre>
                      </div>
                      <div>
                        <h4 className="font-semibold mb-2 text-sm">Corrected</h4>
                        <pre className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded p-3 text-xs overflow-auto max-h-64 whitespace-pre-wrap">
                          {correctedContent || 'Failed to load'}
                        </pre>
                      </div>
                    </div>
                  )}
                </TabsContent>

                <TabsContent value="preview" className="mt-0">
                  {pageImageUrls && pageImageUrls.length > 0 ? (
                    <div className="grid grid-cols-3 gap-2">
                      {pageImageUrls.map((url, idx) => (
                        <a
                          key={idx}
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="border rounded overflow-hidden hover:ring-2 ring-purple-500"
                        >
                          <img
                            src={url}
                            alt={`Page ${idx + 1}`}
                            className="w-full h-auto"
                          />
                          <p className="text-xs text-center py-1 bg-muted">
                            Page {idx + 1}
                          </p>
                        </a>
                      ))}
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-center py-4">
                      No page previews available
                    </p>
                  )}
                </TabsContent>
              </div>
            </Tabs>

            {/* Decision Form */}
            <div className="border-t pt-4 mt-4 space-y-4">
              <div className="grid grid-cols-2 gap-4">
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
                    onValueChange={(v) => setDecision(v as 'approved' | 'rejected')}
                    className="flex gap-4"
                  >
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="approved" id="correction-approved" />
                      <Label htmlFor="correction-approved" className="font-normal">
                        Approve
                      </Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="rejected" id="correction-rejected" />
                      <Label htmlFor="correction-rejected" className="font-normal">
                        Reject
                      </Label>
                    </div>
                  </RadioGroup>
                </div>
              </div>

              <div className="space-y-2">
                <Label>Justification</Label>
                <Textarea
                  value={justification}
                  onChange={(e) => setJustification(e.target.value)}
                  placeholder="Explain your decision..."
                  rows={2}
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

              <div className="flex gap-2">
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
                    ? 'Approve Corrections'
                    : 'Reject Corrections'}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
