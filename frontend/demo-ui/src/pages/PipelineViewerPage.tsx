import { useState, useCallback, useRef, useEffect } from 'react';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from 'react-resizable-panels';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { MarkdownViewer } from '@/components/viewer/MarkdownViewer';
import { StepTabs } from '@/components/pipeline-viewer/StepTabs';
import { ChangesSidebar } from '@/components/pipeline-viewer/ChangesSidebar';
import { VersionCompare } from '@/components/pipeline-viewer/VersionCompare';
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
} from 'lucide-react';

function StructureMetadataPanel({ metadata }: { metadata: Record<string, unknown> }) {
  const pageTypes = (metadata.page_types ?? {}) as Record<string, string>;
  const outline = (metadata.outline ?? []) as Array<{ level: number; text: string; page: number }>;
  const footnotes = (metadata.footnotes ?? []) as Array<{
    number: string;
    body_text: string;
    source_page: number;
  }>;

  return (
    <div className="w-64 border-l bg-white flex flex-col overflow-y-auto">
      <div className="px-4 py-3 border-b">
        <h3 className="text-sm font-medium text-muted-foreground">Structure Metadata</h3>
      </div>

      {/* Page types */}
      {Object.keys(pageTypes).length > 0 && (
        <div className="px-4 py-3 border-b">
          <h4 className="text-xs font-semibold text-muted-foreground mb-2 uppercase tracking-wide">
            Page Types
          </h4>
          <div className="space-y-1">
            {Object.entries(pageTypes).map(([page, type]) => (
              <div key={page} className="flex items-center gap-2 text-xs">
                <span className="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 font-medium">
                  p{page}
                </span>
                <span className="text-muted-foreground">{type.replace(/_/g, ' ')}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Outline */}
      {outline.length > 0 && (
        <div className="px-4 py-3 border-b">
          <h4 className="text-xs font-semibold text-muted-foreground mb-2 uppercase tracking-wide">
            Outline ({outline.length} headings)
          </h4>
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
        </div>
      )}

      {/* Footnotes */}
      {footnotes.length > 0 && (
        <div className="px-4 py-3">
          <h4 className="text-xs font-semibold text-muted-foreground mb-2 uppercase tracking-wide">
            Footnotes ({footnotes.length})
          </h4>
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
        </div>
      )}

      {Object.keys(pageTypes).length === 0 && outline.length === 0 && footnotes.length === 0 && (
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
  const { result, uploading, error, processing, currentStepName, processFile, reset } = usePipelineViewer();

  const [currentPage, setCurrentPage] = useState(1);
  const [activeStepIdx, setActiveStepIdx] = useState(0);
  const [showFullMarkdown, setShowFullMarkdown] = useState(false);
  const [showFigures, setShowFigures] = useState(false);
  const [imagesScale, setImagesScale] = useState(2.0);
  const [doTableStructure, setDoTableStructure] = useState(true);
  const [copiedAll, setCopiedAll] = useState(false);
  const [copiedImage, setCopiedImage] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Compare mode state
  const [compareMode, setCompareMode] = useState(false);
  const [versionA, setVersionA] = useState('v0');
  const [versionB, setVersionB] = useState('v0');

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
  const activeVersion = activeStep?.version_after ?? 'v0';

  // Whether this version has per-page markdowns (v0, v1) vs full-document only (v2, v3)
  const hasPerPageMarkdown = !!result?.page_markdowns[activeVersion];

  // Current page markdown for the active version — fall back to full document for v2/v3
  const pageMarkdown = hasPerPageMarkdown
    ? (result?.page_markdowns[activeVersion]?.[String(currentPage)] ?? '')
    : (result?.versions[activeVersion] ?? '');
  const pageImage = result?.page_images[String(currentPage)] ?? null;

  // Full document markdown for active version
  const fullMarkdown = result?.versions[activeVersion] ?? '';

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

  /** Copy all: rich HTML + plain text. */
  const handleCopyAll = useCallback(async () => {
    if (!result) return;
    try {
      const pages = Object.keys(result.page_markdowns[activeVersion] ?? {})
        .sort((a, b) => Number(a) - Number(b));
      const htmlParts = pages.map((p) => {
        const md = result.page_markdowns[activeVersion]?.[p] ?? '';
        const img = result.page_images[p];
        const imgTag = img
          ? `<img src="data:image/png;base64,${img}" alt="Page ${p}" style="max-width:100%;margin-bottom:8px;" />`
          : '';
        const escapedMd = md
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
        return `<h3>Page ${p}</h3>${imgTag}<pre style="white-space:pre-wrap;font-size:13px;">${escapedMd}</pre>`;
      });
      const html = `<div>${htmlParts.join('<hr/>')}</div>`;
      const htmlBlob = new Blob([html], { type: 'text/html' });
      const textBlob = new Blob([fullMarkdown], { type: 'text/plain' });
      await navigator.clipboard.write([
        new ClipboardItem({ 'text/html': htmlBlob, 'text/plain': textBlob }),
      ]);
    } catch {
      await navigator.clipboard.writeText(fullMarkdown);
    }
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 1500);
  }, [result, activeVersion, fullMarkdown]);

  const handleProcess = useCallback(
    async (file: File) => {
      setCurrentPage(1);
      setActiveStepIdx(0);
      setCompareMode(false);
      await processFile(file, {
        imagesScale,
        doTableStructure,
      });
    },
    [processFile, imagesScale, doTableStructure],
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

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b shadow-sm">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-uic-blue">Pipeline Viewer</h1>
          <span className="text-xs font-medium text-muted-foreground bg-gray-100 px-2 py-0.5 rounded">
            Dev Tool
          </span>
          {result && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopyAll}
              title="Copy full document markdown"
              className={cn(
                'gap-1.5 h-7 text-xs',
                copiedAll
                  ? 'text-green-600 hover:text-green-700 hover:bg-green-50'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {copiedAll ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {copiedAll ? 'Copied' : 'Copy All'}
            </Button>
          )}
        </div>

        <div className="flex items-center gap-4 text-sm">
          {/* Compare toggle */}
          {result && (
            <VersionCompare
              steps={result.steps}
              compareMode={compareMode}
              onToggleCompare={() => setCompareMode(!compareMode)}
              versionA={versionA}
              versionB={versionB}
              onChangeA={setVersionA}
              onChangeB={setVersionB}
            />
          )}

          {/* Pipeline options */}
          <label className="flex items-center gap-1.5">
            <span className="text-muted-foreground">Scale:</span>
            <Input
              type="number"
              min={1.0}
              max={3.0}
              step={0.5}
              value={imagesScale}
              onChange={(e) => setImagesScale(parseFloat(e.target.value) || 2.0)}
              className="w-16 h-7 text-xs"
            />
          </label>
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={doTableStructure}
              onChange={(e) => setDoTableStructure(e.target.checked)}
              className="rounded border-gray-300"
            />
            <span className="text-muted-foreground">Tables</span>
          </label>
        </div>
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

      {/* Error state */}
      {error && (
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

      {/* Results — shown as soon as init event arrives (while processing continues) */}
      {result && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Step tabs */}
          <StepTabs
            steps={result.steps}
            activeIndex={activeStepIdx}
            onSelect={setActiveStepIdx}
            processingStepName={processing ? currentStepName : null}
          />

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
            </div>

            <div className="flex-1" />

            {/* Page nav */}
            <div className="flex items-center gap-1">
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                <button
                  key={p}
                  onClick={() => setCurrentPage(p)}
                  className={cn(
                    'w-7 h-7 text-xs font-medium rounded transition-colors',
                    p === currentPage
                      ? 'bg-uic-blue text-white'
                      : 'text-muted-foreground hover:bg-gray-100',
                  )}
                >
                  {p}
                </button>
              ))}
            </div>

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

          {/* Main content area */}
          <div className="flex-1 flex min-h-0">
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
            <PanelGroup orientation="horizontal" className="flex-1">
              {compareMode ? (
                <>
                  {/* Compare mode: version A | version B */}
                  <Panel defaultSize={50} minSize={20}>
                    <MarkdownViewer
                      content={
                        result.page_markdowns[versionA]?.[String(currentPage)]
                        ?? result.versions[versionA]
                        ?? ''
                      }
                      isComplete={true}
                      onCopy={() => {
                        const md = result.page_markdowns[versionA]?.[String(currentPage)]
                          ?? result.versions[versionA] ?? '';
                        navigator.clipboard.writeText(md);
                      }}
                    />
                  </Panel>
                  <PanelResizeHandle className="w-1.5 bg-gray-200 hover:bg-uic-blue/30 transition-colors cursor-col-resize" />
                  <Panel defaultSize={50} minSize={20}>
                    <MarkdownViewer
                      content={
                        result.page_markdowns[versionB]?.[String(currentPage)]
                        ?? result.versions[versionB]
                        ?? ''
                      }
                      isComplete={true}
                      onCopy={() => {
                        const md = result.page_markdowns[versionB]?.[String(currentPage)]
                          ?? result.versions[versionB] ?? '';
                        navigator.clipboard.writeText(md);
                      }}
                    />
                  </Panel>
                </>
              ) : (
                <>
                  {/* Normal mode: image | markdown */}
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
                      isComplete={true}
                      onCopy={() => {
                        navigator.clipboard.writeText(pageMarkdown);
                      }}
                    />
                  </Panel>
                </>
              )}
            </PanelGroup>

            {/* Changes sidebar / metadata panel */}
            {activeStep?.name === 'structure' && activeStep.metadata ? (
              <StructureMetadataPanel metadata={activeStep.metadata} />
            ) : (
              <ChangesSidebar
                changes={activeStep?.changes ?? []}
                totalPages={totalPages}
              />
            )}
          </div>

          {/* Collapsible sections */}
          <div className="border-t bg-white">
            {/* Full document markdown */}
            <button
              onClick={() => setShowFullMarkdown(!showFullMarkdown)}
              className="w-full flex items-center justify-between px-6 py-2 hover:bg-gray-50 transition-colors text-sm font-medium text-muted-foreground"
            >
              <span>Full Document Markdown ({activeVersion})</span>
              {showFullMarkdown ? (
                <ChevronUp className="w-4 h-4" />
              ) : (
                <ChevronDown className="w-4 h-4" />
              )}
            </button>
            {showFullMarkdown && (
              <div className="max-h-80 overflow-auto border-t">
                <MarkdownViewer content={fullMarkdown} isComplete={true} />
              </div>
            )}

            {/* Figures */}
            {result.figures.length > 0 && (
              <>
                <button
                  onClick={() => setShowFigures(!showFigures)}
                  className="w-full flex items-center justify-between px-6 py-2 hover:bg-gray-50 transition-colors text-sm font-medium text-muted-foreground border-t"
                >
                  <span>Extracted Figures ({result.figures.length})</span>
                  {showFigures ? (
                    <ChevronUp className="w-4 h-4" />
                  ) : (
                    <ChevronDown className="w-4 h-4" />
                  )}
                </button>
                {showFigures && (
                  <div className="max-h-96 overflow-auto border-t p-4 grid grid-cols-2 md:grid-cols-3 gap-4">
                    {result.figures.map((fig, idx) => (
                      <div key={idx} className="border rounded-lg overflow-hidden bg-gray-50">
                        <img
                          src={`data:image/png;base64,${fig.image_base64}`}
                          alt={fig.caption || `Figure from page ${fig.page_number}`}
                          className="w-full"
                        />
                        {fig.caption && (
                          <p className="p-2 text-xs text-muted-foreground">{fig.caption}</p>
                        )}
                        <p className="px-2 pb-2 text-xs text-gray-400">
                          Page {fig.page_number}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
