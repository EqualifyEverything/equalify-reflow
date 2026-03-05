import { useState, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Eye, Code2, Download, Bug, Copy, Check } from 'lucide-react';
import { useTextSelection } from '@/hooks/useTextSelection';
import { SelectionPopover } from '@/components/feedback/SelectionPopover';
import { InlineFeedbackForm } from '@/components/feedback/InlineFeedbackForm';
import type { FeedbackItemType, FeedbackCategory, TextSelector } from '@/types/feedback';

/**
 * Resolve rendered text back to the raw markdown.
 *
 * The user selects text from the rendered HTML (which strips markdown syntax).
 * The backend needs to find the text in raw markdown.  We search the raw
 * content for the best match:
 *   1. Exact substring (works for plain text without formatting)
 *   2. Normalized whitespace match (collapses runs of whitespace)
 *   3. Line-by-line fuzzy match for shorter selections
 *
 * Returns a TextSelector with `exact` from the raw markdown (not the rendered text),
 * plus surrounding context for disambiguation.
 */
function resolveToRawMarkdown(
  renderedText: string,
  rawContent: string,
  renderedPrefix: string,
  renderedSuffix: string,
): TextSelector {
  // 1. Try exact match (works when there's no formatting syntax in selection)
  const exactIdx = rawContent.indexOf(renderedText);
  if (exactIdx !== -1) {
    return {
      exact: renderedText,
      prefix: rawContent.slice(Math.max(0, exactIdx - 20), exactIdx) || null,
      suffix: rawContent.slice(exactIdx + renderedText.length, exactIdx + renderedText.length + 20) || null,
    };
  }

  // 2. Normalized whitespace match: collapse whitespace in both
  const normalize = (s: string) => s.replace(/\s+/g, ' ').trim();
  const normalizedRendered = normalize(renderedText);
  const normalizedRaw = normalize(rawContent);
  const normIdx = normalizedRaw.indexOf(normalizedRendered);
  if (normIdx !== -1) {
    // Map back to the original raw string by walking character positions
    const rawMatch = findNormalizedSpan(rawContent, normalizedRendered);
    if (rawMatch) {
      const [start, end] = rawMatch;
      const exact = rawContent.slice(start, end);
      return {
        exact,
        prefix: rawContent.slice(Math.max(0, start - 20), start) || null,
        suffix: rawContent.slice(end, end + 20) || null,
      };
    }
  }

  // 3. Fallback: return rendered text with rendered context.
  // The backend has a fuzzy fallback (edit distance on lines) that may catch this.
  return {
    exact: renderedText,
    prefix: renderedPrefix || null,
    suffix: renderedSuffix || null,
  };
}

/**
 * Find the span in the original string that, when whitespace-normalized,
 * matches the given normalized target.
 */
function findNormalizedSpan(raw: string, normalizedTarget: string): [number, number] | null {
  // Walk through the raw string, building a normalized version char by char,
  // tracking the mapping from normalized position to raw position.
  let normPos = 0;
  let inWhitespace = false;
  let matchStartRaw = -1;

  for (let i = 0; i < raw.length && normPos <= normalizedTarget.length; i++) {
    const ch = raw[i];
    const isWs = /\s/.test(ch);

    let normCh: string | null = null;
    if (isWs) {
      if (!inWhitespace) {
        normCh = ' ';
        inWhitespace = true;
      }
    } else {
      normCh = ch;
      inWhitespace = false;
    }

    if (normCh !== null) {
      if (normCh === normalizedTarget[normPos]) {
        if (normPos === 0) {
          matchStartRaw = i;
        }
        normPos++;
        if (normPos === normalizedTarget.length) {
          // Found the match - end is after the current raw character
          return [matchStartRaw, i + 1];
        }
      } else {
        // Restart match
        if (matchStartRaw !== -1) {
          // Restart from the character after where the match started
          i = matchStartRaw;
        }
        normPos = 0;
        matchStartRaw = -1;
        inWhitespace = false;
      }
    }
  }

  return null;
}

interface FeedbackCreateData {
  type: FeedbackItemType;
  selector: TextSelector;
  newText: string | null;
  description: string;
  category: FeedbackCategory | null;
  page: number;
}

interface MarkdownViewerProps {
  content: string;
  className?: string;
  isComplete?: boolean;
  showDebugDownload?: boolean;
  figureMap?: Record<string, string>;
  onDownloadMarkdown?: () => void;
  onDownloadDebug?: () => void;
  onCopy?: () => void;
  feedbackEnabled?: boolean;
  currentPage?: number;
  onFeedbackCreate?: (data: FeedbackCreateData) => void;
}

