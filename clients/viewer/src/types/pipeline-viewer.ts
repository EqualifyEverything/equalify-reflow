export interface DocumentChange {
  page: number;
  old_text: string;
  new_text: string;
  reasoning: string;
  stage: string;
}

export interface FigureData {
  ref_id: string;
  caption: string;
  page_number: number;
  image_base64: string;
}

export type StepStatus = 'success' | 'skipped' | 'error';

export interface StepResult {
  name: string;
  display_name: string;
  version_before: string | null;
  version_after: string;
  elapsed_ms: number;
  changes: DocumentChange[];
  metadata: Record<string, unknown>;
  skipped: boolean;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_cents: number;
}

export interface StageDefinition {
  name: string;
  label: string;
  steps: string[];
}

/**
 * The 5-stage pipeline, mapping internal step names to user-facing stages.
 * Steps not listed here (e.g. revision_*, feedback_*) go into a dynamic "Review" stage.
 */
export const PIPELINE_STAGES: StageDefinition[] = [
  { name: 'pii',         label: 'PII Review',    steps: ['pii_scan'] },
  { name: 'extraction',  label: 'Extraction',   steps: ['docling', 'docling_ocr'] },
  { name: 'analysis',    label: 'Analysis',      steps: ['classification', 'structure'] },
  { name: 'headings',    label: 'Headings',      steps: ['heading_levels', 'heading_reconciliation'] },
  { name: 'translation', label: 'Translation',   steps: ['page_content', 'code_blocks'] },
  { name: 'assembly',    label: 'Assembly',       steps: ['boundaries', 'cleanup'] },
];

export interface PIIFinding {
  entity_type: string;
  start: number;
  end: number;
  score: number;
  text: string;
}

/** Steps that don't belong to a known stage get grouped here. */
export const REVIEW_STAGE: StageDefinition = {
  name: 'review',
  label: 'Review',
  steps: [],
};

export interface PipelineViewerResult {
  filename: string;
  total_pages: number;
  versions: Record<string, string>;
  page_images: Record<string, string>;
  page_markdowns: Record<string, Record<string, string>>;
  figures: FigureData[];
  steps: StepResult[];
  stats: Record<string, unknown>;
  warnings: string[];
}
