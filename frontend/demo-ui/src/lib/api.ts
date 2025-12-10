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

  // For awaiting_correction_approval status
  correction_summary?: CorrectionSummary
  review_url?: string
  original_markdown_url?: string
  corrected_markdown_url?: string
  page_image_urls?: string[]
  llm_cost?: LLMCost

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
}

export interface CorrectionURLs {
  original_markdown: string
  corrected_markdown: string
  page_images: string[]
}

export interface CorrectionReviewData {
  job_id: string
  total_corrections: number
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

// ============================================================================
// API Methods
// ============================================================================

export const api = {
  async submitDocument(file: File): Promise<SubmitResponse> {
    const formData = new FormData()
    formData.append('file', file)
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
}
