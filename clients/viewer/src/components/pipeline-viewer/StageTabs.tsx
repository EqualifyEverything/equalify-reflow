import { cn } from '@/lib/utils';
import {
  CheckCircle2,
  SkipForward,
  AlertCircle,
  Loader2,
  Download,
  FileInput,
  Search,
  Heading,
  Languages,
  Merge,
  MessageSquareDot,
} from 'lucide-react';
import type { StepResult, StageDefinition } from '@/types/pipeline-viewer';
import { PIPELINE_STAGES, REVIEW_STAGE } from '@/types/pipeline-viewer';

const STAGE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  extraction: FileInput,
  analysis: Search,
  headings: Heading,
  translation: Languages,
  assembly: Merge,
  review: MessageSquareDot,
};

interface StageTabsProps {
  steps: StepResult[];
  activeStepIdx: number;
  onSelectStep: (index: number) => void;
  processingStepName?: string | null;
  processingUserPhase?: string | null;
  onDownloadVersion?: (stepIndex: number) => void;
  onOpenChangesModal?: () => void;
}


interface ResolvedStage {
  definition: StageDefinition;
  stepIndices: number[];
  status: 'pending' | 'active' | 'success' | 'error' | 'skipped';
  totalMs: number;
  totalCostCents: number;
  totalChanges: number;
  lastStepIdx: number | null;
}

function resolveStages(
  steps: StepResult[],
  processingUserPhase: string | null,
): ResolvedStage[] {
  const knownStepNames = new Set(PIPELINE_STAGES.flatMap((s) => s.steps));
  const resolved: ResolvedStage[] = [];

  for (const stage of PIPELINE_STAGES) {
    const indices: number[] = [];
    for (let i = 0; i < steps.length; i++) {
      if (stage.steps.includes(steps[i].name)) {
        indices.push(i);
      }
    }
    resolved.push({
      definition: stage,
      stepIndices: indices,
      status: 'pending',
      totalMs: 0,
      totalCostCents: 0,
      totalChanges: 0,
      lastStepIdx: null,
    });
  }

  // Collect orphan steps (revision, feedback, etc.) into a review stage
  const orphanIndices: number[] = [];
  for (let i = 0; i < steps.length; i++) {
    if (!knownStepNames.has(steps[i].name)) {
      orphanIndices.push(i);
    }
  }
  if (orphanIndices.length > 0) {
    resolved.push({
      definition: REVIEW_STAGE,
      stepIndices: orphanIndices,
      status: 'pending',
      totalMs: 0,
      totalCostCents: 0,
      totalChanges: 0,
      lastStepIdx: null,
    });
  }

  // Compute stats and status for each stage
  for (const stage of resolved) {
    const stageSteps = stage.stepIndices.map((i) => steps[i]);
    stage.totalMs = stageSteps.reduce((sum, s) => sum + s.elapsed_ms, 0);
    stage.totalCostCents = stageSteps.reduce((sum, s) => sum + (s.cost_cents || 0), 0);
    stage.totalChanges = stageSteps.reduce((sum, s) => sum + s.changes.length, 0);
    stage.lastStepIdx = stage.stepIndices.length > 0
      ? stage.stepIndices[stage.stepIndices.length - 1]
      : null;

    if (stageSteps.length === 0) {
      const processingBelongsHere =
        stage.definition.steps.length > 0 &&
        isProcessingInStage(stage.definition, processingUserPhase);
      stage.status = processingBelongsHere ? 'active' : 'pending';
    } else if (stageSteps.some((s) => s.error)) {
      stage.status = 'error';
    } else if (stageSteps.every((s) => s.skipped)) {
      stage.status = 'skipped';
    } else {
      stage.status = 'success';
    }

    // Override to active if the current processing step belongs here
    if (stage.status !== 'error' && isProcessingInStage(stage.definition, processingUserPhase)) {
      stage.status = 'active';
    }
  }

  return resolved;
}

function isProcessingInStage(
  stage: StageDefinition,
  processingUserPhase: string | null,
): boolean {
  // Source-of-truth: SSE events carry a `user_phase` that is already resolved
  // server-side from the same PIPELINE_STAGES mapping used here (see
  // docs/reference/pipeline-phases.md).
  if (!processingUserPhase) return false;
  return stage.name === processingUserPhase;
}

