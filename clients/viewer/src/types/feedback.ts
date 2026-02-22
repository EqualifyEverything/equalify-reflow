import type { DocumentChange } from './pipeline-viewer';

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export type FeedbackItemType = 'edit' | 'comment';

export type FeedbackCategory = 'content' | 'formatting' | 'accessibility' | 'structure';

export type ChangeDecision = 'accept' | 'reject';

export type ReviewAction = 'approve' | 'request_changes';

export type FeedbackPhase =
  | 'collecting'
  | 'submitting'
  | 'reviewing'
  | 'applying'
  | 'finalized';

// ---------------------------------------------------------------------------
// Domain models (mirror backend Pydantic models)
// ---------------------------------------------------------------------------

export interface TextSelector {
  exact: string;
  prefix: string | null;
  suffix: string | null;
}

export interface FeedbackItem {
  id: string;
  type: FeedbackItemType;
  selector: TextSelector | null;
  section: string | null;
  page: number | null;
  new_text: string | null;
  description: string;
  feedback_type: FeedbackCategory | null;
}

export interface CandidateChange {
  id: string;
  change: DocumentChange;
  source_type: string;
  source_feedback_id: string;
  source_description: string;
}

export interface ChangeReview {
  change_id: string;
  decision: ChangeDecision;
  comment: string | null;
}

// ---------------------------------------------------------------------------
// API request / response types
// ---------------------------------------------------------------------------

export interface FeedbackRequest {
  items: FeedbackItem[];
}

export interface FeedbackResponse {
  candidates: CandidateChange[];
  revision_round: number;
}

export interface ReviewRequest {
  reviews: ChangeReview[];
  action: ReviewAction;
}

export interface ReviewResponse {
  applied_count: number;
  rejected_count: number;
  new_version: string | null;
  rejection_comments: string[];
  finalized: boolean;
}

export interface ApproveResponse {
  finalized: boolean;
  final_version: string;
}

export interface SessionStateResponse {
  session_id: string;
  revision_round: number;
  finalized: boolean;
  versions: string[];
  latest_markdown: string;
  pending_candidates: number;
  feedback_rounds: number;
}
