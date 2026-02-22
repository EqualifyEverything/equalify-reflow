import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { X, Send, Loader2, LogOut } from 'lucide-react';
import type { FeedbackItem, FeedbackPhase } from '@/types/feedback';

interface FeedbackPanelProps {
  items: FeedbackItem[];
  phase: FeedbackPhase;
  revisionRound: number;
  onRemoveItem: (id: string) => void;
  onSubmit: () => void;
  onExit: () => void;
}

export function FeedbackPanel({
  items,
  phase,
  revisionRound,
  onRemoveItem,
  onSubmit,
  onExit,
}: FeedbackPanelProps) {
  const isSubmitting = phase === 'submitting';

  return (
    <div className="w-72 flex-shrink-0 border-l bg-white flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-800">Feedback</h3>
          <div className="flex items-center gap-2">
            {revisionRound > 0 && (
              <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">
                Round {revisionRound}
              </span>
            )}
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">
              {items.length} item{items.length !== 1 ? 's' : ''}
            </span>
          </div>
        </div>
      </div>

      {/* Items list */}
      <div className="flex-1 overflow-y-auto">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-6 text-center">
            <p className="text-xs text-muted-foreground">
              Select text in the viewer to add feedback.
            </p>
            <p className="text-[10px] text-muted-foreground mt-1">
              Choose Edit for direct changes, or Comment to describe issues.
            </p>
          </div>
        ) : (
          items.map((item) => (
            <div
              key={item.id}
              className="px-3 py-2.5 border-b hover:bg-gray-50 transition-colors group"
            >
              <div className="flex items-start gap-1.5">
                <span
                  className={cn(
                    'text-[10px] font-medium px-1.5 py-0.5 rounded shrink-0 mt-0.5',
                    item.type === 'edit'
                      ? 'bg-blue-50 text-blue-700'
                      : 'bg-purple-50 text-purple-700',
                  )}
                >
                  {item.type === 'edit' ? 'Edit' : 'Comment'}
                </span>
                {item.page != null && (
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 shrink-0 mt-0.5">
                    p{item.page}
                  </span>
                )}
                <button
                  onClick={() => onRemoveItem(item.id)}
                  className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-red-50"
                  title="Remove"
                >
                  <X className="w-3 h-3 text-red-500" />
                </button>
              </div>
              {item.selector && (
                <p className="text-[10px] text-muted-foreground mt-1 line-clamp-1 font-mono">
                  "{truncate(item.selector.exact, 40)}"
                </p>
              )}
              <p className="text-xs text-gray-700 mt-1 line-clamp-2">
                {item.description}
              </p>
              {item.feedback_type && (
                <span className="inline-block mt-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                  {item.feedback_type}
                </span>
              )}
            </div>
          ))
        )}
      </div>

      {/* Footer actions */}
      <div className="px-3 py-3 border-t space-y-2">
        <Button
          size="sm"
          variant="primary"
          className="w-full h-8 text-xs gap-1.5"
          onClick={onSubmit}
          disabled={items.length === 0 || isSubmitting}
        >
          {isSubmitting ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Submitting...
            </>
          ) : (
            <>
              <Send className="w-3.5 h-3.5" />
              Submit Feedback ({items.length})
            </>
          )}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="w-full h-7 text-xs gap-1.5 text-muted-foreground"
          onClick={onExit}
          disabled={isSubmitting}
        >
          <LogOut className="w-3.5 h-3.5" />
          Exit Feedback Mode
        </Button>
      </div>
    </div>
  );
}

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + '\u2026';
}
