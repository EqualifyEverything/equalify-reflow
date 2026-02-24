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
