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
import type { StepResult, StepStatus, StageDefinition } from '@/types/pipeline-viewer';
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
  onDownloadVersion?: (stepIndex: number) => void;
}

function getStepStatus(step: StepResult): StepStatus {
  if (step.error) return 'error';
  if (step.skipped) return 'skipped';
  return 'success';
}

function StatusIcon({ status }: { status: StepStatus }) {
  switch (status) {
    case 'success':
      return <CheckCircle2 className="w-3 h-3 text-green-500" />;
    case 'skipped':
      return <SkipForward className="w-3 h-3 text-amber-500" />;
    case 'error':
      return <AlertCircle className="w-3 h-3 text-red-500" />;
  }
}

interface ResolvedStage {
  definition: StageDefinition;
  stepIndices: number[];
  status: 'pending' | 'active' | 'success' | 'error' | 'skipped';
  totalMs: number;
  totalCostCents: number;
}

function resolveStages(
  steps: StepResult[],
  processingStepName: string | null,
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
    });
  }

  // Compute stats and status for each stage
  for (const stage of resolved) {
    const stageSteps = stage.stepIndices.map((i) => steps[i]);
    stage.totalMs = stageSteps.reduce((sum, s) => sum + s.elapsed_ms, 0);
    stage.totalCostCents = stageSteps.reduce((sum, s) => sum + (s.cost_cents || 0), 0);

    if (stageSteps.length === 0) {
      // Check if processing step belongs to this stage
      if (processingStepName) {
        const processingBelongsHere = stage.definition.steps.length > 0 &&
          isProcessingInStage(stage.definition, processingStepName, steps);
        stage.status = processingBelongsHere ? 'active' : 'pending';
      } else {
        stage.status = 'pending';
      }
    } else if (stageSteps.some((s) => s.error)) {
      stage.status = 'error';
    } else if (stageSteps.every((s) => s.skipped)) {
      stage.status = 'skipped';
    } else {
      stage.status = 'success';
    }

    // Override to active if the current processing step belongs here
    if (processingStepName && stage.status !== 'error') {
      if (isProcessingInStage(stage.definition, processingStepName, steps)) {
        stage.status = 'active';
      }
    }
  }

  // Mark the stage containing activeStepIdx
  // (used for visual highlight, not status)
  return resolved;
}

function isProcessingInStage(
  stage: StageDefinition,
  processingStepName: string,
  _steps: StepResult[],
): boolean {
  // Match by display name heuristic — the processing event sends display_name
  const nameMap: Record<string, string[]> = {
    extraction: ['Docling Extraction', 'OCR Re-extraction'],
    analysis: ['PDF Classification', 'Structure Analysis'],
    headings: ['Heading Levels', 'Heading Reconciliation'],
    translation: ['Page Content Corrections', 'Code Block Languages'],
    assembly: ['Cross-Page Fixes', 'Final Cleanup'],
  };
  const names = nameMap[stage.name];
  if (names) return names.includes(processingStepName);
  // Review stage — anything else
  if (stage.name === 'review') return !Object.values(nameMap).flat().includes(processingStepName);
  return false;
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
  onDownloadVersion,
}: StageTabsProps) {
  const stages = resolveStages(steps, processingStepName ?? null);

  // Find which stage the active step belongs to
  const activeStageIdx = stages.findIndex((s) => s.stepIndices.includes(activeStepIdx));
  const activeStage = stages[activeStageIdx] ?? null;

  // Steps to show in the sub-tab row
  const visibleStepIndices = activeStage?.stepIndices ?? [];

  return (
    <div className="bg-white border-b">
      {/* Stage row */}
      <div className="flex items-center gap-0.5 px-4 py-1.5 border-b border-gray-100">
        {stages.map((stage, stageIdx) => {
          const Icon = STAGE_ICONS[stage.definition.name] ?? FileInput;
          const isActiveStage = stageIdx === activeStageIdx;
          const hasSteps = stage.stepIndices.length > 0;

          return (
            <button
              key={stage.definition.name}
              onClick={() => {
                // Click stage → select its first step
                if (stage.stepIndices.length > 0) {
                  onSelectStep(stage.stepIndices[0]);
                }
              }}
              disabled={!hasSteps}
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
            </button>
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

      {/* Sub-step row */}
      {visibleStepIndices.length > 0 && (
        <div className="flex items-center gap-1 overflow-x-auto px-4 py-1.5">
          {visibleStepIndices.map((idx) => {
            const step = steps[idx];
            const status = getStepStatus(step);
            const isActive = idx === activeStepIdx;

            return (
              <div key={step.name} className="flex items-center">
                <button
                  onClick={() => onSelectStep(idx)}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors border-b-2',
                    isActive
                      ? 'border-b-uic-blue text-uic-blue bg-uic-blue/5'
                      : 'border-b-transparent text-muted-foreground hover:text-foreground hover:bg-gray-50',
                  )}
                >
                  <StatusIcon status={status} />
                  <span>{step.display_name}</span>
                  <span className={cn(
                    'text-[10px] px-1 py-0.5 rounded',
                    isActive ? 'bg-uic-blue/10 text-uic-blue' : 'bg-gray-100 text-gray-500',
                  )}>
                    {(step.elapsed_ms / 1000).toFixed(1)}s
                  </span>
                  {step.cost_cents > 0 && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-green-50 text-green-700">
                      ${(step.cost_cents / 100).toFixed(4)}
                    </span>
                  )}
                  {step.changes.length > 0 && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-amber-50 text-amber-700">
                      {step.changes.length}
                    </span>
                  )}
                </button>
                {isActive && onDownloadVersion && step.version_after && !step.error && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDownloadVersion(idx);
                    }}
                    title={`Download ${step.version_after} markdown`}
                    className="p-1 ml-0.5 rounded text-muted-foreground hover:text-green-600 hover:bg-green-50 transition-colors"
                  >
                    <Download className="w-3 h-3" />
                  </button>
                )}
              </div>
            );
          })}

          {/* Processing placeholder for sub-step within active stage */}
          {processingStepName && activeStage?.status === 'active' && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium whitespace-nowrap text-muted-foreground border-b-2 border-b-transparent">
              <Loader2 className="w-3 h-3 animate-spin text-uic-blue" />
              <span>{processingStepName}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
