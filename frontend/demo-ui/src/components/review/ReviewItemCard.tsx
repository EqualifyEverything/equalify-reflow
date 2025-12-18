import { useState } from 'react'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ReviewItem } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ImageModal } from './ImageModal'
import {
  CheckCircle2,
  Star,
  MessageSquare,
  FileText,
  Expand,
  Image as ImageIcon,
} from 'lucide-react'

// Selection state for a single item
export interface ItemSelection {
  selectedOptionId: string | null
  customInput: string
  isOther: boolean
}

interface ReviewItemCardProps {
  item: ReviewItem
  pageImageUrl?: string // Page image from job data
  selection: ItemSelection
  onSelectionChange: (itemId: string, selection: ItemSelection) => void
}

export function ReviewItemCard({
  item,
  pageImageUrl,
  selection,
  onSelectionChange,
}: ReviewItemCardProps) {
  const [imageModalOpen, setImageModalOpen] = useState(false)

  const isAlreadyReviewed = !!item.reviewed_at

  // Use visual_context_url if available, otherwise fall back to pageImageUrl
  const imageUrl = item.visual_context_url || pageImageUrl

  const handleOptionChange = (value: string) => {
    if (value === 'other') {
      onSelectionChange(item.id, {
        selectedOptionId: null,
        customInput: selection.customInput,
        isOther: true,
      })
    } else {
      onSelectionChange(item.id, {
        selectedOptionId: value,
        customInput: '',
        isOther: false,
      })
    }
  }

  const handleCustomInputChange = (value: string) => {
    onSelectionChange(item.id, {
      ...selection,
      customInput: value,
    })
  }

  // Filter out "custom alt text" options - we already have "Other (custom input)"
  const filteredOptions = item.options.filter(
    (option) => !option.label.toLowerCase().includes('custom alt text')
  )

  // Confidence badge color
  const confidenceColor =
    item.agent_confidence >= 0.9
      ? 'bg-green-500'
      : item.agent_confidence >= 0.7
      ? 'bg-yellow-500'
      : 'bg-red-500'

  // Check if this item has a selection
  const hasSelection = selection.selectedOptionId || (selection.isOther && selection.customInput.trim())

  return (
    <>
      <Card
        className={cn(
          'transition-all',
          isAlreadyReviewed &&
            'opacity-60 border-green-300 dark:border-green-700 bg-green-50/30 dark:bg-green-900/20',
          hasSelection && !isAlreadyReviewed &&
            'border-primary/50 bg-primary/5'
        )}
      >
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="outline" className="capitalize">
                {item.agent.replace(/_/g, ' ')}
              </Badge>
              <Badge variant="secondary">
                <FileText className="h-3 w-3 mr-1" />
                Page {item.page_num}
              </Badge>
              <Badge className={cn('text-white', confidenceColor)}>
                {(item.agent_confidence * 100).toFixed(0)}% conf
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              {hasSelection && !isAlreadyReviewed && (
                <Badge className="bg-blue-500 text-white">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  Selected
                </Badge>
              )}
              {isAlreadyReviewed && (
                <Badge className="bg-green-500 text-white">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  Reviewed
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Question */}
          <div>
            <h3 className="font-medium text-lg text-primary">{item.question}</h3>
          </div>

          {/* Context */}
          <div className="bg-muted rounded-lg p-3 border border-border">
            <p className="text-xs text-muted-foreground mb-1 font-medium">Context:</p>
            <p className="text-sm font-mono whitespace-pre-wrap break-words text-foreground">
              {item.context}
            </p>
          </div>

          {/* Agent recommendation */}
          <div className="bg-purple-50 dark:bg-purple-900/30 rounded-lg p-3 border border-purple-200 dark:border-purple-700">
            <div className="flex items-start gap-2">
              <MessageSquare className="h-4 w-4 text-purple-600 dark:text-purple-400 mt-0.5 flex-shrink-0" />
              <div>
                <p className="text-xs text-purple-700 dark:text-purple-300 font-medium mb-1">
                  Agent says:
                </p>
                <p className="text-sm text-purple-800 dark:text-purple-200">
                  {item.agent_recommendation}
                </p>
              </div>
            </div>
          </div>

          {/* Visual context image - expandable */}
          {imageUrl ? (
            <div className="border rounded-lg overflow-hidden group relative">
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault()
                  setImageModalOpen(true)
                }}
                className="w-full focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 rounded-lg"
              >
                <img
                  src={imageUrl}
                  alt={`Visual context from PDF page ${item.page_num}`}
                  className="max-w-full h-auto cursor-pointer"
                />
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors flex items-center justify-center">
                  <div className="opacity-0 group-hover:opacity-100 transition-opacity bg-black/70 text-white px-3 py-2 rounded-lg flex items-center gap-2">
                    <Expand className="h-4 w-4" />
                    Click to expand
                  </div>
                </div>
              </button>
              <p className="text-xs text-center py-1 bg-muted text-muted-foreground">
                Page {item.page_num} - Click image to expand
              </p>
            </div>
          ) : (
            <div className="border border-dashed rounded-lg p-4 text-center bg-muted/30">
              <ImageIcon className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
              <p className="text-sm text-muted-foreground">
                No page image available for page {item.page_num}
              </p>
            </div>
          )}

          {/* Options - selection only, no submit */}
          {!isAlreadyReviewed && (
            <div
              className="space-y-3"
              style={{ overflowAnchor: 'none' }}
            >
              <p className="text-sm font-medium text-muted-foreground">Select an option:</p>
              <RadioGroup
                value={selection.isOther ? 'other' : selection.selectedOptionId || undefined}
                onValueChange={handleOptionChange}
              >
                {filteredOptions.map((option) => (
                  <div
                    key={option.id}
                    className={cn(
                      'flex items-start space-x-3 p-3 rounded-lg border transition-colors',
                      selection.selectedOptionId === option.id && !selection.isOther
                        ? 'border-primary bg-primary/10'
                        : 'border-border hover:border-muted-foreground/50'
                    )}
                  >
                    <RadioGroupItem
                      value={option.id}
                      id={`${item.id}-${option.id}`}
                      className="mt-0.5"
                    />
                    <Label
                      htmlFor={`${item.id}-${option.id}`}
                      className="flex-1 cursor-pointer"
                    >
                      <div className="flex items-center gap-2">
                        <span>{option.label}</span>
                        {option.is_recommended && (
                          <Badge className="bg-yellow-400 text-yellow-900 text-xs">
                            <Star className="h-3 w-3 mr-1" />
                            Recommended
                          </Badge>
                        )}
                      </div>
                      {option.action === 'replace' && option.replacement_text && (
                        <p className="text-xs text-muted-foreground mt-1">
                          Will replace with:{' '}
                          <code className="bg-muted px-1 rounded">
                            {option.replacement_text}
                          </code>
                        </p>
                      )}
                    </Label>
                  </div>
                ))}

                {/* Other option */}
                <div
                  className={cn(
                    'flex items-start space-x-3 p-3 rounded-lg border transition-colors',
                    selection.isOther
                      ? 'border-primary bg-primary/10'
                      : 'border-border hover:border-muted-foreground/50'
                  )}
                >
                  <RadioGroupItem
                    value="other"
                    id={`${item.id}-other`}
                    className="mt-0.5"
                  />
                  <Label
                    htmlFor={`${item.id}-other`}
                    className="flex-1 cursor-pointer"
                  >
                    <span>Other (custom input)</span>
                  </Label>
                </div>
              </RadioGroup>

              {/* Custom input textarea */}
              {selection.isOther && (
                <div className="ml-6 space-y-2">
                  <Textarea
                    placeholder="Enter your custom correction..."
                    value={selection.customInput}
                    onChange={(e) => handleCustomInputChange(e.target.value)}
                    rows={3}
                  />
                </div>
              )}
            </div>
          )}

          {/* Already reviewed info */}
          {isAlreadyReviewed && (
            <div className="text-sm text-muted-foreground border-t border-border pt-3">
              <p>
                Reviewed by{' '}
                <span className="font-medium text-foreground">{item.reviewed_by}</span> on{' '}
                {new Date(item.reviewed_at!).toLocaleString()}
              </p>
              {item.custom_input && (
                <p className="mt-1">
                  Custom input:{' '}
                  <code className="bg-muted px-1 rounded">{item.custom_input}</code>
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Image expansion modal */}
      {imageUrl && (
        <ImageModal
          open={imageModalOpen}
          onClose={() => setImageModalOpen(false)}
          src={imageUrl}
          alt={`Visual context from PDF page ${item.page_num}`}
          pageNum={item.page_num}
        />
      )}
    </>
  )
}