export function MarkdownViewer({
  content,
  className,
  isComplete = false,
  showDebugDownload = false,
  figureMap,
  onDownloadMarkdown,
  onDownloadDebug,
  onCopy,
  feedbackEnabled = false,
  currentPage = 1,
  onFeedbackCreate,
}: MarkdownViewerProps) {
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const [activeForm, setActiveForm] = useState<{
    type: FeedbackItemType;
    text: string;
    selector: TextSelector;
    rect: { top: number; left: number; width: number; height: number; bottom: number };
  } | null>(null);

  const contentRef = useRef<HTMLDivElement>(null);
  const { selection, clearSelection } = useTextSelection(
    feedbackEnabled ? contentRef : { current: null },
  );

  const handleCopy = () => {
    if (onCopy) {
      onCopy();
    } else {
      navigator.clipboard.writeText(content);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handlePopoverAction = useCallback(
    (type: FeedbackItemType) => {
      if (!selection) return;

      // Resolve the rendered text selection back to the raw markdown
      const selector = resolveToRawMarkdown(
        selection.text,
        content,
        selection.prefix,
        selection.suffix,
      );

      setActiveForm({
        type,
        text: selection.text,
        selector,
        rect: selection.rect,
      });
      clearSelection();
    },
    [selection, clearSelection, content],
  );

  const handleFormSubmit = useCallback(
    (data: FeedbackCreateData) => {
      onFeedbackCreate?.(data);
      setActiveForm(null);
    },
    [onFeedbackCreate],
  );

  const handleFormCancel = useCallback(() => {
    setActiveForm(null);
  }, []);

  const handlePopoverDismiss = useCallback(() => {
    clearSelection();
  }, [clearSelection]);

  return (
    <div className={cn('flex flex-col h-full', className)}>
      {/* Toggle Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-gray-50">
        <span className="text-sm font-medium text-muted-foreground">
          {showRaw ? 'Raw Markdown' : 'Rendered Preview'}
          {feedbackEnabled && (
            <span className="ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">
              Select text to give feedback
            </span>
          )}
        </span>
        <div className="flex items-center gap-2">
          {/* Download buttons - shown when complete */}
          {isComplete && onDownloadMarkdown && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onDownloadMarkdown}
              title="Download Markdown"
              className="gap-1.5 text-green-600 hover:text-green-700 hover:bg-green-50"
            >
              <Download className="w-4 h-4" />
              <span className="text-xs">Markdown</span>
            </Button>
          )}
          {isComplete && showDebugDownload && onDownloadDebug && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onDownloadDebug}
              title="Download Debug Bundle"
              className="gap-1.5 text-purple-600 hover:text-purple-700 hover:bg-purple-50"
            >
              <Bug className="w-4 h-4" />
              <span className="text-xs">Debug</span>
            </Button>
          )}
          {/* Copy page content */}
          {onCopy && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopy}
              title="Copy page markdown"
              className={cn(
                'gap-1.5',
                copied
                  ? 'text-green-600 hover:text-green-700 hover:bg-green-50'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              <span className="text-xs">{copied ? 'Copied' : 'Copy'}</span>
            </Button>
          )}
          {/* Raw/Preview toggle */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowRaw(!showRaw)}
            className="gap-2 text-uic-blue hover:text-uic-blue hover:bg-uic-blue/10"
          >
            {showRaw ? (
              <>
                <Eye className="w-4 h-4" />
                Preview
              </>
            ) : (
              <>
                <Code2 className="w-4 h-4" />
                Raw
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Content Area */}
      <div ref={contentRef} className="flex-1 overflow-y-auto overflow-x-hidden relative">
        <AnimatePresence mode="wait">
          {showRaw ? (
            <motion.pre
              key="raw"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="p-6 font-mono text-sm whitespace-pre-wrap break-words bg-slate-900 text-slate-100 min-h-full"
            >
              {content || 'Waiting for content...'}
            </motion.pre>
          ) : (
            <motion.div
              key="rendered"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="p-6 prose prose-slate max-w-none min-h-full break-words overflow-hidden"
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={{
                  // Custom heading styles with UIC colors
                  h1: ({ children }) => (
                    <h1 className="text-3xl font-bold text-uic-blue border-b-2 border-uic-red pb-2 mb-6">
                      {children}
                    </h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="text-2xl font-bold text-uic-blue mt-8 mb-4">{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="text-xl font-semibold text-uic-blue mt-6 mb-3">{children}</h3>
                  ),
                  h4: ({ children }) => (
                    <h4 className="text-lg font-semibold text-uic-blue mt-4 mb-2">{children}</h4>
                  ),
                  h5: ({ children }) => (
                    <h5 className="text-base font-semibold text-uic-blue mt-3 mb-2">{children}</h5>
                  ),
                  h6: ({ children }) => (
                    <h6 className="text-sm font-semibold text-uic-blue mt-3 mb-2">{children}</h6>
                  ),
                  // List styling
                  ul: ({ children }) => (
                    <ul className="list-disc list-inside space-y-1 my-4">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="list-decimal list-inside space-y-1 my-4">{children}</ol>
                  ),
                  li: ({ children }) => <li className="text-slate-700 leading-relaxed">{children}</li>,
                  // Paragraph styling
                  p: ({ children }) => <p className="text-slate-700 leading-relaxed my-4">{children}</p>,
                  // Code styling
                  code: ({ className, children }) => {
                    const isInline = !className;
                    return isInline ? (
                      <code className="bg-slate-100 text-uic-red px-1.5 py-0.5 rounded text-sm font-mono">
                        {children}
                      </code>
                    ) : (
                      <code className={cn('block', className)}>{children}</code>
                    );
                  },
                  pre: ({ children }) => (
                    <pre className="bg-slate-900 text-slate-100 p-4 rounded-lg overflow-x-auto my-4 text-sm">
                      {children}
                    </pre>
                  ),
                  // Blockquote styling
                  blockquote: ({ children }) => (
                    <blockquote className="border-l-4 border-uic-red pl-4 italic my-4 text-slate-600">
                      {children}
                    </blockquote>
                  ),
                  // Table styling
                  table: ({ children }) => (
                    <div className="overflow-x-auto my-4">
                      <table className="min-w-full border-collapse border border-slate-200">
                        {children}
                      </table>
                    </div>
                  ),
                  th: ({ children, scope, ...props }) => (
                    <th scope={scope || 'col'} className="bg-uic-blue !text-white px-4 py-2 text-left font-semibold border border-slate-200" {...props}>
                      {children}
                    </th>
                  ),
                  caption: ({ children }) => (
                    <caption className="caption-top font-semibold text-left px-4 py-2 text-[0.95em] text-slate-900">
                      {children}
                    </caption>
                  ),
                  td: ({ children }) => (
                    <td className="px-4 py-2 border border-slate-200">{children}</td>
                  ),
                  // Link styling
                  a: ({ href, children }) => (
                    <a
                      href={href}
                      className="text-uic-blue hover:text-uic-red underline underline-offset-2 transition-colors"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {children}
                    </a>
                  ),
                  // Image — resolve from figureMap; show placeholder until base64 arrives
                  img: ({ src, alt }) => {
                    const resolved = figureMap?.[src ?? ''];
                    if (!resolved) {
                      return <div className="max-w-full rounded my-4 h-48 bg-gray-100 dark:bg-gray-800 animate-pulse flex items-center justify-center text-sm text-gray-400">{alt || 'Loading figure…'}</div>;
                    }
                    return <img src={resolved} alt={alt ?? ''} className="max-w-full rounded my-4" />;
                  },
                  // Horizontal rule
                  hr: () => <hr className="my-8 border-t-2 border-slate-200" />,
                  // Definition list styling
                  dl: ({ children }) => <dl className="my-4">{children}</dl>,
                  dt: ({ children }) => <dt className="font-semibold text-slate-900 mt-3">{children}</dt>,
                  dd: ({ children }) => <dd className="ml-6 mt-1 text-slate-700">{children}</dd>,
                  // Strong/emphasis
                  strong: ({ children }) => <strong className="font-bold text-slate-900">{children}</strong>,
                  em: ({ children }) => <em className="italic">{children}</em>,
                }}
              >
                {content || '*Waiting for content...*'}
              </ReactMarkdown>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Selection popover */}
        <AnimatePresence>
          {feedbackEnabled && selection && !activeForm && (
            <SelectionPopover
              rect={selection.rect}
              containerRef={contentRef}
              onEdit={() => handlePopoverAction('edit')}
              onComment={() => handlePopoverAction('comment')}
              onDismiss={handlePopoverDismiss}
            />
          )}
        </AnimatePresence>

        {/* Inline feedback form */}
        <AnimatePresence>
          {feedbackEnabled && activeForm && (
            <InlineFeedbackForm
              type={activeForm.type}
              selectedText={activeForm.text}
              selector={activeForm.selector}
              rect={activeForm.rect}
              containerRef={contentRef}
              page={currentPage}
              onSubmit={handleFormSubmit}
              onCancel={handleFormCancel}
            />
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
