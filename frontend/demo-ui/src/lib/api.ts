/**
 * API client for Equalify PDF Converter backend.
 * Types match the FastAPI backend schemas.
 *
 * Authentication: The demo UI is served from the same origin as the API
 * and protected by HTTP Basic Auth. The backend allows same-origin requests
 * from /demo without requiring an API key.
 */

const API_URL = import.meta.env.VITE_API_URL || ''

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  }

  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  return response.json()
}

// ============================================================================
// Types - Match backend API schemas
// ============================================================================

export interface SubmitResponse {
  job_id: string
  status: string
  estimated_completion_minutes: number
  created_at: string
}

export interface PIIEntity {
  entity_type: string
  text: string
  score: number
  start?: number
  end?: number
}

export interface PageLLMUsage {
  page: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  estimated_cost_cents: number
}

export interface LLMCost {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  estimated_cost_cents: number
  estimated_cost_dollars: number
}

export interface CorrectionSummary {
  total_corrections: number
  auto_applied_count: number
  manual_review_count: number
  confidence_score: number
  corrections_by_type: Record<string, number>
}

export interface CorrectionDecisionRecord {
  decision: 'approved' | 'rejected' | 'auto_completed'
  reviewed_by: string
  reviewed_at: string
  justification: string
}

/**
 * Job status response - polymorphic based on status field.
 * All optional fields accommodate different status states.
 */
export interface JobStatus {
  job_id: string
  status: string
  filename?: string
  created_at: string
  updated_at: string

  // For pii_scanning and processing statuses
  estimated_completion_minutes?: number

  // For awaiting_approval status
  pii_findings?: PIIEntity[]
  approval_token?: string
  approval_expires_at?: string
  approval_url?: string

  // For awaiting_correction_approval status (legacy)
  correction_summary?: CorrectionSummary
  corrections?: CorrectionItem[]
  review_url?: string
  original_markdown_url?: string
  corrected_markdown_url?: string
  page_image_urls?: string[]
  llm_cost?: LLMCost

  // For needs_review status (Phase 5)
  review_item_count?: number
  review_checklist_summary?: ChecklistSummary
  processing_confidence?: number

  // For completed status
  markdown_url?: string
  confidence_score?: number
  correction_decision?: CorrectionDecisionRecord

  // For failed status
  error?: string

  // For denied status
  reason?: string
}

export interface HealthCheck {
  status: string
  checks: {
    redis: boolean
    s3: boolean
    queue_depth: number
  }
}

export interface QueueMetrics {
  queues: {
    pii_scan: number
    processing: number
    approval_pending: number
  }
  timestamp: string
}

// PII Review types
export interface PIIReviewData {
  job_id: string
  document_filename?: string
  created_at: string
  detected_at?: string
  pii_findings: PIIEntity[]
  status: string
}

export interface ApprovalDecision {
  decision: 'approved' | 'denied'
  justification: string
  reviewed_by: string
}

export interface ApprovalResponse {
  success: boolean
  job_id: string
  decision: string
  message: string
}

// Correction Review types
export interface CorrectionItem {
  page: number
  type: string
  original_snippet: string
  corrected_snippet: string
  confidence: number
  explanation: string
  is_auto_applied: boolean
}

export interface CorrectionURLs {
  original_markdown: string
  corrected_markdown: string
  page_images: string[]
}

export interface CorrectionReviewData {
  job_id: string
  total_corrections: number
  auto_applied_count: number
  manual_review_count: number
  overall_confidence: number
  by_type: Record<string, number>
  by_page: Record<number, number>
  corrections: CorrectionItem[]
  urls: CorrectionURLs
  expires_at: string
}

export interface CorrectionDecision {
  token: string
  decision: 'approved' | 'rejected'
  reviewed_by: string
  justification: string
}

export interface CorrectionResponse {
  job_id: string
  status: string
  decision: string
  message: string
}

// Processing Phases types
export interface PageFeatureSummary {
  page_num: number
  has_images: boolean
  image_count: number
  has_tables: boolean
  table_count: number
  has_lists: boolean
  complexity_score: number
}

// Phase 5: DocumentSummary - Glass-box transparency for analysis
export interface DocumentSummary {
  topic_summary: string
  structure_summary: string
  key_entities: string[]
  domain_terms: string[]
  audience_level: string
  expected_elements: string[]
}

