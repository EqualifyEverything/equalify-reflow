import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Eye, Code2, Download, Bug, Copy, Check } from 'lucide-react';

interface MarkdownViewerProps {
  content: string;
  className?: string;
  isComplete?: boolean;
  showDebugDownload?: boolean;
  onDownloadMarkdown?: () => void;
  onDownloadDebug?: () => void;
  onCopy?: () => void;
}

export function MarkdownViewer({
  content,
  className,
  isComplete = false,
  showDebugDownload = false,
  onDownloadMarkdown,
  onDownloadDebug,
  onCopy,
}: MarkdownViewerProps) {
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (onCopy) {
      onCopy();
    } else {
      navigator.clipboard.writeText(content);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className={cn('flex flex-col h-full', className)}>
      {/* Toggle Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-gray-50">
        <span className="text-sm font-medium text-muted-foreground">
          {showRaw ? 'Raw Markdown' : 'Rendered Preview'}
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
      <div className="flex-1 overflow-y-auto">
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
              className="p-6 prose prose-slate max-w-none min-h-full"
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
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
                  th: ({ children }) => (
                    <th className="bg-uic-blue text-white px-4 py-2 text-left font-semibold border border-slate-200">
                      {children}
                    </th>
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
                  // Horizontal rule
                  hr: () => <hr className="my-8 border-t-2 border-slate-200" />,
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
      </div>
    </div>
  );
}
