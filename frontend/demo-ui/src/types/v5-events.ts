/**
 * V5 Pipeline Event Types
 *
 * These types match the SSE events emitted by the V5 processing pipeline.
 */

export type V5EventType =
  | 'docling:started'
  | 'docling:complete'
  | 'planning:started'
  | 'planning:structure'
  | 'planning:page_summarized'
  | 'planning:complete'
  | 'job:created'
  | 'job:started'
  | 'job:completed'
  | 'edit:committed'
  | 'edit:validated'
  | 'agent:thinking'
  | 'verification:started'
  | 'verification:page_verified'
  | 'verification:complete'
  | 'final_pass:started'
  | 'final_pass:complete'
  | 'recovery:started'
  | 'recovery:complete'
  | 'processing:complete'
  | 'processing:error';

export interface BaseEventData {
  timestamp?: string;
  job_id?: string;
}

export interface DoclingStartedData extends BaseEventData {
  filename?: string;
}

export interface DoclingCompleteData extends BaseEventData {
  pages_extracted: number;
}

export interface PlanningStructureData extends BaseEventData {
  document_title: string;
  document_type: string;
}

export interface PlanningPageSummarizedData extends BaseEventData {
  page_num: number;
  summary: string;
}

export interface PlanningCompleteData extends BaseEventData {
  total_jobs: number;
}

export interface JobStartedData extends BaseEventData {
  job_id: string;
  page: number;
  job_type?: string;
}

export interface JobCompletedData extends BaseEventData {
  job_id: string;
  page: number;
  edits_made: number;
}

export interface EditCommittedData extends BaseEventData {
  ledger_entry: {
    target: string;
    action: string;
    page: number;
    before: string;
    after: string;
    reasoning: string;
  };
}

export interface EditValidatedData extends BaseEventData {
  target: string;
  approved: boolean;
  feedback?: string;
}

export interface AgentThinkingData extends BaseEventData {
  job_id: string;
  message_type: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  tool_result_preview?: string;
  content_preview?: string;
}

export interface VerificationPageVerifiedData extends BaseEventData {
  page_num: number;
  passed: boolean;
  confidence: number;
}

export interface VerificationCompleteData extends BaseEventData {
  passed: boolean;
  pages_passed: number;
}

export interface RecoveryStartedData extends BaseEventData {
  pages_to_recover: number[];
}

export interface RecoveryCompleteData extends BaseEventData {
  pages_recovered: number[];
}

export interface ProcessingCompleteData extends BaseEventData {
  success: boolean;
  total_edits: number;
  total_cost: number;
  total_duration_ms: number;
}

export interface ProcessingErrorData extends BaseEventData {
  error: string;
}

export type V5EventData =
  | DoclingStartedData
  | DoclingCompleteData
  | PlanningStructureData
  | PlanningPageSummarizedData
  | PlanningCompleteData
  | JobStartedData
  | JobCompletedData
  | EditCommittedData
  | EditValidatedData
  | AgentThinkingData
  | VerificationPageVerifiedData
  | VerificationCompleteData
  | RecoveryStartedData
  | RecoveryCompleteData
  | ProcessingCompleteData
  | ProcessingErrorData
  | BaseEventData;

export interface V5Event {
  type: V5EventType;
  data: V5EventData;
  timestamp: Date;
  id: string;
}

export interface Decision {
  id: string;
  approved: boolean;
  target: string;
  page: number;
  action: string;
  before: string;
  after: string;
  reasoning: string;
  feedback?: string;
}

export interface AgentActivity {
  jobId: string;
  page: number;
  events: AgentThinkingData[];
}

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';