export interface AnalysisPhase {
  status: string
  document_title?: string
  document_type?: string
  total_pages?: number
  layout_type?: string
  required_agents: string[]
  analysis_confidence?: number
  page_features: PageFeatureSummary[]
  heading_tree?: Record<string, unknown>
  raw_manifest?: Record<string, unknown>
  // Phase 5: DocumentSummary for glass-box transparency
  document_summary?: DocumentSummary
  time_seconds?: number
  cost_cents?: number
}

export interface ExtractionPhase {
  status: string
  markdown_url?: string
  confidence_score?: number
  extraction_model?: string
  // Phase 5: Additional metrics
  pages_extracted?: number
  correction_iterations?: number
  time_seconds?: number
  cost_cents?: number
}

// Phase 5: Structure Verification Loop summary
export interface StructureLoopSummary {
  iterations: number
  lint_issues_found: number
  lint_issues_fixed: number
  ocr_suggestions_processed: number
  corrections_applied: number
  final_lint_clean: boolean
  time_seconds: number
  cost_cents: number
}

// Phase 5: Agent summary for Refine phase
export interface AgentSummary {
  agent_name: string
  observations_count: number
  auto_corrections_count: number
  review_items_count: number
  reasoning_summary: string
  confidence: number
  time_seconds: number
  cost_cents: number
}

// Phase 5: Refine phase (combines structure loop + agents)
export interface RefinePhase {
  status: string
  structure_loop?: StructureLoopSummary
  agents: AgentSummary[]
  total_observations: number
  total_auto_corrections: number
  total_review_items: number
}

export interface ObservationSummary {
  id: string
  agent: string
  severity: string
  confidence: number
  route: string
  status: string
  visual_description?: string
  markup_description?: string
  page_num?: number
}

export interface AgentsPhase {
  status: string
  agents_run: string[]
  observation_count: number
  observations: ObservationSummary[]
  raw_observations?: Record<string, unknown>[]
}

export interface ProposalSummary {
  id: string
  route: string
  status: string
  page_nums: number[]
  resolves_count: number
  search_preview: string
  replace_preview: string
  justification: string
  estimated_impact?: string
}

export interface ConsolidationPhase {
  status: string
  proposal_count: number
  auto_count: number
  manual_count: number
  proposals: ProposalSummary[]
  raw_proposals?: Record<string, unknown>[]
}

export interface ProcessingPhasesResponse {
  job_id: string
  filename: string
  status: string
  created_at: string
  updated_at: string

  // Phase 5 naming (preferred)
  analyze?: AnalysisPhase
  extract?: ExtractionPhase
  refine?: RefinePhase
  assemble?: RemediationPhase

  // Legacy naming (backward compatibility)
  analysis?: AnalysisPhase
  extraction?: ExtractionPhase
  agents?: AgentsPhase
  consolidation?: ConsolidationPhase
  remediation?: RemediationPhase

  total_llm_cost?: LLMCost
}

// Remediation Phase types (Phase 5 - auto corrections)
export interface AutoCorrectionSummary {
  id: string
  observation_id: string
  applied: boolean
  page_num: number | null
  search_preview: string
  replace_preview: string
  justification: string
  confidence: number
  agent: string
}

export interface RemediationPhase {
  status: string
  auto_correction_count: number
  applied_count: number
  pending_count: number
  auto_corrections: AutoCorrectionSummary[]
  raw_corrections?: Record<string, unknown>[]
}

// ============================================================================
// Phase 5: Review Checklist Types (PRD-027)
// ============================================================================

/** One option in a review item (multiple choice) */
export interface ReviewOption {
  id: string
  label: string
  action: 'replace' | 'keep' | 'skip' | 'other'
  replacement_text?: string | null
  is_recommended: boolean
}

/** Item needing human decision - multiple choice with free input */
export interface ReviewItem {
  id: string
  observation_id: string
  agent: string
  question: string
  options: ReviewOption[]
  search_text: string
  context: string
  page_num: number
  visual_context_url?: string | null
  agent_recommendation: string
  agent_confidence: number
  // Human's decision (filled after review)
  selected_option_id?: string | null
  custom_input?: string | null
  reviewed_by?: string | null
  reviewed_at?: string | null
}

