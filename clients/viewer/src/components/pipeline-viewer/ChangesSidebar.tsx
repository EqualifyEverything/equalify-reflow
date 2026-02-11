import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Input } from '@/components/ui/input';
import { Search, ChevronDown, ChevronUp } from 'lucide-react';
import type { DocumentChange } from '@/types/pipeline-viewer';

interface ChangesSidebarProps {
  changes: DocumentChange[];
  totalPages: number;
}

export function ChangesSidebar({ changes, totalPages }: ChangesSidebarProps) {
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

  if (changes.length === 0) {
    return (
      <div className="w-64 flex-shrink-0 border-l bg-white flex flex-col">
        <div className="px-4 py-3 border-b">
          <h3 className="text-sm font-medium text-muted-foreground">Changes</h3>
        </div>
        <div className="flex-1 flex items-center justify-center p-4">
          <p className="text-xs text-muted-foreground text-center">
            No changes in this step.
            <br />
            Docling produces v0 from scratch.
          </p>
        </div>
      </div>
    );
  }

  const pages = Array.from(new Set(changes.map((c) => c.page))).sort((a, b) => a - b);

  return (
    <div className="w-64 flex-shrink-0 border-l bg-white flex flex-col">
      {/* Header + filters */}
      <div className="px-3 py-2 border-b space-y-2">
        <h3 className="text-sm font-medium text-muted-foreground">
          Changes ({filtered.length}/{changes.length})
        </h3>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <Input
            placeholder="Filter..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-7 text-xs pl-7"
          />
        </div>
        {totalPages > 1 && (
          <select
            value={pageFilter ?? ''}
            onChange={(e) => setPageFilter(e.target.value ? Number(e.target.value) : null)}
            className="w-full h-7 text-xs border rounded px-2"
          >
            <option value="">All pages</option>
            {pages.map((p) => (
              <option key={p} value={p}>
                Page {p}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Change cards */}
      <div className="flex-1 overflow-y-auto">
        {filtered.map((change, idx) => {
          const isExpanded = expandedIdx === idx;
          return (
            <button
              key={idx}
              onClick={() => setExpandedIdx(isExpanded ? null : idx)}
              className={cn(
                'w-full text-left px-3 py-2 border-b hover:bg-gray-50 transition-colors',
                isExpanded && 'bg-gray-50',
              )}
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 font-medium">
                  p{change.page}
                </span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-purple-50 text-purple-700">
                  {change.stage}
                </span>
                {isExpanded ? (
                  <ChevronUp className="w-3 h-3 ml-auto text-muted-foreground" />
                ) : (
                  <ChevronDown className="w-3 h-3 ml-auto text-muted-foreground" />
                )}
              </div>
              <p className="text-xs text-muted-foreground line-clamp-2">{change.reasoning}</p>
              {isExpanded && (
                <div className="mt-2 space-y-1">
                  <div className="text-xs">
                    <span className="font-medium text-red-600">Before:</span>
                    <pre className="mt-0.5 p-1.5 bg-red-50 rounded text-[10px] whitespace-pre-wrap break-words max-h-24 overflow-auto">
                      {change.old_text || '(empty)'}
                    </pre>
                  </div>
                  <div className="text-xs">
                    <span className="font-medium text-green-600">After:</span>
                    <pre className="mt-0.5 p-1.5 bg-green-50 rounded text-[10px] whitespace-pre-wrap break-words max-h-24 overflow-auto">
                      {change.new_text || '(empty)'}
                    </pre>
                  </div>
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
