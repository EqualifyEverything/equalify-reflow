import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from 'react-resizable-panels';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { MarkdownViewer } from '@/components/viewer/MarkdownViewer';
import { StepTabs } from '@/components/pipeline-viewer/StepTabs';
import { ChangesSidebar } from '@/components/pipeline-viewer/ChangesSidebar';
import { WarningsBanner } from '@/components/pipeline-viewer/WarningsBanner';
import { ClassificationError } from '@/components/pipeline-viewer/ClassificationError';
import { FeedbackPanel } from '@/components/feedback/FeedbackPanel';
import { ReviewPanel } from '@/components/feedback/ReviewPanel';
import { FeedbackStatusBar } from '@/components/feedback/FeedbackStatusBar';
import { usePipelineViewer } from '@/hooks/usePipelineViewer';
import { useFeedbackSession } from '@/hooks/useFeedbackSession';
import type { FeedbackItemType, FeedbackCategory, TextSelector } from '@/types/feedback';
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
  MessageSquare,
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

function StructureMetadataPanel({ metadata }: { metadata: Record<string, unknown> }) {
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
      <div className="px-4 py-3 border-b">
        <h3 className="text-sm font-semibold text-gray-800">Structure Metadata</h3>
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
    processFile,
    reset,
    sessionId,
    updateVersion,
  } = usePipelineViewer();

  const feedback = useFeedbackSession(sessionId, {
    onVersionUpdate: (key, markdown) => {
      updateVersion(key, markdown);
    },
  });

  const [currentPage, setCurrentPage] = useState(1);
  const [activeStepIdx, setActiveStepIdx] = useState(0);
  const [copiedImage, setCopiedImage] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Auto-advance to newest step tab as steps stream in
  const stepsLength = result?.steps.length ?? 0;
  useEffect(() => {
    if (stepsLength > 0) {
      setActiveStepIdx(stepsLength - 1);
    }
  }, [stepsLength]);

  const totalPages = result?.total_pages ?? 0;
  const activeStep = result?.steps[activeStepIdx] ?? null;
  const stepVersion = activeStep?.version_after ?? 'v0';

  // When feedback has produced a new version, show it instead of the step version
  const activeVersion = feedback.feedbackVersion ?? stepVersion;

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
      map[`figures/${fig.ref_id}.png`] = `data:image/png;base64,${fig.image_base64}`;
    }
    return map;
  }, [result?.figures]);

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

  // Feedback: create a feedback item from the MarkdownViewer selection
  const handleFeedbackCreate = useCallback(
    (data: {
      type: FeedbackItemType;
      selector: TextSelector;
      newText: string | null;
      description: string;
      category: FeedbackCategory | null;
      page: number;
    }) => {
      feedback.addFeedbackItem({
        id: crypto.randomUUID(),
        type: data.type,
        selector: data.selector,
        section: null,
        page: data.page,
        new_text: data.newText,
        description: data.description,
        feedback_type: data.category,
      });
    },
    [feedback],
  );

  // Determine if feedback mode is active
  const isFeedbackActive = feedback.phase != null;
  const isFeedbackCollecting = feedback.phase === 'collecting' || feedback.phase === 'submitting';
  const isFeedbackReviewing = feedback.phase === 'reviewing' || feedback.phase === 'applying';
  const canShowFeedbackButton =
    sessionId != null && !processing && !isFeedbackActive && result != null;

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="flex items-center px-6 py-3 bg-white border-b shadow-sm">
        <h1 className="text-lg font-bold text-uic-blue">Pipeline Viewer</h1>
        <span className="ml-3 text-xs font-medium text-muted-foreground bg-gray-100 px-2 py-0.5 rounded">
          Dev Tool
        </span>
      </header>

      {/* Upload area */}
      {!result && !uploading && !error && (
        <div className="flex-1 flex items-center justify-center p-8">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            className={cn(
              'w-full max-w-lg border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer',
              dragOver ? 'border-uic-blue bg-uic-blue/5' : 'border-gray-300 hover:border-gray-400',
            )}
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload className="w-12 h-12 mx-auto mb-4 text-gray-400" />
            <p className="text-lg font-medium text-gray-700 mb-1">
              Drop a PDF here or click to upload
            </p>
            <p className="text-sm text-muted-foreground">
              Versioned pipeline viewer — see every processing step
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf"
              className="hidden"
              onChange={handleFileSelect}
            />
          </div>
        </div>
      )}

      {/* Loading state — shown while waiting for Docling */}
      {uploading && (
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <Loader2 className="w-10 h-10 animate-spin text-uic-blue" />
          <p className="text-muted-foreground">Running Docling extraction...</p>
          <p className="text-xs text-muted-foreground">This typically takes 10-30 seconds</p>
        </div>
      )}

      {/* Error state — only show full overlay when no results have been received */}
      {error && !result && (
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="max-w-md text-center">
            <p className="text-red-600 font-medium mb-2">Processing Error</p>
            <p className="text-sm text-muted-foreground mb-4">{error}</p>
            <Button variant="outline" onClick={reset}>
              Try Again
            </Button>
          </div>
        </div>
      )}

      {/* Classification error — document was rejected before processing */}
      {result && Object.keys(result.versions).length === 0 && (() => {
        const classStep = result.steps.find((s) => s.name === 'classification' && s.error);
        return classStep ? (
          <ClassificationError step={classStep} onReset={reset} />
        ) : null;
      })()}

      {/* Results — shown as soon as init event arrives (while processing continues) */}
      {result && Object.keys(result.versions).length > 0 && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Step tabs */}
          <StepTabs
            steps={result.steps}
            activeIndex={activeStepIdx}
            onSelect={setActiveStepIdx}
            processingStepName={processing ? currentStepName : null}
          />

          {/* Warnings banner */}
          {result.warnings?.length > 0 && (
            <WarningsBanner warnings={result.warnings} />
          )}

          {/* Stats bar */}
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
              {/* Feedback status */}
              {feedback.phase && (
                <FeedbackStatusBar
                  phase={feedback.phase}
                  revisionRound={feedback.revisionRound}
                  itemCount={feedback.pendingItems.length}
                />
              )}
            </div>

            <div className="flex-1" />

            {/* Feedback button */}
            {canShowFeedbackButton && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs gap-1.5 text-purple-700 border-purple-200 hover:bg-purple-50"
                onClick={feedback.enterFeedbackMode}
              >
                <MessageSquare className="w-3.5 h-3.5" />
                Give Feedback
              </Button>
            )}
            {feedback.phase === 'finalized' && (
              <span className="text-[10px] font-medium px-2 py-1 rounded bg-green-50 text-green-700">
                Session Finalized
              </span>
            )}

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

          {/* Feedback error */}
          {feedback.error && (
            <div className="px-6 py-1.5 bg-red-50 border-b text-xs text-red-700">
              {feedback.error}
            </div>
          )}

          {/* Main content area */}
          <div className="flex-1 flex min-h-0 overflow-hidden">
            {/* Page sidebar */}
            {totalPages > 1 && (
              <div className="w-16 border-r bg-white overflow-y-auto flex-shrink-0">
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                  <button
                    key={p}
                    onClick={() => setCurrentPage(p)}
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
              </div>
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
                <MarkdownViewer
                  content={pageMarkdown}
                  figureMap={figureMap}
                  isComplete={true}
                  feedbackEnabled={isFeedbackCollecting}
                  currentPage={currentPage}
                  onFeedbackCreate={handleFeedbackCreate}
                  onCopy={() => {
                    navigator.clipboard.writeText(pageMarkdown);
                  }}
                />
              </Panel>
            </PanelGroup>

            {/* Right sidebar: feedback panels override the default sidebar */}
            {isFeedbackCollecting ? (
              <FeedbackPanel
                items={feedback.pendingItems}
                phase={feedback.phase!}
                revisionRound={feedback.revisionRound}
                onRemoveItem={feedback.removeFeedbackItem}
                onSubmit={feedback.submitFeedback}
                onExit={feedback.exitFeedbackMode}
              />
            ) : isFeedbackReviewing ? (
              <ReviewPanel
                candidates={feedback.candidates}
                reviews={feedback.reviews}
                isApplying={feedback.phase === 'applying'}
                onSetDecision={feedback.setReviewDecision}
                onApplyChanges={() => feedback.submitReviews('request_changes')}
                onApproveFinalize={feedback.approveSession}
                onBackToCollecting={feedback.backToCollecting}
              />
            ) : activeStep?.name === 'structure' && activeStep.metadata ? (
              <StructureMetadataPanel metadata={activeStep.metadata} />
            ) : (
              <ChangesSidebar
                changes={activeStep?.changes ?? []}
                totalPages={totalPages}
              />
            )}
          </div>

        </div>
      )}
    </div>
  );
}
