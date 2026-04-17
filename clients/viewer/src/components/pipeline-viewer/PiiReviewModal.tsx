import { useMemo, useState } from 'react';
import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { ShieldAlert, ChevronDown, ChevronRight, Eye, EyeOff } from 'lucide-react';
import type { PIIFinding } from '@/types/pipeline-viewer';

interface PiiReviewModalProps {
  findings: PIIFinding[];
  onDecision: (decision: 'approved' | 'denied') => void;
}

const ENTITY_LABELS: Record<string, string> = {
  EMAIL_ADDRESS: 'Email address',
  PHONE_NUMBER: 'Phone number',
  US_SSN: 'Social security number',
  CREDIT_CARD: 'Credit card',
  IBAN_CODE: 'Bank account (IBAN)',
  US_DRIVER_LICENSE: "Driver's license",
};

function maskSnippet(text: string, entityType: string): string {
  if (entityType === 'EMAIL_ADDRESS') {
    const at = text.indexOf('@');
    if (at <= 0) return '***';
    return `${text[0]}${'*'.repeat(Math.max(1, at - 1))}${text.slice(at)}`;
  }
  if (text.length <= 4) return '*'.repeat(text.length);
  return `${text.slice(0, 2)}${'*'.repeat(text.length - 4)}${text.slice(-2)}`;
}

export function PiiReviewModal({ findings, onDecision }: PiiReviewModalProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [revealed, setRevealed] = useState(false);

  const grouped = useMemo(() => {
    const map = new Map<string, PIIFinding[]>();
    for (const f of findings) {
      const list = map.get(f.entity_type) ?? [];
      list.push(f);
      map.set(f.entity_type, list);
    }
    return Array.from(map.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [findings]);

  return (
    <Modal
      onClose={() => {
        /* Require an explicit decision — don't close on Escape. */
      }}
      aria-label="Review potentially sensitive information"
      className="bg-white rounded-lg shadow-xl w-full max-w-xl mx-4 max-h-[85vh] flex flex-col"
    >
      <div className="flex items-start gap-3 px-5 py-4 border-b">
        <ShieldAlert className="w-6 h-6 text-amber-600 shrink-0 mt-0.5" aria-hidden="true" />
        <div className="flex-1">
          <h2 className="text-base font-semibold text-gray-900">
            Potentially sensitive information detected
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            This document contains {findings.length} match
            {findings.length === 1 ? '' : 'es'} that may be personally identifiable information.
            Review the findings and decide whether to continue processing.
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs text-muted-foreground">
            Detected via pattern-based scan (Microsoft Presidio). False positives are possible.
          </p>
          <button
            onClick={() => setRevealed((v) => !v)}
            className="text-xs text-uic-blue hover:underline inline-flex items-center gap-1"
          >
            {revealed ? (
              <>
                <EyeOff className="w-3.5 h-3.5" aria-hidden="true" />
                Mask matches
              </>
            ) : (
              <>
                <Eye className="w-3.5 h-3.5" aria-hidden="true" />
                Show matches
              </>
            )}
          </button>
        </div>

        <ul className="space-y-2">
          {grouped.map(([entityType, items]) => {
            const open = expanded[entityType] ?? false;
            const label = ENTITY_LABELS[entityType] ?? entityType;
            return (
              <li key={entityType} className="border rounded-md">
                <button
                  onClick={() => setExpanded((s) => ({ ...s, [entityType]: !open }))}
                  className="w-full flex items-center justify-between px-3 py-2 hover:bg-gray-50 rounded-md"
                  aria-expanded={open}
                >
                  <span className="flex items-center gap-2 text-sm font-medium text-gray-900">
                    {open ? (
                      <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" aria-hidden="true" />
                    )}
                    {label}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {items.length} match{items.length === 1 ? '' : 'es'}
                  </span>
                </button>
                {open && (
                  <ul className="px-3 pb-2 space-y-1 text-xs font-mono text-gray-700">
                    {items.slice(0, 20).map((f, idx) => (
                      <li key={idx} className="flex items-center justify-between gap-2 py-0.5">
                        <span className="truncate">
                          {revealed ? f.text : maskSnippet(f.text, f.entity_type)}
                        </span>
                        <span className="text-[10px] text-muted-foreground shrink-0">
                          {Math.round(f.score * 100)}%
                        </span>
                      </li>
                    ))}
                    {items.length > 20 && (
                      <li className="text-[11px] text-muted-foreground py-0.5">
                        + {items.length - 20} more
                      </li>
                    )}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      <div className="flex items-center justify-end gap-2 px-5 py-3 border-t bg-gray-50 rounded-b-lg">
        <Button variant="outline" onClick={() => onDecision('denied')}>
          Cancel processing
        </Button>
        <Button onClick={() => onDecision('approved')}>
          Continue anyway
        </Button>
      </div>
    </Modal>
  );
}
