import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from 'react-resizable-panels';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { MarkdownViewer } from '@/components/viewer/MarkdownViewer';
import { StageTabs } from '@/components/pipeline-viewer/StageTabs';
import { PIPELINE_STAGES, REVIEW_STAGE } from '@/types/pipeline-viewer';
// import { ChangesSidebar } from '@/components/pipeline-viewer/ChangesSidebar';
import { ChangesModal } from '@/components/pipeline-viewer/ChangesModal';
import { StructureMetadataModal } from '@/components/pipeline-viewer/StructureMetadataModal';
import { WarningsBanner } from '@/components/pipeline-viewer/WarningsBanner';
import { KeyboardShortcuts, type FocusRegion } from '@/components/pipeline-viewer/KeyboardShortcuts';
import { ClassificationError } from '@/components/pipeline-viewer/ClassificationError';
import { usePipelineViewer } from '@/hooks/usePipelineViewer';
import {
  Upload,
  Loader2,
  FileText,
  Image as ImageIcon,
  BarChart3,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  DollarSign,
  Clock,
  Play,
  Pause,
  Maximize2,
} from 'lucide-react';

const FLAG_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  academic: { bg: 'bg-indigo-50', text: 'text-indigo-700', label: 'Academic' },
  images: { bg: 'bg-emerald-50', text: 'text-emerald-700', label: 'Images' },
  tables: { bg: 'bg-sky-50', text: 'text-sky-700', label: 'Tables' },
  equations: { bg: 'bg-violet-50', text: 'text-violet-700', label: 'Equations' },
  scanned: { bg: 'bg-amber-50', text: 'text-amber-700', label: 'Scanned' },
};

const LAYOUT_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  single_column: { bg: 'bg-slate-100', text: 'text-slate-700', label: 'Single Column' },
  double_column: { bg: 'bg-blue-100', text: 'text-blue-800', label: 'Double Column' },
  presentation: { bg: 'bg-rose-100', text: 'text-rose-700', label: 'Presentation' },
};

type PageAttrs = {
  layout: string;
  is_academic: boolean;
  has_images: boolean;
  has_tables: boolean;
  has_equations: boolean;
  is_scanned: boolean;
};

function CollapsibleSection({
  title,
  count,
  defaultOpen = true,
  children,
}: {
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b last:border-b-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-gray-50 transition-colors"
      >
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          {title}{count != null ? ` (${count})` : ''}
        </h4>
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-muted-foreground" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
        )}
      </button>
      {open && <div className="px-4 pb-3">{children}</div>}
    </div>
  );
}

