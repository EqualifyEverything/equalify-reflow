/**
 * API type definitions matching the FastAPI backend.
 * All types verified against OpenAPI spec.
 */

export interface JobSubmissionResponse {
  job_id: string;
  status: string;
  message: string;
  approval_url?: string;  // If PII detected
}

export type JobStatusType =
  | 'pending'
  | 'pii_scanning'
  | 'processing'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'denied';

export interface JobStatus {
  job_id: string;
  status: JobStatusType;
  created_at: string;
  updated_at: string;
  pii_detected?: boolean;
  error?: string;
}

export interface JobResult {
  job_id: string;
  status: string;
  output_url?: string;
  processing_time?: number;
  confidence_score?: number;
  error?: string;
}

export interface PIIEntity {
  entity_type: string;
  text: string;
  score: number;
  start: number;
  end: number;
}

export interface PIIReviewData {
  job_id: string;
  document_filename: string;
  pii_findings: PIIEntity[];
  detected_at: string;
}

export interface ApprovalDecision {
  decision: 'approved' | 'denied';
  justification: string;
  reviewed_by: string;
}

export interface HealthStatus {
  status: string;
  redis: boolean;
  s3: boolean;
  timestamp: string;
}

export interface QueueMetrics {
  queues: {
    pii_scan: number;
    processing: number;
    approval_pending: number;
  };
  timestamp: string;
}

export interface ApiError {
  detail: string;
}
