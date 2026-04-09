import { useState, useCallback, useRef } from 'react';
import type { PipelineViewerResult, StepResult } from '@/types/pipeline-viewer';

const API_URL = import.meta.env.VITE_API_URL ?? '';

const MAX_RECONNECT_ATTEMPTS = 10;
const RECONNECT_BASE_DELAY_MS = 2000;

interface ProcessOptions {
  imagesScale: number;
  doTableStructure: boolean;
}

interface SSEEvent {
  type: string;
  data: unknown;
  id?: number;
}

/**
 * Parse an SSE buffer into events + remainder.
 * Splits on double-newline boundaries and extracts id: / event: / data: lines.
 */
function parseSSEBuffer(buffer: string): { events: SSEEvent[]; remainder: string } {
  const events: SSEEvent[] = [];
  const blocks = buffer.split('\n\n');

  // Last element may be incomplete — keep as remainder
  const remainder = blocks.pop() ?? '';

  for (const block of blocks) {
    if (!block.trim()) continue;

    let eventType = 'message';
    let eventId: number | undefined;
    let dataLines: string[] = [];

    for (const line of block.split('\n')) {
      if (line.startsWith('id: ')) {
        const parsed = parseInt(line.slice(4).trim(), 10);
        if (!isNaN(parsed)) eventId = parsed;
      } else if (line.startsWith('event: ')) {
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
        events.push({ type: eventType, data, id: eventId });
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
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  // Reconnect state — refs to survive across renders without re-triggering effects
  const sessionIdRef = useRef<string | null>(null);
  const lastEventIdRef = useRef<number>(-1);

  /**
   * Shared event handler used by both the initial stream and reconnect streams.
   * Returns true if the stream should be considered complete (done event received).
   */
  function handleSSEEvent(event: SSEEvent): boolean {
    // Track event ID for reconnect
    if (event.id !== undefined) {
      lastEventIdRef.current = event.id;
    }

    switch (event.type) {
      case 'session': {
        const { session_id } = event.data as { session_id: string };
        sessionIdRef.current = session_id;
        break;
      }

      case 'init': {
        const initData = event.data as PipelineViewerResult;
        setResult(initData);
        setUploading(false);
        setProcessing(true);
        setStatusMessage(null);
        break;
      }

      case 'page_image': {
        const { page, image_base64 } = event.data as { page: string; image_base64: string };
        setResult((prev) =>
          prev ? { ...prev, page_images: { ...prev.page_images, [page]: image_base64 } } : prev
        );
        break;
      }

      case 'figure_image': {
        const { ref_id, image_base64 } = event.data as { ref_id: string; image_base64: string };
        setResult((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            figures: prev.figures.map((f) => (f.ref_id === ref_id ? { ...f, image_base64 } : f)),
          };
        });
        break;
      }

      case 'status': {
        const { message } = event.data as { message: string; type: string };
        setStatusMessage(message);
        break;
      }

      case 'processing': {
        const { display_name } = event.data as { step_name: string; display_name: string };
        setCurrentStepName(display_name);
        setStatusMessage(null);
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
        return true;
      }
    }
    return false;
  }

  /**
   * Read an SSE stream from a fetch Response and process events with the shared handler.
   * Returns true if the stream completed normally (done event), false if interrupted.
   */
  async function consumeSSEStream(response: Response, signal: AbortSignal): Promise<boolean> {
    const reader = response.body?.getReader();
    if (!reader) throw new Error('No response body');

    const decoder = new TextDecoder();
    let sseBuffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (signal.aborted) break;

        sseBuffer += decoder.decode(value, { stream: true });
        const { events, remainder } = parseSSEBuffer(sseBuffer);
        sseBuffer = remainder;

        for (const event of events) {
          if (handleSSEEvent(event)) return true;
        }
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        console.warn('SSE stream interrupted:', err instanceof Error ? err.message : err);
      }
    }
    return false;
  }

  /**
   * Attempt to reconnect to an in-progress session.
   */
  async function reconnect(signal: AbortSignal, attempt: number = 0): Promise<void> {
    const sid = sessionIdRef.current;
    const lastId = lastEventIdRef.current;
    if (!sid || signal.aborted) return;

    if (attempt >= MAX_RECONNECT_ATTEMPTS) {
      console.warn(`SSE reconnect: gave up after ${MAX_RECONNECT_ATTEMPTS} attempts`);
      return;
    }

    // Exponential backoff with jitter, capped at 30s
    const baseDelay = Math.min(RECONNECT_BASE_DELAY_MS * Math.pow(2, attempt), 30000);
    const delay = baseDelay + Math.random() * 1000;
    console.log(`SSE reconnect: attempt ${attempt + 1}/${MAX_RECONNECT_ATTEMPTS} in ${Math.round(delay)}ms (last_event_id=${lastId})`);
    await new Promise((r) => setTimeout(r, delay));
    if (signal.aborted) return;

    try {
      const url = `${API_URL}/api/v1/pipeline/sessions/${sid}/stream?last_event_id=${lastId}`;
      const response = await fetch(url, { signal });

      if (!response.ok) {
        console.warn(`SSE reconnect: server returned ${response.status}`);
        return await reconnect(signal, attempt + 1);
      }

      const completed = await consumeSSEStream(response, signal);
      if (!completed && !signal.aborted) {
        // Stream broke again — retry
        return await reconnect(signal, attempt + 1);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      return await reconnect(signal, attempt + 1);
    }
  }

  const processFile = useCallback(async (file: File, options: ProcessOptions) => {
    // Abort any in-flight stream
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    // Reset reconnect refs for new upload
    sessionIdRef.current = null;
    lastEventIdRef.current = -1;

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

      const completed = await consumeSSEStream(response, controller.signal);

      // If stream didn't complete and we have a session, try to reconnect
      if (!completed && sessionIdRef.current && !controller.signal.aborted) {
        console.log('SSE stream interrupted, attempting reconnect...');
        await reconnect(controller.signal);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // User cancelled — not an error
        return;
      }

      // Try reconnect if we have a session
      if (sessionIdRef.current && !controller.signal.aborted) {
        console.log('SSE stream error, attempting reconnect...');
        try {
          await reconnect(controller.signal);
          return;
        } catch {
          // Reconnect also failed — fall through to error handling
        }
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
    sessionIdRef.current = null;
    lastEventIdRef.current = -1;
    setResult(null);
    setError(null);
    setProcessing(false);
    setCurrentStepName(null);
    setStatusMessage(null);
    setSessionId(null);
  }, []);

  return { result, uploading, error, processing, currentStepName, statusMessage, processFile, reset, sessionId, updateVersion };
}