/** Human review checklist - exposed via API */
export interface ReviewChecklist {
  items: ReviewItem[]
  summary: string
  by_category: Record<string, ReviewItem[]>
  by_agent: Record<string, ReviewItem[]>
  by_page: Record<number, ReviewItem[]>
  total_items: number
  critical_items: number
  completed_items: number
}

/** Lightweight summary for list views */
export interface ChecklistSummary {
  job_id: string
  total_items: number
  completed_items: number
  categories: string[]
  agents: string[]
  status: string
}

/** Request to submit a review for an item */
export interface ReviewSubmission {
  selected_option_id?: string | null
  custom_input?: string | null
  reviewed_by: string
}

/** Response after submitting a review */
export interface ReviewSubmissionResponse {
  status: string
  item_id: string
  remaining_items: number
}

/** Request to apply all reviews */
export interface ApplyReviewsRequest {
  force?: boolean
}

/** Response after applying reviews */
export interface ApplyReviewsResponse {
  status: string
  reviews_applied: number
  failed_replacements: number
  markdown_url?: string | null
  unreviewed_items: number
}

// ============================================================================
// Phase 5: Processing Result Types (PRD-021)
// ============================================================================

/** Summary of Phase 1: Analysis */
export interface ProcessingAnalysisSummary {
  document_type: string
  total_pages: number
  key_entities: string[]
  required_agents: string[]
  confidence: number
  time_seconds: number
  cost_cents: number
}

/** Summary of Phase 2: Extraction */
export interface ProcessingExtractionSummary {
  confidence: number
  pages_extracted: number
  correction_iterations: number
  time_seconds: number
  cost_cents: number
}

/** Summary of Phase 3a: Structure Verification Loop */
export interface ProcessingStructureSummary {
  iterations: number
  lint_issues_found: number
  lint_issues_fixed: number
  ocr_suggestions_processed: number
  corrections_applied: number
  final_lint_clean: boolean
  time_seconds: number
  cost_cents: number
}

/** Observation from an agent */
export interface Observation {
  id: string
  job_id: string
  agent: string
  source: 'agent' | 'human'
  visual_description: string
  markup_description: string
  location: {
    location_type: string
    value: string
    page_num: number
  }
  affected_pages: number[]
  confidence: number
  severity: 'critical' | 'major' | 'minor'
  category: string
  status: 'open' | 'closed'
  resolution?: 'fixed' | 'kept_original' | 'skipped' | null
  created_at: string
  human_comment?: string | null
}

/** Auto correction from an agent */
export interface AutoCorrection {
  id: string
  observation_id: string
  search: string
  replace: string
  justification: string
  confidence: number
  applied: boolean
  applied_at?: string | null
  agent: string
  page_num?: number | null
}

/** What one agent did - full transparency */
export interface AgentTrace {
  agent_name: string
  observations: Observation[]
  auto_corrections: AutoCorrection[]
  review_items: ReviewItem[]
  reasoning_summary: string
  confidence: number
  cost_cents: number
  time_seconds: number
  iterations: number
  started_at: string
  completed_at: string
}

/** Glass box: Everything that happened during processing */
export interface ProcessingTrace {
  analysis: ProcessingAnalysisSummary
  extraction: ProcessingExtractionSummary
  structure: ProcessingStructureSummary
  agents: AgentTrace[]
  total_observations: number
  auto_corrections_applied: number
  review_items_generated: number
  total_cost_cents: number
  total_time_seconds: number
  total_tokens: number
}

/** Complete result of document processing - exposed via API */
export interface ProcessingResult {
  job_id: string
  status: 'completed' | 'needs_review' | 'failed'
  markdown: string
  confidence: number
  processing_trace: ProcessingTrace
  review_checklist: ReviewChecklist
  created_at: string
  processing_time_seconds: number
}

// ============================================================================
// API Methods
// ============================================================================

export interface SubmitOptions {
  skipPiiScan?: boolean
  skipReason?: string
  generateDebugBundle?: boolean
}