function StructureMetadataPanel({ metadata, onExpand }: { metadata: Record<string, unknown>; onExpand?: () => void }) {
  const pageAttributes = (metadata.page_attributes ?? {}) as Record<string, PageAttrs>;
  const outline = (metadata.outline ?? []) as Array<{ level: number; text: string; page: number }>;
  const footnotes = (metadata.footnotes ?? []) as Array<{
    number: string;
    body_text: string;
    source_page: number;
  }>;
  const codeBlocks = (metadata.code_blocks ?? []) as Array<{
    language: string;
    first_line: string;
    page: number;
    reasoning: string;
  }>;

  const pages = Object.entries(pageAttributes);
  const totalPages = pages.length;

  // Compute document-level summary from page attributes
  const layouts = new Map<string, number>();
  const flagCounts: Record<string, number> = { academic: 0, images: 0, tables: 0, equations: 0, scanned: 0 };
  for (const [, attrs] of pages) {
    layouts.set(attrs.layout, (layouts.get(attrs.layout) ?? 0) + 1);
    if (attrs.is_academic) flagCounts.academic++;
    if (attrs.has_images) flagCounts.images++;
    if (attrs.has_tables) flagCounts.tables++;
    if (attrs.has_equations) flagCounts.equations++;
    if (attrs.is_scanned) flagCounts.scanned++;
  }

  const hasAnyData = totalPages > 0 || outline.length > 0 || footnotes.length > 0 || codeBlocks.length > 0;

  return (
    <div className="w-72 flex-shrink-0 border-l bg-white flex flex-col overflow-y-auto">
      <div className="px-4 py-3 border-b flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-800">Structure Metadata</h3>
        {onExpand && (
          <button
            onClick={onExpand}
            className="p-1 rounded hover:bg-gray-100 transition-colors"
            title="Expand to full view"
          >
            <Maximize2 className="w-3.5 h-3.5 text-muted-foreground" />
          </button>
        )}
      </div>

      {/* Document summary */}
      {totalPages > 0 && (
        <div className="px-4 py-3 border-b bg-gray-50/50">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            Document Summary
          </h4>

          {/* Layout distribution */}
          <div className="flex flex-wrap gap-1.5 mb-2">
            {[...layouts.entries()].map(([layout, count]) => {
              const style = LAYOUT_STYLES[layout] ?? { bg: 'bg-gray-100', text: 'text-gray-700', label: layout };
              return (
                <span
                  key={layout}
                  className={cn('px-2 py-1 rounded-md text-[11px] font-medium', style.bg, style.text)}
                >
                  {style.label}
                  {count < totalPages && (
                    <span className="ml-1 opacity-60">{count}/{totalPages}</span>
                  )}
                </span>
              );
            })}
          </div>

          {/* Flag summary bar */}
          <div className="flex flex-wrap gap-1">
            {Object.entries(flagCounts)
              .filter(([, count]) => count > 0)
              .map(([flag, count]) => {
                const style = FLAG_STYLES[flag]!;
                return (
                  <span
                    key={flag}
                    className={cn('px-1.5 py-0.5 rounded text-[10px] font-medium', style.bg, style.text)}
                  >
                    {style.label} {count < totalPages ? `${count}p` : ''}
                  </span>
                );
              })}
          </div>
        </div>
      )}

      {/* Per-page attributes */}
      {totalPages > 0 && (
        <CollapsibleSection title="Page Attributes" count={totalPages} defaultOpen={totalPages <= 12}>
          <div className="space-y-1.5">
            {pages.map(([page, attrs]) => {
              const layoutStyle = LAYOUT_STYLES[attrs.layout] ?? { bg: 'bg-gray-100', text: 'text-gray-700', label: attrs.layout };
              const activeFlags = [
                attrs.is_academic && 'academic',
                attrs.has_images && 'images',
                attrs.has_tables && 'tables',
                attrs.has_equations && 'equations',
                attrs.is_scanned && 'scanned',
              ].filter(Boolean) as string[];

              return (
                <div key={page} className="flex items-start gap-1.5 text-xs">
                  <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 font-mono font-medium shrink-0 w-6 text-center">
                    {page}
                  </span>
                  <div className="flex flex-wrap gap-1 min-w-0">
                    <span
                      className={cn('px-1.5 py-0.5 rounded font-medium text-[10px]', layoutStyle.bg, layoutStyle.text)}
                    >
                      {layoutStyle.label.toLowerCase().replace(' ', '-')}
                    </span>
                    {activeFlags.map((flag) => {
                      const style = FLAG_STYLES[flag]!;
                      return (
                        <span
                          key={flag}
                          className={cn('px-1.5 py-0.5 rounded font-medium text-[10px]', style.bg, style.text)}
                        >
                          {style.label.toLowerCase()}
                        </span>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </CollapsibleSection>
      )}

      {/* Outline */}
      {outline.length > 0 && (
        <CollapsibleSection title="Outline" count={outline.length} defaultOpen={outline.length <= 25}>
          <div className="space-y-0.5">
            {outline.map((entry, idx) => (
              <div
                key={idx}
                className="text-xs text-muted-foreground"
                style={{ paddingLeft: `${(entry.level - 1) * 12}px` }}
              >
                <span className="text-gray-400 mr-1">{'#'.repeat(entry.level)}</span>
                <span>{entry.text}</span>
                <span className="text-gray-300 ml-1">p{entry.page}</span>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      )}

      {/* Code blocks */}
      {codeBlocks.length > 0 && (
        <CollapsibleSection title="Code Blocks" count={codeBlocks.length}>
          <div className="space-y-2">
            {codeBlocks.map((cb, idx) => (
              <div key={idx} className="text-xs">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-100 font-mono font-medium text-[10px]">
                    {cb.language}
                  </span>
                  <span className="text-gray-400">p{cb.page}</span>
                </div>
                <p className="text-muted-foreground line-clamp-1 pl-1 font-mono text-[10px]">{cb.first_line}</p>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      )}

      {/* Footnotes */}
      {footnotes.length > 0 && (
        <CollapsibleSection title="Footnotes" count={footnotes.length}>
          <div className="space-y-2">
            {footnotes.map((fn, idx) => (
              <div key={idx} className="text-xs">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 font-medium">
                    [{fn.number}]
                  </span>
                  <span className="text-gray-400">p{fn.source_page}</span>
                </div>
                <p className="text-muted-foreground line-clamp-3 pl-1">{fn.body_text}</p>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      )}

      {!hasAnyData && (
        <div className="flex-1 flex items-center justify-center p-4">
          <p className="text-xs text-muted-foreground text-center">
            No structural metadata found.
          </p>
        </div>
      )}
    </div>
  );
}

export function PipelineViewerPage() {
  const {
    result,
    uploading,
    error,
    processing,
    currentStepName,
    statusMessage,
    processFile,
    reset,
  } = usePipelineViewer();

  const [currentPage, setCurrentPage] = useState(1);
  const [activeStepIdx, setActiveStepIdx] = useState(0);
  const [copiedImage, setCopiedImage] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [autoAdvance, setAutoAdvance] = useState(true);
  const [changesModalOpen, setChangesModalOpen] = useState(false);
  const [metadataModalOpen, setMetadataModalOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const skipNavRef = useRef<HTMLElement>(null);
  const loadingStatusRef = useRef<HTMLDivElement>(null);

  const hasClassificationError = !!(
    result &&
    Object.keys(result.versions).length === 0 &&
    result.steps.some((s) => s.name === 'classification' && s.error)
  );

  // Coarse view state drives the skip-nav targets so screen reader users get
  // the right landing spot for whichever screen they're looking at.
  const viewState: 'idle' | 'uploading' | 'error' | 'result' = uploading
    ? 'uploading'
    : error || hasClassificationError
      ? 'error'
      : result
        ? 'result'
        : 'idle';

  // Single polite live region copy — drives the aria-live announcement.
  const liveAnnouncement = useMemo(() => {
    if (error) return `Error: ${error}`;
    if (uploading && !processing) return 'Uploading document. Extracting content.';
    if (processing && currentStepName) return `Processing step: ${currentStepName}.`;
    if (processing) return 'Document received. Processing pipeline started.';
    if (result && Object.keys(result.versions).length > 0) return 'Processing complete.';
    return '';
  }, [uploading, processing, currentStepName, error, result]);

  // Move focus to the loading status region the moment the upload starts so
  // a blind user knows the stream is live and has a live-region neighbour.
  useEffect(() => {
    if (uploading && loadingStatusRef.current) {
      loadingStatusRef.current.focus();
    }
  }, [uploading]);

  // When processing starts, move focus off the (now-unmounted) loading region
  // and onto the active stage tab — prefer the one the pipeline flagged as
  // currently processing, falling back to the first (Extraction) tab so the
  // user has a deterministic anchor before any step has streamed in.
  useEffect(() => {
    if (!processing) return;
    requestAnimationFrame(() => {
      const stagesRoot = document.getElementById('region-stages');
      if (!stagesRoot) return;
      const target =
        stagesRoot.querySelector<HTMLButtonElement>('button[aria-current="step"]') ??
        stagesRoot.querySelector<HTMLButtonElement>('button:not([disabled])') ??
        stagesRoot.querySelector<HTMLButtonElement>('button');
      target?.focus();
    });
  }, [processing]);

  // Auto-advance to newest step tab as steps stream in
  const stepsLength = result?.steps.length ?? 0;
  useEffect(() => {
    if (autoAdvance && stepsLength > 0) {
      setActiveStepIdx(stepsLength - 1);
    }
  }, [stepsLength, autoAdvance]);

  const totalPages = result?.total_pages ?? 0;
  const activeStep = result?.steps[activeStepIdx] ?? null;
  const stepVersion = activeStep?.version_after ?? 'v0';

  const activeVersion = stepVersion;

  // Whether this version has per-page markdowns (v0, v1) vs full-document only (v2, v3)
  const hasPerPageMarkdown = !!result?.page_markdowns[activeVersion];

  // Current page markdown for the active version — fall back to full document for v2/v3
  const pageMarkdown = hasPerPageMarkdown
    ? (result?.page_markdowns[activeVersion]?.[String(currentPage)] ?? '')
    : (result?.versions[activeVersion] ?? '');
  const pageImage = result?.page_images[String(currentPage)] ?? null;

  // Map figure paths to base64 data URIs for inline rendering
  const figureMap = useMemo(() => {
    if (!result?.figures.length) return {};
    const map: Record<string, string> = {};
    for (const fig of result.figures) {
      if (fig.image_base64) {
        map[`figures/${fig.ref_id}.png`] = `data:image/png;base64,${fig.image_base64}`;
      }
    }
    return map;
  }, [result?.figures]);

  // Aggregate all changes from the active stage (not just the active step)
  const stageChanges = useMemo(() => {
    if (!result || !activeStep) return [];
    const allStages = [...PIPELINE_STAGES, REVIEW_STAGE];
    const stage = allStages.find((s) => s.steps.includes(activeStep.name))
      ?? (PIPELINE_STAGES.every((s) => !s.steps.includes(activeStep.name)) ? REVIEW_STAGE : null);
    if (!stage) return activeStep.changes;
    return result.steps
      .filter((s) => stage.steps.includes(s.name))
      .flatMap((s) => s.changes);
  }, [result, activeStep]);

  /** Convert a base64 PNG string to a Blob. */
  const base64ToBlob = useCallback((b64: string, mime = 'image/png'): Blob => {
    const bytes = atob(b64);
    const buf = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i);
    return new Blob([buf], { type: mime });
  }, []);

  /** Copy current page image to clipboard. */
  const handleCopyImage = useCallback(async () => {
    if (!pageImage) return;
    try {
      const blob = base64ToBlob(pageImage);
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      setCopiedImage(true);
      setTimeout(() => setCopiedImage(false), 1500);
    } catch {
      navigator.clipboard.writeText(`[Page ${currentPage} image — clipboard not supported]`);
      setCopiedImage(true);
      setTimeout(() => setCopiedImage(false), 1500);
    }
  }, [pageImage, currentPage, base64ToBlob]);

  /** Trigger a browser download for a markdown string. */
  const downloadMarkdown = useCallback((markdown: string, filename: string) => {
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, []);

  /** Download the markdown version for a given step index. */
  const handleDownloadVersion = useCallback(
    (stepIndex: number) => {
      if (!result) return;
      const step = result.steps[stepIndex];
      if (!step?.version_after) return;
      const version = step.version_after;
      const markdown = result.versions[version];
      if (!markdown) return;
      const baseName = result.filename.replace(/\.pdf$/i, '');
      downloadMarkdown(markdown, `${baseName}-${version}.md`);
    },
    [result, downloadMarkdown],
  );

  /** Download the currently active version's markdown. */
  const handleDownloadCurrentMarkdown = useCallback(() => {
    if (!result) return;
    const markdown = result.versions[activeVersion];
    if (!markdown) return;
    const baseName = result.filename.replace(/\.pdf$/i, '');
    downloadMarkdown(markdown, `${baseName}-${activeVersion}.md`);
  }, [result, activeVersion, downloadMarkdown]);

  const handleProcess = useCallback(
    async (file: File) => {
      setCurrentPage(1);
      setActiveStepIdx(0);
      await processFile(file, {
        imagesScale: 2.0,
        doTableStructure: true,
      });
    },
    [processFile],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file && file.name.toLowerCase().endsWith('.pdf')) {
        handleProcess(file);
      }
    },
    [handleProcess],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleProcess(file);
    },
    [handleProcess],
  );

  // Focus a landmark region by ID with a visible focus ring
  const handleFocusRegion = useCallback((region: FocusRegion) => {
    if (region === 'skip') {
      const firstLink = skipNavRef.current?.querySelector<HTMLAnchorElement>('a');
      firstLink?.focus();
      return;
    }
    const idMap = { pages: 'region-pages', stages: 'region-stages', preview: 'region-preview', changes: 'region-changes' };
    const el = document.getElementById(idMap[region]);
    if (el) {
      el.focus();
      el.scrollIntoView({ block: 'nearest' });
      // Apply a visible focus ring via inline style (Tailwind focus: doesn't reliably work on programmatic focus)
      el.style.outline = '2px solid #1e3a5f';
      el.style.outlineOffset = '-2px';
      const cleanup = () => {
        el.style.outline = '';
        el.style.outlineOffset = '';
        el.removeEventListener('blur', cleanup);
      };
      el.addEventListener('blur', cleanup);
    }
  }, []);

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Skip navigation menu — targets change with view state */}
      <nav
        ref={skipNavRef}
        aria-label="Skip navigation"
        className="sr-only focus-within:not-sr-only focus-within:absolute focus-within:z-[60] focus-within:top-0 focus-within:left-0 focus-within:bg-white focus-within:border focus-within:rounded-md focus-within:shadow-lg focus-within:p-3"
      >
        <ul className="flex flex-col gap-1">
          {viewState === 'idle' && (
            <li><a href="#region-upload" className="text-sm text-uic-blue underline focus:outline-2 focus:outline-uic-blue px-2 py-1 block rounded hover:bg-uic-blue/5">Skip to PDF upload</a></li>
          )}
          {viewState === 'uploading' && (
            <li><a href="#region-status" className="text-sm text-uic-blue underline focus:outline-2 focus:outline-uic-blue px-2 py-1 block rounded hover:bg-uic-blue/5">Skip to processing status</a></li>
          )}
          {viewState === 'error' && (
            <li><a href="#region-error" className="text-sm text-uic-blue underline focus:outline-2 focus:outline-uic-blue px-2 py-1 block rounded hover:bg-uic-blue/5">Skip to error message</a></li>
          )}
          {viewState === 'result' && (
            <>
              <li><a href="#region-preview" className="text-sm text-uic-blue underline focus:outline-2 focus:outline-uic-blue px-2 py-1 block rounded hover:bg-uic-blue/5">Skip to Rendered Preview</a></li>
              <li><a href="#region-stages" className="text-sm text-uic-blue underline focus:outline-2 focus:outline-uic-blue px-2 py-1 block rounded hover:bg-uic-blue/5">Skip to Stage Picker</a></li>
              <li><a href="#region-pages" className="text-sm text-uic-blue underline focus:outline-2 focus:outline-uic-blue px-2 py-1 block rounded hover:bg-uic-blue/5">Skip to Page Picker</a></li>
              <li><a href="#region-changes" className="text-sm text-uic-blue underline focus:outline-2 focus:outline-uic-blue px-2 py-1 block rounded hover:bg-uic-blue/5">Skip to Changes Panel</a></li>
            </>
          )}
        </ul>
      </nav>

      {/* Polite live region — announces upload / pipeline state transitions
          to assistive technology without moving focus. */}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {liveAnnouncement}
      </div>

      <KeyboardShortcuts onFocusRegion={handleFocusRegion} />

      {/* Header */}
      <header className="flex items-center px-6 py-3 bg-white border-b shadow-sm">
        <h1 className="text-lg font-bold text-uic-blue flex items-center gap-2">
          {import.meta.env.VITE_SHOW_UIC_LOGO === 'true' && (
            <img
              src="/uic-logo.png"
              alt="University of Illinois Chicago"
              className="h-7 w-7"
            />
          )}
          Equalify Reflow
          <span
            className="text-[10px] font-semibold uppercase tracking-wider bg-uic-blue/10 text-uic-blue border border-uic-blue/20 px-2 py-0.5 rounded-full"
          >
            Beta
          </span>
        </h1>
      </header>

      {/* Classification error — document was rejected before processing */}
      {result && Object.keys(result.versions).length === 0 && (() => {
        const classStep = result.steps.find((s) => s.name === 'classification' && s.error);
        return classStep ? (
          <div id="region-error" tabIndex={-1} className="outline-none">
            <ClassificationError step={classStep} onReset={reset} />
          </div>
        ) : null;
      })()}

      {/* Pipeline layout — always visible */}
      {!(result && Object.keys(result.versions).length === 0 && result.steps.some((s) => s.name === 'classification' && s.error)) && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Step tabs */}
          <nav id="region-stages" tabIndex={-1} aria-label="Pipeline stages" className="outline-none rounded-sm">
          <StageTabs
            steps={result?.steps ?? []}
            activeStepIdx={activeStepIdx}
            onSelectStep={setActiveStepIdx}
            processingStepName={processing ? currentStepName : null}
            onDownloadVersion={handleDownloadVersion}
            onOpenChangesModal={() => setChangesModalOpen(true)}
          />
          </nav>

          {/* Warnings banner */}
          {result?.warnings && result.warnings.length > 0 && (
            <WarningsBanner warnings={result.warnings} />
          )}

          {/* Pre-result states: upload, loading, error */}
          {!result && (
            <div className="flex-1 flex items-center justify-center p-8">
              {uploading ? (
                <div
                  id="region-status"
                  ref={loadingStatusRef}
                  tabIndex={-1}
                  className="flex flex-col items-center gap-4 outline-none focus:outline-2 focus:outline-uic-blue focus:outline-offset-4 rounded-md"
                >
                  <Loader2 className="w-10 h-10 animate-spin text-uic-blue" aria-hidden="true" />
                  <p className="text-muted-foreground">Extracting document content...</p>
                  {statusMessage ? (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 max-w-md text-center">
                      <p className="text-sm text-amber-900">{statusMessage}</p>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">Processing time depends on document length and complexity</p>
                  )}
                </div>
              ) : error ? (
                <div id="region-error" tabIndex={-1} className="max-w-md text-center outline-none">
                  <p className="text-red-700 font-medium mb-2">Processing Error</p>
                  <p className="text-sm text-muted-foreground mb-4">{error}</p>
                  <Button variant="outline" onClick={reset}>
                    Try Again
                  </Button>
                </div>
              ) : (
                <button
                  id="region-upload"
                  type="button"
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  className={cn(
                    'w-full max-w-lg border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer bg-transparent focus:outline-2 focus:outline-uic-blue focus:outline-offset-2',
                    dragOver ? 'border-uic-blue bg-uic-blue/5' : 'border-gray-400 hover:border-uic-blue',
                  )}
                  onClick={() => fileInputRef.current?.click()}
                  aria-label="Upload a PDF: press Enter or Space to open a file picker, or drop a file here"
                >
                  <Upload className="w-12 h-12 mx-auto mb-4 text-gray-500" aria-hidden="true" />
                  <p className="text-lg font-medium text-gray-800 mb-1">
                    Drop a PDF here or click to upload
                  </p>
                  <p className="text-sm text-muted-foreground">
                    See every processing step in the pipeline above
                  </p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf"
                    className="hidden"
                    onChange={handleFileSelect}
                  />
                </button>
              )}
            </div>
          )}

          {/* Stats bar — only when we have results */}
          {result && Object.keys(result.versions).length > 0 && (
          <div className="flex items-center gap-6 px-6 py-2 bg-white border-b text-sm">
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <FileText className="w-3.5 h-3.5" />
                {result.total_pages} pages
              </span>
              <span className="flex items-center gap-1">
                <BarChart3 className="w-3.5 h-3.5" />
                {result.stats.chars_per_page as number} chars/page
              </span>
              {(result.stats.is_likely_scanned as boolean) && (
                <span className="text-amber-600 font-medium">Likely scanned</span>
              )}
              {result.figures.length > 0 && (
                <span className="flex items-center gap-1">
                  <ImageIcon className="w-3.5 h-3.5" />
                  {result.figures.length} figures
                </span>
              )}
              {(() => {
                const totalMs = result.steps.reduce((s, st) => s + (st.elapsed_ms || 0), 0);
                const totalSec = totalMs / 1000;
                const timeStr = totalSec >= 60
                  ? `${Math.floor(totalSec / 60)}m ${Math.round(totalSec % 60)}s`
                  : `${totalSec.toFixed(1)}s`;
                return totalMs > 0 ? (
                  <span className="flex items-center gap-1">
                    <Clock className="w-3.5 h-3.5" />
                    {timeStr}
                  </span>
                ) : null;
              })()}
              {(() => {
                const totalCost = result.steps.reduce((s, st) => s + (st.cost_cents || 0), 0);
                const totalTokens = result.steps.reduce((s, st) => s + (st.input_tokens || 0) + (st.output_tokens || 0), 0);
                if (totalTokens === 0) return null;
                return (
                  <span className="flex items-center gap-1">
                    <DollarSign className="w-3.5 h-3.5" />
                    {(totalCost / 100).toFixed(4)} · {totalTokens.toLocaleString()} tokens
                  </span>
                );
              })()}
            </div>

            <div className="flex-1" />

            {/* Auto-advance toggle */}
            <Button
              variant="outline"
              size="sm"
              className={cn(
                'h-7 text-xs gap-1.5',
                autoAdvance
                  ? 'text-uic-blue border-uic-blue/30 hover:bg-uic-blue/5'
                  : 'text-muted-foreground',
              )}
              onClick={() => setAutoAdvance(!autoAdvance)}
              title={autoAdvance ? 'Auto-advance is on — click to pause' : 'Auto-advance is off — click to resume'}
            >
              {autoAdvance ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              Auto-advance
            </Button>

            {/* New upload */}
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={reset}
            >
              <Upload className="w-3.5 h-3.5 mr-1" />
              New PDF
            </Button>
          </div>
          )}

          {/* Main content area — only when we have results */}
          {result && Object.keys(result.versions).length > 0 && (
          <div className="flex-1 flex min-h-0 overflow-hidden">
            {/* Page sidebar */}
            {totalPages > 1 && (
              <nav id="region-pages" tabIndex={-1} aria-label="Page navigation" className="w-16 border-r bg-white overflow-y-auto flex-shrink-0 outline-none rounded-sm">
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                  <button
                    key={p}
                    onClick={() => setCurrentPage(p)}
                    aria-label={`Page ${p}`}
                    aria-current={p === currentPage ? 'page' : undefined}
                    className={cn(
                      'w-full py-2 text-xs font-medium border-b transition-colors',
                      p === currentPage
                        ? 'bg-uic-blue/10 text-uic-blue border-l-2 border-l-uic-blue'
                        : 'text-muted-foreground hover:bg-gray-50',
                    )}
                  >
                    {p}
                  </button>
                ))}
              </nav>
            )}

            {/* Split view */}
            <PanelGroup orientation="horizontal" className="flex-1 min-w-0 overflow-hidden">
              <Panel defaultSize={45} minSize={20}>
                <div className="h-full flex flex-col">
                  {pageImage && (
                    <div className="flex items-center justify-between px-4 py-2 border-b bg-gray-50">
                      <span className="text-sm font-medium text-muted-foreground">
                        Page {currentPage} Image
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={handleCopyImage}
                        title="Copy page image"
                        className={cn(
                          'gap-1.5',
                          copiedImage
                            ? 'text-green-600 hover:text-green-700 hover:bg-green-50'
                            : 'text-muted-foreground hover:text-foreground',
                        )}
                      >
                        {copiedImage ? (
                          <Check className="w-4 h-4" />
                        ) : (
                          <Copy className="w-4 h-4" />
                        )}
                        <span className="text-xs">{copiedImage ? 'Copied' : 'Copy Image'}</span>
                      </Button>
                    </div>
                  )}
                  <div className="flex-1 overflow-auto bg-gray-100 flex items-start justify-center p-4">
                    {pageImage ? (
                      <img
                        src={`data:image/png;base64,${pageImage}`}
                        alt={`Page ${currentPage}`}
                        className="max-w-full shadow-lg rounded"
                      />
                    ) : processing || uploading ? (
                      <div className="flex flex-col items-center gap-2 mt-20 text-muted-foreground">
                        <Loader2 className="w-6 h-6 animate-spin" />
                        <span className="text-sm">Loading page image...</span>
                      </div>
                    ) : (
                      <div className="text-muted-foreground text-sm mt-20">
                        No image available
                      </div>
                    )}
                  </div>
                </div>
              </Panel>

              <PanelResizeHandle className="w-1.5 bg-gray-200 hover:bg-uic-blue/30 transition-colors cursor-col-resize" />

              <Panel defaultSize={55} minSize={20}>
                <main id="region-preview" tabIndex={-1} aria-label="Rendered document preview" className="h-full outline-none rounded-sm">
                <MarkdownViewer
                  content={pageMarkdown}
                  figureMap={figureMap}
                  isComplete={true}
                  onDownloadMarkdown={handleDownloadCurrentMarkdown}
                  onCopy={() => {
                    navigator.clipboard.writeText(pageMarkdown);
                  }}
                />
                </main>
              </Panel>
            </PanelGroup>

            {/* Right sidebar */}
            <aside id="region-changes" tabIndex={-1} aria-label="Changes and metadata panel" className="flex-shrink-0 outline-none rounded-sm">
            {activeStep?.name === 'structure' && activeStep.metadata ? (
              <StructureMetadataPanel metadata={activeStep.metadata} onExpand={() => setMetadataModalOpen(true)} />
            ) : (
              <div className="w-64 flex-shrink-0 border-l bg-white flex flex-col">
                <div className="px-4 py-3 border-b">
                  <h3 className="text-sm font-medium text-muted-foreground">Changes</h3>
                </div>
                {stageChanges.length === 0 ? (
                  <div className="flex-1 flex items-center justify-center p-4">
                    <p className="text-xs text-muted-foreground text-center">
                      No changes in this stage.
                      <br />
                      Docling produces v0 from scratch.
                    </p>
                  </div>
                ) : (
                  <div className="flex-1 flex flex-col items-center justify-center p-4 gap-3">
                    <span className="text-2xl font-bold text-amber-700">{stageChanges.length}</span>
                    <p className="text-xs text-muted-foreground text-center">
                      change{stageChanges.length !== 1 ? 's' : ''} in this stage
                    </p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-xs gap-1.5"
                      onClick={() => setChangesModalOpen(true)}
                    >
                      <Maximize2 className="w-3.5 h-3.5" />
                      View Details
                    </Button>
                  </div>
                )}
              </div>
            )}
            </aside>
          </div>
          )}

        </div>
      )}

      {/* Modals */}
      {changesModalOpen && (
        <ChangesModal
          changes={stageChanges}
          totalPages={totalPages}
          onClose={() => setChangesModalOpen(false)}
        />
      )}
      {metadataModalOpen && activeStep?.metadata && (
        <StructureMetadataModal
          metadata={activeStep.metadata}
          onClose={() => setMetadataModalOpen(false)}
        />
      )}
    </div>
  );
}
