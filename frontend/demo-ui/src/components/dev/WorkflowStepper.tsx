import { cn } from '@/lib/utils'
import {
  Upload,
  Shield,
  UserCheck,
  Cog,
  FileCheck,
  ClipboardList,
  CheckCircle2,
  XCircle,
  Clock
} from 'lucide-react'

// Workflow steps in order
const WORKFLOW_STEPS = [
  { id: 'submit', label: 'Submit', icon: Upload },
  { id: 'pii_scan', label: 'PII Scan', icon: Shield },
  { id: 'pii_approval', label: 'PII Review', icon: UserCheck },
  { id: 'processing', label: 'Processing', icon: Cog },
  { id: 'correction_review', label: 'Correction Review', icon: FileCheck },
  { id: 'human_review', label: 'Human Review', icon: ClipboardList },
  { id: 'completed', label: 'Completed', icon: CheckCircle2 },
] as const

type StepId = typeof WORKFLOW_STEPS[number]['id']

// Map job status to workflow step
function getStepFromStatus(status: string): { step: StepId; isActive: boolean; isFailed: boolean } {
  switch (status) {
    case 'pending':
    case 'pii_scanning':
      return { step: 'pii_scan', isActive: true, isFailed: false }
    case 'awaiting_approval':
      return { step: 'pii_approval', isActive: true, isFailed: false }
    case 'processing':
      return { step: 'processing', isActive: true, isFailed: false }
    case 'awaiting_correction_approval':
      return { step: 'correction_review', isActive: true, isFailed: false }
    case 'needs_review':
      return { step: 'human_review', isActive: true, isFailed: false }
    case 'completed':
      return { step: 'completed', isActive: false, isFailed: false }
    case 'failed':
    case 'denied':
      return { step: 'submit', isActive: false, isFailed: true }
    default:
      return { step: 'submit', isActive: true, isFailed: false }
  }
}

// Check if a step is complete based on current step
function isStepComplete(stepId: StepId, currentStep: StepId): boolean {
  const stepIndex = WORKFLOW_STEPS.findIndex(s => s.id === stepId)
  const currentIndex = WORKFLOW_STEPS.findIndex(s => s.id === currentStep)
  return stepIndex < currentIndex
}

interface WorkflowStepperProps {
  status: string
  className?: string
  showLabels?: boolean
  compact?: boolean
}

export function WorkflowStepper({
  status,
  className,
  showLabels = true,
  compact = false
}: WorkflowStepperProps) {
  const { step: currentStep, isActive, isFailed } = getStepFromStatus(status)

  return (
    <div className={cn('w-full', className)}>
      <div className={cn(
        'flex items-center justify-between',
        compact ? 'gap-1' : 'gap-2'
      )}>
        {WORKFLOW_STEPS.map((step, index) => {
          const isComplete = isStepComplete(step.id, currentStep)
          const isCurrent = step.id === currentStep
          const Icon = step.icon

          // Determine step state for styling
          let stepState: 'complete' | 'active' | 'pending' | 'failed' = 'pending'
          if (isFailed && isCurrent) {
            stepState = 'failed'
          } else if (isComplete) {
            stepState = 'complete'
          } else if (isCurrent && isActive) {
            stepState = 'active'
          } else if (isCurrent && !isActive) {
            stepState = 'complete'
          }

          return (
            <div key={step.id} className="flex items-center flex-1">
              {/* Step circle/icon */}
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    'rounded-full flex items-center justify-center transition-all',
                    compact ? 'w-8 h-8' : 'w-10 h-10',
                    {
                      'bg-green-500 text-white': stepState === 'complete',
                      'bg-primary text-primary-foreground ring-4 ring-primary/20 animate-pulse': stepState === 'active',
                      'bg-red-500 text-white': stepState === 'failed',
                      'bg-muted text-muted-foreground': stepState === 'pending',
                    }
                  )}
                >
                  {stepState === 'complete' ? (
                    <CheckCircle2 className={compact ? 'h-4 w-4' : 'h-5 w-5'} />
                  ) : stepState === 'failed' ? (
                    <XCircle className={compact ? 'h-4 w-4' : 'h-5 w-5'} />
                  ) : stepState === 'active' ? (
                    <Clock className={cn(compact ? 'h-4 w-4' : 'h-5 w-5', 'animate-spin')} style={{ animationDuration: '3s' }} />
                  ) : (
                    <Icon className={compact ? 'h-4 w-4' : 'h-5 w-5'} />
                  )}
                </div>

                {/* Label */}
                {showLabels && (
                  <span
                    className={cn(
                      'text-center mt-1 whitespace-nowrap',
                      compact ? 'text-[10px]' : 'text-xs',
                      {
                        'text-green-600 dark:text-green-400 font-medium': stepState === 'complete',
                        'text-primary font-semibold': stepState === 'active',
                        'text-red-600 dark:text-red-400 font-medium': stepState === 'failed',
                        'text-muted-foreground': stepState === 'pending',
                      }
                    )}
                  >
                    {step.label}
                  </span>
                )}
              </div>

              {/* Connector line */}
              {index < WORKFLOW_STEPS.length - 1 && (
                <div
                  className={cn(
                    'flex-1 h-0.5 mx-2',
                    {
                      'bg-green-500': isComplete,
                      'bg-gradient-to-r from-primary to-muted': isCurrent && isActive,
                      'bg-red-500': isFailed && isCurrent,
                      'bg-muted': !isComplete && !isCurrent,
                    }
                  )}
                />
              )}
            </div>
          )
        })}
      </div>

      {/* Status text */}
      {!compact && (
        <div className="mt-3 text-center">
          <span className={cn(
            'text-sm font-medium px-3 py-1 rounded-full',
            {
              'bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300': status === 'completed',
              'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300': ['pii_scanning', 'processing'].includes(status),
              'bg-yellow-100 dark:bg-yellow-900/50 text-yellow-700 dark:text-yellow-300': ['awaiting_approval', 'awaiting_correction_approval'].includes(status),
              'bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300': status === 'needs_review',
              'bg-red-100 dark:bg-red-900/50 text-red-700 dark:text-red-300': ['failed', 'denied'].includes(status),
              'bg-muted text-muted-foreground': status === 'pending',
            }
          )}>
            {formatStatusText(status)}
          </span>
        </div>
      )}
    </div>
  )
}

function formatStatusText(status: string): string {
  switch (status) {
    case 'pending': return 'Pending'
    case 'pii_scanning': return 'Scanning for PII...'
    case 'awaiting_approval': return 'Awaiting PII Approval'
    case 'processing': return 'AI Processing...'
    case 'awaiting_correction_approval': return 'Awaiting Correction Review'
    case 'needs_review': return 'Awaiting Human Review'
    case 'completed': return 'Completed'
    case 'failed': return 'Failed'
    case 'denied': return 'Denied'
    default: return status
  }
}

// Mini version for lists
export function WorkflowStepperMini({ status }: { status: string }) {
  return <WorkflowStepper status={status} showLabels={false} compact />
}