function StageStatusIndicator({ status }: { status: ResolvedStage['status'] }) {
  switch (status) {
    case 'success':
      return <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />;
    case 'error':
      return <AlertCircle className="w-3.5 h-3.5 text-red-500" />;
    case 'skipped':
      return <SkipForward className="w-3.5 h-3.5 text-amber-500" />;
    case 'active':
      return <Loader2 className="w-3.5 h-3.5 animate-spin text-uic-blue" />;
    case 'pending':
      return <div className="w-3.5 h-3.5 rounded-full border-2 border-gray-300" />;
  }
}

export function StageTabs({
  steps,
  activeStepIdx,
  onSelectStep,
  processingStepName,
  processingUserPhase,
  onDownloadVersion,
  onOpenChangesModal,
}: StageTabsProps) {
  const stages = resolveStages(steps, processingUserPhase ?? null);

  // Find which stage the active step belongs to
  const activeStageIdx = stages.findIndex((s) => s.stepIndices.includes(activeStepIdx));

  return (
    <div className="bg-white border-b">
      {/* Stage row — parent tabs only */}
      <div className="flex items-center gap-0.5 px-4 py-1.5">
        {stages.map((stage, stageIdx) => {
          const Icon = STAGE_ICONS[stage.definition.name] ?? FileInput;
          const isActiveStage = stageIdx === activeStageIdx;
          const hasSteps = stage.stepIndices.length > 0;
          // Last child step for download
          const lastIdx = stage.lastStepIdx;
          const lastStep = lastIdx != null ? steps[lastIdx] : null;
          const canDownload = lastStep?.version_after && !lastStep.error;

          const isActiveProcessing = stage.status === 'active';
          return (
            <div key={stage.definition.name} className="flex items-center">
              <button
                onClick={() => {
                  // Click stage → select its last completed step
                  if (lastIdx != null) {
                    onSelectStep(lastIdx);
                  }
                }}
                disabled={!hasSteps && !isActiveProcessing}
                aria-current={isActiveProcessing ? 'step' : undefined}
                data-stage-name={stage.definition.name}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                  isActiveStage
                    ? 'bg-uic-blue/10 text-uic-blue'
                    : hasSteps
                      ? 'text-muted-foreground hover:text-foreground hover:bg-gray-50'
                      : 'text-gray-300 cursor-default',
                )}
              >
                <StageStatusIndicator status={stage.status} />
                <Icon className={cn('w-3.5 h-3.5', isActiveStage ? 'text-uic-blue' : '')} />
                <span>{stage.definition.label}</span>
                {stage.totalMs > 0 && (
                  <span className={cn(
                    'text-[10px] px-1 py-0.5 rounded',
                    isActiveStage ? 'bg-uic-blue/10 text-uic-blue' : 'bg-gray-100 text-gray-500',
                  )}>
                    {(stage.totalMs / 1000).toFixed(1)}s
                  </span>
                )}
                {stage.totalCostCents > 0 && (
                  <span className="text-[10px] px-1 py-0.5 rounded bg-green-50 text-green-700">
                    ${(stage.totalCostCents / 100).toFixed(4)}
                  </span>
                )}
                {stage.totalChanges > 0 && (
                  <span
                    role="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      // Select this stage first, then open modal
                      if (lastIdx != null) onSelectStep(lastIdx);
                      onOpenChangesModal?.();
                    }}
                    className="text-[10px] px-1 py-0.5 rounded bg-amber-50 text-amber-700 hover:bg-amber-100 cursor-pointer"
                  >
                    {stage.totalChanges}
                  </span>
                )}
              </button>
              {isActiveStage && canDownload && onDownloadVersion && lastIdx != null && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDownloadVersion(lastIdx);
                  }}
                  title={`Download ${lastStep!.version_after} markdown`}
                  className="p-1 ml-0.5 rounded text-muted-foreground hover:text-green-600 hover:bg-green-50 transition-colors"
                >
                  <Download className="w-3 h-3" />
                </button>
              )}
            </div>
          );
        })}

        {/* Processing indicator when no stage is active yet */}
        {processingStepName && !stages.some((s) => s.status === 'active') && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-uic-blue" />
            <span>{processingStepName}</span>
          </div>
        )}
      </div>
    </div>
  );
}
