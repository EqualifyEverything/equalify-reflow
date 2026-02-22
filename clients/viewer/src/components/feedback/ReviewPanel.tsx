import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Check,
  X,
  ChevronDown,
  ChevronUp,
  Loader2,
  ThumbsUp,
  Send,
} from 'lucide-react';
import type { CandidateChange, ChangeReview, ChangeDecision } from '@/types/feedback';

interface ReviewPanelProps {
  candidates: CandidateChange[];
  reviews: Map<string, ChangeReview>;
  isApplying: boolean;
  onSetDecision: (changeId: string, decision: ChangeDecision, comment?: string) => void;
  onApplyChanges: () => void;
  onApproveFinalize: () => void;
  onBackToCollecting?: () => void;
}

export function ReviewPanel({
  candidates,
  reviews,
  isApplying,
  onSetDecision,
  onApplyChanges,
  onApproveFinalize,
  onBackToCollecting,
}: ReviewPanelProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [rejectComment, setRejectComment] = useState<Record<string, string>>({});

  const reviewedCount = reviews.size;
  const totalCount = candidates.length;

  return (
    <div className="w-72 flex-shrink-0 border-l bg-white flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-800">Review Changes</h3>
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">
            {reviewedCount}/{totalCount}
          </span>
        </div>
      </div>

      {/* Candidate cards */}
      <div className="flex-1 overflow-y-auto">
        {totalCount === 0 && (
          <div className="flex flex-col items-center justify-center p-6 text-center">
            <p className="text-xs text-muted-foreground">
              No candidate changes were generated.
            </p>
            <p className="text-[10px] text-muted-foreground mt-1">
              The AI may not have found changes to suggest, or the selected text couldn't be located in the markdown. You can finalize the current version or go back to add more feedback.
            </p>
          </div>
        )}
        {candidates.map((candidate) => {
          const isExpanded = expandedId === candidate.id;
          const review = reviews.get(candidate.id);
          const decision = review?.decision;

          return (
            <div
              key={candidate.id}
              className={cn(
                'border-b transition-colors',
                decision === 'accept' && 'bg-green-50/50',
                decision === 'reject' && 'bg-red-50/50',
              )}
            >
              {/* Card header */}
              <button
                onClick={() => setExpandedId(isExpanded ? null : candidate.id)}
                className="w-full text-left px-3 py-2.5 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <span
                    className={cn(
                      'text-[10px] font-medium px-1.5 py-0.5 rounded',
                      candidate.source_type === 'direct_edit'
                        ? 'bg-green-50 text-green-700'
                        : 'bg-purple-50 text-purple-700',
                    )}
                  >
                    {candidate.source_type === 'direct_edit' ? 'Direct Edit' : 'AI Revision'}
                  </span>
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                    p{candidate.change.page}
                  </span>
                  {decision && (
                    <span
                      className={cn(
                        'text-[10px] font-medium px-1.5 py-0.5 rounded ml-auto',
                        decision === 'accept'
                          ? 'bg-green-100 text-green-700'
                          : 'bg-red-100 text-red-700',
                      )}
                    >
                      {decision === 'accept' ? 'Accepted' : 'Rejected'}
                    </span>
                  )}
                  {isExpanded ? (
                    <ChevronUp className="w-3 h-3 ml-auto text-muted-foreground" />
                  ) : (
                    <ChevronDown className="w-3 h-3 ml-auto text-muted-foreground" />
                  )}
                </div>
                <p className="text-xs text-muted-foreground line-clamp-2">
                  {candidate.source_description}
                </p>
              </button>

              {/* Expanded content */}
              {isExpanded && (
                <div className="px-3 pb-3 space-y-2">
                  {/* Diff view */}
                  <div className="space-y-1">
                    <div className="text-xs">
                      <span className="font-medium text-red-600">Before:</span>
                      <pre className="mt-0.5 p-1.5 bg-red-50 rounded text-[10px] whitespace-pre-wrap break-words max-h-20 overflow-auto">
                        {candidate.change.old_text || '(empty)'}
                      </pre>
                    </div>
                    <div className="text-xs">
                      <span className="font-medium text-green-600">After:</span>
                      <pre className="mt-0.5 p-1.5 bg-green-50 rounded text-[10px] whitespace-pre-wrap break-words max-h-20 overflow-auto">
                        {candidate.change.new_text || '(empty)'}
                      </pre>
                    </div>
                  </div>

                  {/* Reasoning */}
                  {candidate.change.reasoning && (
                    <p className="text-[10px] text-muted-foreground italic">
                      {candidate.change.reasoning}
                    </p>
                  )}

                  {/* Action buttons */}
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => onSetDecision(candidate.id, 'accept')}
                      className={cn(
                        'flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors',
                        decision === 'accept'
                          ? 'bg-green-600 text-white'
                          : 'bg-green-50 text-green-700 hover:bg-green-100',
                      )}
                    >
                      <Check className="w-3 h-3" />
                      Accept
                    </button>
                    <button
                      onClick={() => {
                        const comment = rejectComment[candidate.id];
                        onSetDecision(candidate.id, 'reject', comment || undefined);
                      }}
                      className={cn(
                        'flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors',
                        decision === 'reject'
                          ? 'bg-red-600 text-white'
                          : 'bg-red-50 text-red-700 hover:bg-red-100',
                      )}
                    >
                      <X className="w-3 h-3" />
                      Reject
                    </button>
                  </div>

                  {/* Rejection comment */}
                  {decision === 'reject' && (
                    <textarea
                      value={rejectComment[candidate.id] ?? ''}
                      onChange={(e) => {
                        const val = e.target.value;
                        setRejectComment((prev) => ({ ...prev, [candidate.id]: val }));
                        onSetDecision(candidate.id, 'reject', val || undefined);
                      }}
                      placeholder="Why? (optional, feeds next round)"
                      className="w-full h-12 px-2 py-1 text-[10px] border rounded resize-none focus:outline-none focus:ring-1 focus:ring-red-300"
                    />
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="px-3 py-3 border-t space-y-2">
        {totalCount > 0 && (
          <>
            <div className="text-[10px] text-muted-foreground text-center">
              {reviewedCount} of {totalCount} reviewed
            </div>
            <Button
              size="sm"
              variant="primary"
              className="w-full h-8 text-xs gap-1.5"
              onClick={onApplyChanges}
              disabled={reviewedCount === 0 || isApplying}
            >
              {isApplying ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Applying...
                </>
              ) : (
                <>
                  <Send className="w-3.5 h-3.5" />
                  Apply Changes
                </>
              )}
            </Button>
          </>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="w-full h-7 text-xs gap-1.5 text-green-700 hover:text-green-800 hover:bg-green-50"
          onClick={onApproveFinalize}
          disabled={isApplying}
        >
          <ThumbsUp className="w-3.5 h-3.5" />
          Approve & Finalize
        </Button>
        {onBackToCollecting && (
          <Button
            size="sm"
            variant="ghost"
            className="w-full h-7 text-xs gap-1.5 text-muted-foreground"
            onClick={onBackToCollecting}
            disabled={isApplying}
          >
            Add More Feedback
          </Button>
        )}
      </div>
    </div>
  );
}
