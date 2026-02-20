import { useState, useCallback, useRef } from 'react';
import type { PipelineViewerResult, StepResult } from '@/types/pipeline-viewer';

const API_URL = import.meta.env.VITE_API_URL ?? '';

interface ProcessOptions {
  imagesScale: number;
  doTableStructure: boolean;
}

interface SSEEvent {
  type: string;
  data: unknown;
}

/**
 * Parse an SSE buffer into events + remainder.
 * Splits on double-newline boundaries and extracts event: / data: lines.
 */
function parseSSEBuffer(buffer: string): { events: SSEEvent[]; remainder: string } {
  const events: SSEEvent[] = [];
  const blocks = buffer.split('\n\n');

  // Last element may be incomplete — keep as remainder
  const remainder = blocks.pop() ?? '';

  for (const block of blocks) {
    if (!block.trim()) continue;

    let eventType = 'message';
    let dataLines: string[] = [];

    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        dataLines.push(line.slice(6));
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5));
      }
    }

    if (dataLines.length > 0) {
      try {
        const data = JSON.parse(dataLines.join('\n'));
        events.push({ type: eventType, data });
      } catch {
        // Skip malformed JSON
      }
    }
  }

  return { events, remainder };
}

export function usePipelineViewer() {
  const [result, setResult] = useState<PipelineViewerResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [currentStepName, setCurrentStepName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const processFile = useCallback(async (file: File, options: ProcessOptions) => {
    // Abort any in-flight stream
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setUploading(true);
    setProcessing(false);
    setCurrentStepName(null);
    setError(null);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('images_scale', String(options.imagesScale));
      formData.append('do_table_structure', String(options.doTableStructure));

      const response = await fetch(`${API_URL}/api/v1/pipeline/process/stream`, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`Processing failed (${response.status}): ${detail}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }

      const decoder = new TextDecoder();
      let sseBuffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        sseBuffer += decoder.decode(value, { stream: true });
        const { events, remainder } = parseSSEBuffer(sseBuffer);
        sseBuffer = remainder;

        for (const event of events) {
          switch (event.type) {
            case 'init': {
              const initData = event.data as PipelineViewerResult;
              setResult(initData);
              setUploading(false);
              setProcessing(true);
              break;
            }

            case 'processing': {
              const { display_name } = event.data as { step_name: string; display_name: string };
              setCurrentStepName(display_name);
              break;
            }

            case 'step': {
              const stepData = event.data as {
                step: StepResult;
                new_versions: Record<string, string>;
                new_page_markdowns: Record<string, Record<string, string>>;
              };
              setResult((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  steps: [...prev.steps, stepData.step],
                  versions: { ...prev.versions, ...stepData.new_versions },
                  page_markdowns: { ...prev.page_markdowns, ...stepData.new_page_markdowns },
                };
              });
              break;
            }

            case 'error': {
              const { step_name, message } = event.data as { step_name: string; message: string };
              console.error(`Pipeline step "${step_name}" failed: ${message}`);
              break;
            }

            case 'done': {
              const doneData = event.data as { session_id?: string };
              if (doneData.session_id) {
                setSessionId(doneData.session_id);
              }
              setProcessing(false);
              setCurrentStepName(null);
              break;
            }
          }
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // User cancelled — not an error
        return;
      }
      // Only set error if we have no results yet.
      // Late-stage disconnects are non-fatal when steps have already been received.
      setResult((prev) => {
        if (!prev) {
          setError(err instanceof Error ? err.message : 'Unknown error');
        }
        return prev;
      });
    } finally {
      setUploading(false);
      setProcessing(false);
      setCurrentStepName(null);
    }
  }, []);

  const updateVersion = useCallback(
    (key: string, markdown: string, pageMarkdowns?: Record<string, string>) => {
      setResult((prev) => {
        if (!prev) return prev;
        const updated = {
          ...prev,
          versions: { ...prev.versions, [key]: markdown },
        };
        if (pageMarkdowns) {
          updated.page_markdowns = { ...prev.page_markdowns, [key]: pageMarkdowns };
        }
        return updated;
      });
    },
    [],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setResult(null);
    setError(null);
    setProcessing(false);
    setCurrentStepName(null);
    setSessionId(null);
  }, []);

  return { result, uploading, error, processing, currentStepName, processFile, reset, sessionId, updateVersion };
}
