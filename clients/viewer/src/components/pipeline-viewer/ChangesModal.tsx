import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Input } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal';
import { Search, ChevronDown, ChevronUp, X } from 'lucide-react';
import type { DocumentChange } from '@/types/pipeline-viewer';

interface ChangesModalProps {
  changes: DocumentChange[];
  totalPages: number;
  onClose: () => void;
}

export function ChangesModal({ changes, totalPages, onClose }: ChangesModalProps) {
  const [search, setSearch] = useState('');
  const [pageFilter, setPageFilter] = useState<number | null>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const filtered = useMemo(() => {
    let result = changes;
    if (pageFilter !== null) {
      result = result.filter((c) => c.page === pageFilter);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (c) =>
          c.reasoning.toLowerCase().includes(q) ||
          c.old_text.toLowerCase().includes(q) ||
          c.new_text.toLowerCase().includes(q),
      );
    }
    return result;
  }, [changes, pageFilter, search]);

  const pages = Array.from(new Set(changes.map((c) => c.page))).sort((a, b) => a - b);

  return (
    <Modal onClose={onClose} aria-label="Document changes" className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col mx-4">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b">
        <h2 className="text-base font-semibold text-gray-800">
          Changes ({filtered.length}/{changes.length})
        </h2>
        <button
          onClick={onClose}
          className="p-1.5 rounded-md hover:bg-gray-100 transition-colors"
          aria-label="Close"
        >
          <X className="w-4 h-4 text-muted-foreground" />
        </button>
      </div>

      {/* Filters */}
      <div className="px-6 py-3 border-b flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Filter changes..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 text-sm pl-9"
          />
        </div>
        {totalPages > 1 && (
          <select
            value={pageFilter ?? ''}
            onChange={(e) => setPageFilter(e.target.value ? Number(e.target.value) : null)}
            className="h-8 text-sm border rounded px-2"
            aria-label="Filter by page"
          >
            <option value="">All pages</option>
            {pages.map((p) => (
              <option key={p} value={p}>Page {p}</option>
            ))}
          </select>
        )}
      </div>

      {/* Change list */}
      <div className="flex-1 overflow-y-auto px-6 py-3 space-y-2">
        {filtered.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            {changes.length === 0 ? 'No changes in this stage.' : 'No changes match your filter.'}
          </p>
        ) : (
          filtered.map((change, idx) => {
            const isExpanded = expandedIdx === idx;
            return (
              <button
                key={idx}
                onClick={() => setExpandedIdx(isExpanded ? null : idx)}
                className={cn(
                  'w-full text-left px-4 py-3 rounded-lg border hover:bg-gray-50 transition-colors',
                  isExpanded && 'bg-gray-50 border-uic-blue/30',
                )}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700 font-medium">
                    Page {change.page}
                  </span>
                  <span className="text-xs px-2 py-0.5 rounded bg-purple-50 text-purple-700">
                    {change.stage}
                  </span>
                  {isExpanded ? (
                    <ChevronUp className="w-3.5 h-3.5 ml-auto text-muted-foreground" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5 ml-auto text-muted-foreground" />
                  )}
                </div>
                <p className={cn('text-sm text-muted-foreground', !isExpanded && 'line-clamp-2')}>
                  {change.reasoning}
                </p>
                {isExpanded && (
                  <div className="mt-3 space-y-2">
                    <div>
                      <span className="text-xs font-medium text-red-600">Before:</span>
                      <pre className="mt-1 p-2.5 bg-red-50 rounded-md text-xs whitespace-pre-wrap break-words max-h-40 overflow-auto">
                        {change.old_text || '(empty)'}
                      </pre>
                    </div>
                    <div>
                      <span className="text-xs font-medium text-green-600">After:</span>
                      <pre className="mt-1 p-2.5 bg-green-50 rounded-md text-xs whitespace-pre-wrap break-words max-h-40 overflow-auto">
                        {change.new_text || '(empty)'}
                      </pre>
                    </div>
                  </div>
                )}
              </button>
            );
          })
        )}
      </div>
    </Modal>
  );
}
