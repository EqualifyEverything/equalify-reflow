/**
 * API type definitions matching the FastAPI backend.
 * Re-exports types from api.ts for backward compatibility.
 */

// Re-export all types from the API client
export type {
  JobStatus,
  SubmitResponse as JobSubmissionResponse,
  HealthCheck as HealthStatus,
  QueueMetrics,
  PIIEntity,
  PIIReviewData,
  ApprovalDecision,
  ApprovalResponse,
  CorrectionItem,
  CorrectionReviewData,
  CorrectionDecision,
  CorrectionResponse,
  JobResult,
  LLMCost,
} from '@/lib/api';

// Status type for convenience
export type JobStatusType =
  | 'pending'
  | 'pii_scanning'
  | 'processing'
  | 'awaiting_approval'
  | 'awaiting_correction_approval'
  | 'completed'
  | 'failed'
  | 'denied';

export interface ApiError {
  detail: string;
}
