import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Modal } from '@/components/ui/modal';
import { X, ChevronDown, ChevronUp } from 'lucide-react';

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
        className="w-full flex items-center justify-between px-6 py-3 hover:bg-gray-50 transition-colors"
      >
        <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          {title}{count != null ? ` (${count})` : ''}
        </h4>
        {open ? (
          <ChevronUp className="w-4 h-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="w-4 h-4 text-muted-foreground" />
        )}
      </button>
      {open && <div className="px-6 pb-4">{children}</div>}
    </div>
  );
}

interface StructureMetadataModalProps {
  metadata: Record<string, unknown>;
  onClose: () => void;
}

export function StructureMetadataModal({ metadata, onClose }: StructureMetadataModalProps) {
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

  return (
    <Modal onClose={onClose} aria-label="Structure metadata" className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col mx-4">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b">
        <h2 className="text-base font-semibold text-gray-800">Structure Metadata</h2>
        <button
          onClick={onClose}
          className="p-1.5 rounded-md hover:bg-gray-100 transition-colors"
          aria-label="Close"
        >
          <X className="w-4 h-4 text-muted-foreground" />
        </button>
      </div>

        <div className="flex-1 overflow-y-auto">
          {/* Document summary */}
          {totalPages > 0 && (
            <div className="px-6 py-4 border-b bg-gray-50/50">
              <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
                Document Summary
              </h4>
              <div className="flex flex-wrap gap-2 mb-3">
                {[...layouts.entries()].map(([layout, count]) => {
                  const style = LAYOUT_STYLES[layout] ?? { bg: 'bg-gray-100', text: 'text-gray-700', label: layout };
                  return (
                    <span
                      key={layout}
                      className={cn('px-3 py-1.5 rounded-md text-sm font-medium', style.bg, style.text)}
                    >
                      {style.label}
                      {count < totalPages && (
                        <span className="ml-1.5 opacity-60">{count}/{totalPages}</span>
                      )}
                    </span>
                  );
                })}
              </div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(flagCounts)
                  .filter(([, count]) => count > 0)
                  .map(([flag, count]) => {
                    const style = FLAG_STYLES[flag]!;
                    return (
                      <span
                        key={flag}
                        className={cn('px-2.5 py-1 rounded text-xs font-medium', style.bg, style.text)}
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
            <CollapsibleSection title="Page Attributes" count={totalPages}>
              <div className="space-y-2">
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
                    <div key={page} className="flex items-center gap-2 text-sm">
                      <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-600 font-mono font-medium shrink-0 w-8 text-center">
                        {page}
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        <span className={cn('px-2 py-0.5 rounded font-medium text-xs', layoutStyle.bg, layoutStyle.text)}>
                          {layoutStyle.label.toLowerCase().replace(' ', '-')}
                        </span>
                        {activeFlags.map((flag) => {
                          const style = FLAG_STYLES[flag]!;
                          return (
                            <span
                              key={flag}
                              className={cn('px-2 py-0.5 rounded font-medium text-xs', style.bg, style.text)}
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
            <CollapsibleSection title="Outline" count={outline.length}>
              <div className="space-y-1">
                {outline.map((entry, idx) => (
                  <div
                    key={idx}
                    className="text-sm text-muted-foreground"
                    style={{ paddingLeft: `${(entry.level - 1) * 16}px` }}
                  >
                    <span className="text-gray-400 mr-1.5">{'#'.repeat(entry.level)}</span>
                    <span>{entry.text}</span>
                    <span className="text-gray-300 ml-1.5">p{entry.page}</span>
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          )}

          {/* Code blocks */}
          {codeBlocks.length > 0 && (
            <CollapsibleSection title="Code Blocks" count={codeBlocks.length}>
              <div className="space-y-3">
                {codeBlocks.map((cb, idx) => (
                  <div key={idx} className="text-sm">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-100 font-mono font-medium text-xs">
                        {cb.language}
                      </span>
                      <span className="text-gray-400">p{cb.page}</span>
                    </div>
                    <p className="text-muted-foreground pl-1 font-mono text-xs">{cb.first_line}</p>
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          )}

          {/* Footnotes */}
          {footnotes.length > 0 && (
            <CollapsibleSection title="Footnotes" count={footnotes.length}>
              <div className="space-y-3">
                {footnotes.map((fn, idx) => (
                  <div key={idx} className="text-sm">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="px-2 py-0.5 rounded bg-amber-50 text-amber-700 font-medium text-xs">
                        [{fn.number}]
                      </span>
                      <span className="text-gray-400">p{fn.source_page}</span>
                    </div>
                    <p className="text-muted-foreground pl-1">{fn.body_text}</p>
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          )}
        </div>
    </Modal>
  );
}
