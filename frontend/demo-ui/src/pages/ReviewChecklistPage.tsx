import { useState, useMemo, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { api, ReviewItem } from '@/lib/api'
import { ReviewItemCard, ItemSelection } from '@/components/review/ReviewItemCard'
import { ReviewProgress } from '@/components/review/ReviewProgress'
import { ReviewFilters, FilterState } from '@/components/review/ReviewFilters'
import { JsonViewer } from '@/components/dev/JsonViewer'
import { WorkflowStepper } from '@/components/dev/WorkflowStepper'
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  Play,
  Loader2,
  XCircle,
  AlertTriangle,
  FileText,
  User,
  Send,
} from 'lucide-react'

// Map of item ID to selection state
type SelectionsMap = Record<string, ItemSelection>

export function ReviewChecklistPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [filters, setFilters] = useState<FilterState>({})
  const [forceApply, setForceApply] = useState(false)
  const [showRawData, setShowRawData] = useState(false)
  const [reviewerName, setReviewerName] = useState('')
  const [selections, setSelections] = useState<SelectionsMap>({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // Fetch checklist
  const {
    data: checklist,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['checklist', jobId, filters],
    queryFn: () => api.getChecklist(jobId!, filters),
    enabled: !!jobId,
  })

  // Fetch job data to get page images
  const { data: jobData } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.getJobStatus(jobId!),
    enabled: !!jobId,
  })

  // Apply reviews mutation
  const applyMutation = useMutation({
    mutationFn: () => api.applyReviews(jobId!, forceApply),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['checklist', jobId] })
      queryClient.invalidateQueries({ queryKey: ['job', jobId] })
      // Navigate to job detail on success
      if (data.status === 'completed' || data.markdown_url) {
        navigate(`/job/${jobId}`)
      }
    },
  })

  // Handle selection change for an item
  const handleSelectionChange = useCallback((itemId: string, selection: ItemSelection) => {
    setSelections((prev) => ({
      ...prev,
      [itemId]: selection,
    }))
  }, [])

  // Get selection for an item (with default)
  const getSelection = useCallback(
    (itemId: string): ItemSelection => {
      return (
        selections[itemId] || {
          selectedOptionId: null,
          customInput: '',
          isOther: false,
        }
      )
    },
    [selections]
  )

  // Submit all selections
  const handleSubmitSelections = async () => {
    if (!reviewerName.trim()) {
      setSubmitError('Please enter your name/email before submitting.')
      return
    }

    const itemsToSubmit = Object.entries(selections).filter(([itemId, sel]) => {
      const item = checklist?.items.find((i) => i.id === itemId)
      if (!item || item.reviewed_at) return false // Skip already reviewed
      return sel.selectedOptionId || (sel.isOther && sel.customInput.trim())
    })

    if (itemsToSubmit.length === 0) {
      setSubmitError('No selections to submit. Please make at least one selection.')
      return
    }

    setIsSubmitting(true)
    setSubmitError(null)

    try {
      // Submit each selection
      for (const [itemId, selection] of itemsToSubmit) {
        await api.submitReview(jobId!, itemId, {
          selected_option_id: selection.isOther ? null : selection.selectedOptionId,
          custom_input: selection.isOther ? selection.customInput : null,
          reviewed_by: reviewerName,
        })
      }

      // Clear selections for submitted items
      setSelections((prev) => {
        const newSelections = { ...prev }
        for (const [itemId] of itemsToSubmit) {
          delete newSelections[itemId]
        }
        return newSelections
      })

      // Refresh checklist
      refetch()
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to submit reviews')
    } finally {
      setIsSubmitting(false)
    }
  }

  // Compute unique categories, agents, and pages for filters
  const filterOptions = useMemo(() => {
    if (!checklist) return { categories: [], agents: [], pages: [] }
    const categories = Object.keys(checklist.by_category)
    const agents = Object.keys(checklist.by_agent)
    const pages = Object.keys(checklist.by_page).map(Number)
    return { categories, agents, pages }
  }, [checklist])

  // Filter items client-side for better UX
  const filteredItems = useMemo(() => {
    if (!checklist) return []
    let items = checklist.items

    if (filters.status) {
      items = items.filter((item) =>
        filters.status === 'completed' ? item.reviewed_at : !item.reviewed_at
      )
    }
    if (filters.category && checklist.by_category[filters.category]) {
      const categoryItemIds = new Set(
        checklist.by_category[filters.category].map((i) => i.id)
      )
      items = items.filter((item) => categoryItemIds.has(item.id))
    }
    if (filters.agent) {
      items = items.filter((item) => item.agent === filters.agent)
    }
    if (filters.page) {
      items = items.filter((item) => item.page_num === filters.page)
    }
    return items
  }, [checklist, filters])

  // Count selections ready to submit
  const selectionsCount = useMemo(() => {
    return Object.entries(selections).filter(([itemId, sel]) => {
      const item = checklist?.items.find((i) => i.id === itemId)
      if (!item || item.reviewed_at) return false
      return sel.selectedOptionId || (sel.isOther && sel.customInput.trim())
    }).length
  }, [selections, checklist])

  // Get page image URL for a specific page
  const getPageImageUrl = useCallback(
    (pageNum: number): string | undefined => {
      if (!jobData?.page_image_urls) return undefined
      // page_image_urls is 0-indexed, pageNum is 1-indexed
      return jobData.page_image_urls[pageNum - 1]
    },
    [jobData]
  )

  const canApply = checklist && checklist.completed_items === checklist.total_items
  const hasUnreviewed = checklist && checklist.completed_items < checklist.total_items

  // Loading state
  if (!jobId) {
    return (
      <div className="max-w-6xl mx-auto">
        <p className="text-red-600">Invalid job ID</p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="max-w-6xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center">
            <Loader2 className="h-12 w-12 text-muted-foreground mx-auto mb-4 animate-spin" />
            <p className="text-muted-foreground">Loading review checklist...</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !checklist) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" asChild>
            <Link to={`/job/${jobId}`}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Job
            </Link>
          </Button>
        </div>
        <Card>
          <CardContent className="p-8 text-center">
            <XCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
            <h2 className="text-xl font-bold text-destructive mb-2">Unable to Load Checklist</h2>
            <p className="text-muted-foreground mb-4">
              {error instanceof Error ? error.message : 'Failed to load review checklist'}
            </p>
            <Button onClick={() => refetch()}>Retry</Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Success state after applying reviews
  if (applyMutation.isSuccess && applyMutation.data.markdown_url) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <Card>
          <CardContent className="p-8 text-center">
            <CheckCircle2 className="h-16 w-16 text-green-500 dark:text-green-400 mx-auto mb-4" />
            <h2 className="text-2xl font-bold text-primary mb-2">Reviews Applied!</h2>
            <p className="text-muted-foreground mb-6">
              {applyMutation.data.reviews_applied} review(s) applied successfully.
              {applyMutation.data.failed_replacements > 0 && (
                <span className="text-yellow-600">
                  {' '}
                  ({applyMutation.data.failed_replacements} replacements failed)
                </span>
              )}
            </p>
            <div className="flex justify-center gap-4">
              <Button asChild className="bg-uic-red hover:bg-uic-red/90">
                <a
                  href={applyMutation.data.markdown_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Download className="h-4 w-4 mr-2" />
                  Download Markdown
                </a>
              </Button>
              <Button variant="outline" asChild>
                <Link to={`/job/${jobId}`}>View Job</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-4">
          <Button variant="outline" asChild>
            <Link to={`/job/${jobId}`}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Job
            </Link>
          </Button>
          <div>
            <h1 className="text-3xl font-bold text-primary">Human Review</h1>
            <p className="text-muted-foreground font-mono text-sm">{jobId}</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center space-x-2">
            <Checkbox
              id="show-raw"
              checked={showRawData}
              onCheckedChange={(checked) => setShowRawData(checked === true)}
            />
            <Label htmlFor="show-raw">Show Raw Data</Label>
          </div>
          <Badge variant="secondary">
            <FileText className="h-3 w-3 mr-1" />
            {checklist.total_items} items
          </Badge>
        </div>
      </div>

      {/* Workflow stepper */}
      <Card>
        <CardContent className="pt-6">
          <WorkflowStepper status="needs_review" />
        </CardContent>
      </Card>

      {/* Reviewer Name Input - Single place to enter name */}
      <Card className="border-primary/50 bg-primary/5">
        <CardContent className="pt-6">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-2">
              <User className="h-5 w-5 text-primary" />
              <Label htmlFor="reviewer-name" className="text-base font-medium">
                Your Name/Email:
              </Label>
            </div>
            <div className="flex-1 min-w-[250px]">
              <input
                id="reviewer-name"
                type="text"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                value={reviewerName}
                onChange={(e) => setReviewerName(e.target.value)}
                placeholder="Enter your name or email to sign reviews"
              />
            </div>
            {reviewerName.trim() && (
              <Badge className="bg-green-500 text-white">
                <CheckCircle2 className="h-3 w-3 mr-1" />
                Ready to review
              </Badge>
            )}
          </div>
          {!reviewerName.trim() && (
            <p className="text-sm text-muted-foreground mt-2">
              Enter your name or email above. This will be used to sign all your reviews.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Progress */}
      <ReviewProgress
        totalItems={checklist.total_items}
        completedItems={checklist.completed_items}
        criticalItems={checklist.critical_items}
        summary={checklist.summary}
      />

      {/* Filters */}
      <ReviewFilters
        categories={filterOptions.categories}
        agents={filterOptions.agents}
        pages={filterOptions.pages}
        activeFilters={filters}
        onFilterChange={setFilters}
      />

      {/* Review items */}
      <div className="space-y-4" style={{ overflowAnchor: 'none' }}>
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold text-primary">
            Review Items ({filteredItems.length})
          </h2>
          {selectionsCount > 0 && (
            <Badge className="bg-blue-500 text-white">
              {selectionsCount} selection{selectionsCount > 1 ? 's' : ''} ready
            </Badge>
          )}
        </div>

        {filteredItems.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center">
              <CheckCircle2 className="h-12 w-12 text-green-500 dark:text-green-400 mx-auto mb-4" />
              <p className="text-muted-foreground">
                {checklist.total_items === 0
                  ? 'No items need review!'
                  : 'No items match the current filters.'}
              </p>
            </CardContent>
          </Card>
        ) : (
          filteredItems.map((item: ReviewItem) => (
            <ReviewItemCard
              key={item.id}
              item={item}
              pageImageUrl={getPageImageUrl(item.page_num)}
              selection={getSelection(item.id)}
              onSelectionChange={handleSelectionChange}
            />
          ))
        )}
      </div>

      {/* Submit Selections Section */}
      {selectionsCount > 0 && (
        <Card className="border-2 border-blue-500/50 bg-blue-50/30 dark:bg-blue-900/20">
          <CardHeader>
            <CardTitle className="text-primary flex items-center gap-2">
              <Send className="h-5 w-5" />
              Submit Your Selections ({selectionsCount})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              You have {selectionsCount} selection{selectionsCount > 1 ? 's' : ''} ready to submit.
              Click below to save your reviews.
            </p>

            {!reviewerName.trim() && (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  Please enter your name/email at the top of the page before submitting.
                </AlertDescription>
              </Alert>
            )}

            {submitError && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>{submitError}</AlertDescription>
              </Alert>
            )}

            <Button
              onClick={handleSubmitSelections}
              disabled={isSubmitting || !reviewerName.trim()}
              className="w-full bg-blue-600 hover:bg-blue-700"
              size="lg"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Submitting Reviews...
                </>
              ) : (
                <>
                  <Send className="h-4 w-4 mr-2" />
                  Submit {selectionsCount} Review{selectionsCount > 1 ? 's' : ''}
                </>
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Apply reviews section - at the end */}
      <Card className="border-2 border-green-500/50">
        <CardHeader>
          <CardTitle className="text-primary flex items-center gap-2">
            <Play className="h-5 w-5" />
            Apply Reviews & Generate Final Markdown
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Once all items have been reviewed, click the button below to apply the corrections and
            generate the final markdown.
          </p>

          {hasUnreviewed && (
            <Alert>
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                {checklist.total_items - checklist.completed_items} item(s) still need review.
                Complete all reviews or enable "Force Apply" to proceed.
              </AlertDescription>
            </Alert>
          )}

          {hasUnreviewed && (
            <div className="flex items-center space-x-2">
              <Checkbox
                id="force-apply"
                checked={forceApply}
                onCheckedChange={(checked) => setForceApply(checked === true)}
              />
              <Label htmlFor="force-apply" className="text-sm">
                Force apply (skip unreviewed items)
              </Label>
            </div>
          )}

          {applyMutation.isError && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                {applyMutation.error instanceof Error
                  ? applyMutation.error.message
                  : 'Failed to apply reviews'}
              </AlertDescription>
            </Alert>
          )}

          <Button
            onClick={() => applyMutation.mutate()}
            disabled={(!canApply && !forceApply) || applyMutation.isPending}
            className="w-full bg-green-600 hover:bg-green-700"
            size="lg"
          >
            {applyMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Applying Reviews...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-2" />
                Apply All Reviews & Generate Final Markdown
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Raw data viewer */}
      {showRawData && (
        <JsonViewer
          data={checklist}
          title="Checklist API Response"
          keyFields={['total_items', 'completed_items', 'critical_items']}
        />
      )}
    </div>
  )
}
