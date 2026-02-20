import { cn } from '@/lib/utils';
import { Loader2, MessageSquare } from 'lucide-react';
import type { FeedbackPhase } from '@/types/feedback';

interface FeedbackStatusBarProps {
  phase: FeedbackPhase;
  revisionRound: number;
  itemCount: number;
}

const PHASE_CONFIG: Record<FeedbackPhase, { label: string; bg: string; text: string }> = {
  collecting: { label: 'Collecting', bg: 'bg-blue-50', text: 'text-blue-700' },
  submitting: { label: 'Submitting', bg: 'bg-amber-50', text: 'text-amber-700' },
  reviewing: { label: 'Reviewing', bg: 'bg-purple-50', text: 'text-purple-700' },
  applying: { label: 'Applying', bg: 'bg-amber-50', text: 'text-amber-700' },
  finalized: { label: 'Finalized', bg: 'bg-green-50', text: 'text-green-700' },
};

export function FeedbackStatusBar({ phase, revisionRound, itemCount }: FeedbackStatusBarProps) {
  const config = PHASE_CONFIG[phase];
  const isLoading = phase === 'submitting' || phase === 'applying';

  return (
    <div className="flex items-center gap-2">
      <MessageSquare className="w-3.5 h-3.5 text-muted-foreground" />
      <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded flex items-center gap-1', config.bg, config.text)}>
        {isLoading && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
        {config.label}
      </span>
      {revisionRound > 0 && (
        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
          Round {revisionRound + 1}
        </span>
      )}
      {phase === 'collecting' && itemCount > 0 && (
        <span className="text-[10px] text-muted-foreground">
          {itemCount} pending
        </span>
      )}
    </div>
  );
}
