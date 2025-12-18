import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { CheckCircle2, AlertTriangle, Clock } from 'lucide-react'

interface ReviewProgressProps {
  totalItems: number
  completedItems: number
  criticalItems: number
  summary?: string
}

export function ReviewProgress({
  totalItems,
  completedItems,
  criticalItems,
  summary,
}: ReviewProgressProps) {
  const progress = totalItems > 0 ? (completedItems / totalItems) * 100 : 0
  const remainingItems = totalItems - completedItems
  const isComplete = completedItems === totalItems && totalItems > 0

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="space-y-4">
          {/* Progress bar */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium text-foreground">Review Progress</span>
              <span className="text-muted-foreground">
                {completedItems} of {totalItems} items ({Math.round(progress)}%)
              </span>
            </div>
            <div className="h-3 bg-muted rounded-full overflow-hidden">
              <div
                className={cn(
                  'h-full transition-all duration-500 ease-out',
                  isComplete ? 'bg-green-500' : 'bg-primary'
                )}
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>

          {/* Stats row */}
          <div className="flex items-center gap-4 flex-wrap">
            {/* Completed */}
            <div className="flex items-center gap-2">
              <div
                className={cn(
                  'p-2 rounded-full',
                  isComplete
                    ? 'bg-green-100 dark:bg-green-900/50'
                    : 'bg-muted'
                )}
              >
                <CheckCircle2
                  className={cn(
                    'h-5 w-5',
                    isComplete
                      ? 'text-green-600 dark:text-green-400'
                      : 'text-muted-foreground'
                  )}
                />
              </div>
              <div>
                <p className="text-2xl font-bold text-foreground">{completedItems}</p>
                <p className="text-xs text-muted-foreground">Completed</p>
              </div>
            </div>

            {/* Remaining */}
            <div className="flex items-center gap-2">
              <div className="p-2 rounded-full bg-muted">
                <Clock className="h-5 w-5 text-muted-foreground" />
              </div>
              <div>
                <p className="text-2xl font-bold text-foreground">{remainingItems}</p>
                <p className="text-xs text-muted-foreground">Remaining</p>
              </div>
            </div>

            {/* Critical items warning */}
            {criticalItems > 0 && (
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-full bg-red-100 dark:bg-red-900/50">
                  <AlertTriangle className="h-5 w-5 text-red-600 dark:text-red-400" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-red-600 dark:text-red-400">
                    {criticalItems}
                  </p>
                  <p className="text-xs text-red-600 dark:text-red-400">Critical</p>
                </div>
              </div>
            )}
          </div>

          {/* Summary text */}
          {summary && (
            <p className="text-sm text-muted-foreground border-t border-border pt-3">
              {summary}
            </p>
          )}

          {/* Status badges */}
          <div className="flex items-center gap-2 flex-wrap">
            {isComplete ? (
              <Badge className="bg-green-500 text-white">
                <CheckCircle2 className="h-3 w-3 mr-1" />
                All items reviewed
              </Badge>
            ) : (
              <Badge variant="secondary">
                <Clock className="h-3 w-3 mr-1" />
                {remainingItems} items need review
              </Badge>
            )}

            {criticalItems > 0 && !isComplete && (
              <Badge variant="destructive">
                <AlertTriangle className="h-3 w-3 mr-1" />
                {criticalItems} low confidence items
              </Badge>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
