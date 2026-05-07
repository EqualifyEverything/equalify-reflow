import { useState, useCallback, useRef } from 'react';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from 'react-resizable-panels';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { MarkdownViewer } from '@/components/viewer/MarkdownViewer';
import {
  Upload,
  Loader2,
  FileText,
  Image as ImageIcon,
  Clock,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  BarChart3,
  Copy,
  Check,
} from 'lucide-react';
import { apiFetch } from '@/auth/apiFetch';

interface PageData {
  page_number: number;
  markdown: string;
  image_base64: string | null;
}

interface FigureData {
  ref_id: string;
  caption: string;
  page_number: number;
  image_base64: string;
}

interface StepData {
  name: string;
  description: string;
  elapsed_ms: number;
}

interface PipelineResponse {
  filename: string;
  total_pages: number;
  pages: PageData[];
  figures: FigureData[];
  full_markdown: string;
  steps_run: StepData[];
  stats: Record<string, unknown>;
}

export function MinimalPage() {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PipelineResponse | null>(null);
  const [currentPageIdx, setCurrentPageIdx] = useState(0);
  const [showFullMarkdown, setShowFullMarkdown] = useState(false);
  const [showFigures, setShowFigures] = useState(false);
  const [imagesScale, setImagesScale] = useState(2.0);
  const [doTableStructure, setDoTableStructure] = useState(true);
  const [copiedAll, setCopiedAll] = useState(false);
  const [copiedImage, setCopiedImage] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const currentPage = result?.pages[currentPageIdx] ?? null;
  const totalPages = result?.total_pages ?? 0;

  /** Convert a base64 PNG string to a Blob. */
  const base64ToBlob = useCallback((b64: string, mime = 'image/png'): Blob => {
    const bytes = atob(b64);
    const buf = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i);
    return new Blob([buf], { type: mime });
  }, []);

  /** Copy a single page image to clipboard as PNG. */
  const handleCopyImage = useCallback(async () => {
    if (!currentPage?.image_base64) return;
    try {
      const blob = base64ToBlob(currentPage.image_base64);
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      setCopiedImage(true);
      setTimeout(() => setCopiedImage(false), 1500);
    } catch {
      // Fallback: some browsers restrict ClipboardItem — fall back to text
      navigator.clipboard.writeText(`[Page ${currentPage.page_number} image — clipboard image not supported in this browser]`);
      setCopiedImage(true);
      setTimeout(() => setCopiedImage(false), 1500);
    }
  }, [currentPage, base64ToBlob]);

  /**
   * Copy All: writes both text/html (images + markdown per page) and
   * text/plain (full markdown) so the paste target gets the best format.
   */
  const handleCopyAll = useCallback(async () => {
    if (!result) return;
    try {
      // Build HTML with inline images + per-page markdown
      const htmlParts = result.pages.map((p) => {
        const imgTag = p.image_base64
          ? `<img src="data:image/png;base64,${p.image_base64}" alt="Page ${p.page_number}" style="max-width:100%;margin-bottom:8px;" />`
          : '';
        // Escape HTML in markdown for the <pre> block
        const escapedMd = p.markdown
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
        return `<h3>Page ${p.page_number}</h3>${imgTag}<pre style="white-space:pre-wrap;font-size:13px;">${escapedMd}</pre>`;
      });
      const html = `<div>${htmlParts.join('<hr/>')}</div>`;

      const htmlBlob = new Blob([html], { type: 'text/html' });
      const textBlob = new Blob([result.full_markdown], { type: 'text/plain' });
      await navigator.clipboard.write([
        new ClipboardItem({ 'text/html': htmlBlob, 'text/plain': textBlob }),
      ]);
    } catch {
      // Fallback: plain text only
      await navigator.clipboard.writeText(result.full_markdown);
    }
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 1500);
  }, [result]);

  const handleProcess = useCallback(
    async (file: File) => {
      setUploading(true);
      setError(null);
      setResult(null);
      setCurrentPageIdx(0);

      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('images_scale', String(imagesScale));
        formData.append('do_table_structure', String(doTableStructure));

        const response = await apiFetch('/api/dev/minimal/process', {
          method: 'POST',
          body: formData,
        });

        if (!response.ok) {
          const detail = await response.text();
          throw new Error(`Processing failed (${response.status}): ${detail}`);
        }

        const data: PipelineResponse = await response.json();
        setResult(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setUploading(false);
      }
    },
    [imagesScale, doTableStructure]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file && file.name.toLowerCase().endsWith('.pdf')) {
        handleProcess(file);
      } else {
        setError('Only PDF files are supported');
      }
    },
    [handleProcess]
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleProcess(file);
    },
    [handleProcess]
  );

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b shadow-sm">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-uic-blue">Minimal Pipeline</h1>
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
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {copiedAll ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {copiedAll ? 'Copied' : 'Copy All'}
            </Button>
          )}
        </div>

        {/* Pipeline options */}
        <div className="flex items-center gap-4 text-sm">
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

      {/* Upload area (when no result) */}
      {!result && !uploading && (
        <div className="flex-1 flex items-center justify-center p-8">
          <div
            ref={dropRef}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
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
              Runs raw Docling extraction — no agents, no corrections
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

      {/* Loading state */}
      {uploading && (
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <Loader2 className="w-10 h-10 animate-spin text-uic-blue" />
          <p className="text-muted-foreground">Extracting document content...</p>
          <p className="text-xs text-muted-foreground">Processing time depends on document length and complexity</p>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="max-w-md text-center">
            <p className="text-red-600 font-medium mb-2">Processing Error</p>
            <p className="text-sm text-muted-foreground mb-4">{error}</p>
            <Button variant="outline" onClick={() => { setError(null); setResult(null); }}>
              Try Again
            </Button>
          </div>
        </div>
      )}

      {/* Results */}
      {result && !uploading && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Steps + Stats bar */}
          <div className="flex items-center gap-6 px-6 py-2 bg-white border-b text-sm">
            {/* Steps */}
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-muted-foreground" />
              {result.steps_run.map((step) => (
                <span
                  key={step.name}
                  className="inline-flex items-center gap-1 bg-green-50 text-green-700 px-2 py-0.5 rounded text-xs font-medium"
                  title={step.description}
                >
                  {step.name}
                  <span className="text-green-500">{(step.elapsed_ms / 1000).toFixed(1)}s</span>
                </span>
              ))}
            </div>

            <div className="w-px h-4 bg-gray-200" />

            {/* Stats */}
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
            </div>

            <div className="flex-1" />

            {/* Page nav */}
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPageIdx === 0}
                onClick={() => setCurrentPageIdx((i) => Math.max(0, i - 1))}
                className="h-7 w-7 p-0"
              >
                <ChevronLeft className="w-4 h-4" />
              </Button>
              <span className="text-xs font-medium min-w-[60px] text-center">
                Page {currentPageIdx + 1} / {totalPages}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPageIdx >= totalPages - 1}
                onClick={() => setCurrentPageIdx((i) => Math.min(totalPages - 1, i + 1))}
                className="h-7 w-7 p-0"
              >
                <ChevronRight className="w-4 h-4" />
              </Button>
            </div>

            {/* New upload button */}
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={() => { setResult(null); setError(null); }}
            >
              <Upload className="w-3.5 h-3.5 mr-1" />
              New PDF
            </Button>
          </div>

          {/* Page sidebar + split view */}
          <div className="flex-1 flex min-h-0">
            {/* Page sidebar */}
            {totalPages > 1 && (
              <div className="w-16 border-r bg-white overflow-y-auto flex-shrink-0">
                {result.pages.map((page, idx) => (
                  <button
                    key={page.page_number}
                    onClick={() => setCurrentPageIdx(idx)}
                    className={cn(
                      'w-full py-2 text-xs font-medium border-b transition-colors',
                      idx === currentPageIdx
                        ? 'bg-uic-blue/10 text-uic-blue border-l-2 border-l-uic-blue'
                        : 'text-muted-foreground hover:bg-gray-50',
                    )}
                  >
                    {page.page_number}
                  </button>
                ))}
              </div>
            )}

            {/* Split view: image | markdown */}
            <PanelGroup orientation="horizontal" className="flex-1">
              {/* Left: page image */}
              <Panel defaultSize={45} minSize={20}>
                <div className="h-full flex flex-col">
                  {/* Image toolbar */}
                  {currentPage?.image_base64 && (
                    <div className="flex items-center justify-between px-4 py-2 border-b bg-gray-50">
                      <span className="text-sm font-medium text-muted-foreground">
                        Page {currentPage.page_number} Image
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
                            : 'text-muted-foreground hover:text-foreground'
                        )}
                      >
                        {copiedImage ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                        <span className="text-xs">{copiedImage ? 'Copied' : 'Copy Image'}</span>
                      </Button>
                    </div>
                  )}
                  <div className="flex-1 overflow-auto bg-gray-100 flex items-start justify-center p-4">
                    {currentPage?.image_base64 ? (
                      <img
                        src={`data:image/png;base64,${currentPage.image_base64}`}
                        alt={`Page ${currentPage.page_number}`}
                        className="max-w-full shadow-lg rounded"
                      />
                    ) : (
                      <div className="text-muted-foreground text-sm mt-20">No image available</div>
                    )}
                  </div>
                </div>
              </Panel>

              <PanelResizeHandle className="w-1.5 bg-gray-200 hover:bg-uic-blue/30 transition-colors cursor-col-resize" />

              {/* Right: page markdown */}
              <Panel defaultSize={55} minSize={20}>
                <MarkdownViewer
                  content={currentPage?.markdown ?? ''}
                  isComplete={true}
                  onCopy={() => {
                    if (currentPage?.markdown) {
                      navigator.clipboard.writeText(currentPage.markdown);
                    }
                  }}
                />
              </Panel>
            </PanelGroup>
          </div>

          {/* Collapsible sections at bottom */}
          <div className="border-t bg-white">
            {/* Full document markdown */}
            <button
              onClick={() => setShowFullMarkdown(!showFullMarkdown)}
              className="w-full flex items-center justify-between px-6 py-2 hover:bg-gray-50 transition-colors text-sm font-medium text-muted-foreground"
            >
              <span>Full Document Markdown</span>
              {showFullMarkdown ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
            {showFullMarkdown && (
              <div className="max-h-80 overflow-auto border-t">
                <MarkdownViewer content={result.full_markdown} isComplete={true} />
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
                  {showFigures ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
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
                        <p className="px-2 pb-2 text-xs text-gray-400">Page {fig.page_number}</p>
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