export const api = {
  async submitDocument(file: File, options?: SubmitOptions): Promise<SubmitResponse> {
    const formData = new FormData()
    formData.append('file', file)

    if (options?.skipPiiScan) {
      formData.append('skip_pii_scan', 'true')
      if (options.skipReason) {
        formData.append('skip_reason', options.skipReason)
      }
    }

    if (options?.generateDebugBundle) {
      formData.append('generate_debug_bundle', 'true')
    }

    return request<SubmitResponse>('/api/documents/submit', {
      method: 'POST',
      body: formData,
    })
  },

  async getJobStatus(jobId: string): Promise<JobStatus> {
    return request<JobStatus>(`/api/documents/${jobId}`)
  },

  async getHealth(): Promise<HealthCheck> {
    return request<HealthCheck>('/health')
  },

  async getQueueMetrics(): Promise<QueueMetrics> {
    return request<QueueMetrics>('/api/dev/monitoring/queues')
  },

  async getReviewData(token: string): Promise<PIIReviewData> {
    return request<PIIReviewData>(`/api/approval/${token}/review`)
  },

  async submitApproval(token: string, decision: ApprovalDecision): Promise<ApprovalResponse> {
    return request<ApprovalResponse>(`/api/approval/${token}/decision`, {
      method: 'POST',
      body: JSON.stringify(decision),
    })
  },

  /** Alias for submitApproval for component compatibility */
  async submitApprovalDecision(token: string, decision: ApprovalDecision): Promise<ApprovalResponse> {
    return this.submitApproval(token, decision)
  },

  async getCorrectionReview(jobId: string, token: string): Promise<CorrectionReviewData> {
    return request<CorrectionReviewData>(`/api/corrections/${jobId}/review?token=${encodeURIComponent(token)}`)
  },

  async submitCorrectionDecision(jobId: string, decision: CorrectionDecision): Promise<CorrectionResponse> {
    return request<CorrectionResponse>(`/api/corrections/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify(decision),
    })
  },

  /** Fetch markdown content from a URL */
  async fetchMarkdown(url: string): Promise<string> {
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(`Failed to fetch markdown: ${response.statusText}`)
    }
    return response.text()
  },

  /** Get detailed processing phases for a job */
  async getJobPhases(jobId: string, showRaw = false): Promise<ProcessingPhasesResponse> {
    const params = showRaw ? '?show_raw=true' : ''
    return request<ProcessingPhasesResponse>(`/api/documents/${jobId}/phases${params}`)
  },

  // ============================================================================
  // Phase 5: Review Checklist API Methods
  // ============================================================================

  /** Get full processing result including trace and checklist */
  async getProcessingResult(jobId: string): Promise<ProcessingResult> {
    return request<ProcessingResult>(`/api/documents/${jobId}/result`)
  },

  /** Get review checklist with optional filters */
  async getChecklist(
    jobId: string,
    filters?: {
      category?: string
      agent?: string
      page?: number
      status?: 'pending' | 'completed'
    }
  ): Promise<ReviewChecklist> {
    const params = new URLSearchParams()
    if (filters?.category) params.set('category', filters.category)
    if (filters?.agent) params.set('agent', filters.agent)
    if (filters?.page) params.set('page', String(filters.page))
    if (filters?.status) params.set('status', filters.status)
    const query = params.toString() ? `?${params.toString()}` : ''
    return request<ReviewChecklist>(`/api/documents/${jobId}/checklist${query}`)
  },

  /** Get lightweight checklist summary */
  async getChecklistSummary(jobId: string): Promise<ChecklistSummary> {
    return request<ChecklistSummary>(`/api/documents/${jobId}/checklist/summary`)
  },

  /** Submit a review decision for a single item */
  async submitReview(
    jobId: string,
    itemId: string,
    submission: ReviewSubmission
  ): Promise<ReviewSubmissionResponse> {
    return request<ReviewSubmissionResponse>(
      `/api/documents/${jobId}/checklist/${itemId}/review`,
      {
        method: 'POST',
        body: JSON.stringify(submission),
      }
    )
  },

  /** Apply all completed reviews to generate final markdown */
  async applyReviews(jobId: string, force = false): Promise<ApplyReviewsResponse> {
    return request<ApplyReviewsResponse>(`/api/documents/${jobId}/apply-reviews`, {
      method: 'POST',
      body: JSON.stringify({ force }),
    })
  },
}
