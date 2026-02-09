import { cn } from '@/lib/utils';
import { CheckCircle2, SkipForward, AlertCircle, Loader2 } from 'lucide-react';
import type { StepResult, StepStatus } from '@/types/pipeline-viewer';

interface StepTabsProps {
  steps: StepResult[];
  activeIndex: number;
  onSelect: (index: number) => void;
  processingStepName?: string | null;
}

function getStatus(step: StepResult): StepStatus {
  if (step.error) return 'error';
  if (step.skipped) return 'skipped';
  return 'success';
}

function StatusIcon({ status }: { status: StepStatus }) {
  switch (status) {
    case 'success':
      return <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />;
    case 'skipped':
      return <SkipForward className="w-3.5 h-3.5 text-amber-500" />;
    case 'error':
      return <AlertCircle className="w-3.5 h-3.5 text-red-500" />;
  }
}

export function StepTabs({ steps, activeIndex, onSelect, processingStepName }: StepTabsProps) {
  return (
    <div className="flex items-center gap-1 overflow-x-auto px-4 py-2 bg-white border-b">
      {steps.map((step, idx) => {
        const status = getStatus(step);
        const isActive = idx === activeIndex;

        return (
          <button
            key={step.name}
            onClick={() => onSelect(idx)}
            className={cn(
              'flex items-center gap-2 px-3 py-1.5 rounded-t text-sm font-medium whitespace-nowrap transition-colors border-b-2',
              isActive
                ? 'border-b-uic-blue text-uic-blue bg-uic-blue/5'
                : 'border-b-transparent text-muted-foreground hover:text-foreground hover:bg-gray-50',
            )}
          >
            <StatusIcon status={status} />
            <span>{step.display_name}</span>
            <span className={cn(
              'text-xs px-1.5 py-0.5 rounded',
              isActive ? 'bg-uic-blue/10 text-uic-blue' : 'bg-gray-100 text-gray-500',
            )}>
              {(step.elapsed_ms / 1000).toFixed(1)}s
            </span>
            {step.cost_cents > 0 && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-green-50 text-green-700">
                ${(step.cost_cents / 100).toFixed(4)}
              </span>
            )}
            {step.changes.length > 0 && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">
                {step.changes.length}
              </span>
            )}
          </button>
        );
      })}

      {/* Processing placeholder tab */}
      {processingStepName && (
        <div className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium whitespace-nowrap text-muted-foreground border-b-2 border-b-transparent">
          <Loader2 className="w-3.5 h-3.5 animate-spin text-uic-blue" />
          <span>{processingStepName}</span>
        </div>
      )}
    </div>
  );
}
