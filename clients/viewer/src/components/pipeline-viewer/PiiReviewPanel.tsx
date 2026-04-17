import { useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { ShieldAlert, ShieldCheck, XCircle, ChevronDown, ChevronRight, Eye, EyeOff, Loader2 } from 'lucide-react';
import type { PIIFinding } from '@/types/pipeline-viewer';

type PanelState = 'scanning' | 'awaiting' | 'approved' | 'denied' | 'clean' | 'error';

interface PiiReviewPanelProps {
  state: PanelState;
  findings: PIIFinding[];
  error?: string | null;
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

export function PiiReviewPanel({ state, findings, error, onDecision }: PiiReviewPanelProps) {
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

  if (state === 'scanning') {
    return (
      <div className="h-full flex items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3 text-center max-w-md">
          <Loader2 className="w-8 h-8 animate-spin text-uic-blue" aria-hidden="true" />
          <h2 className="text-base font-semibold text-gray-900">Scanning for sensitive information</h2>
          <p className="text-sm text-muted-foreground">
            Presidio is scanning the document text for patterns that may be personally
            identifiable information (emails, phone numbers, SSNs, card numbers). The
            pipeline will pause here if anything is flagged.
          </p>
        </div>
      </div>
    );
  }

  if (state === 'error') {
    return (
      <div className="h-full flex items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3 text-center max-w-md">
          <XCircle className="w-8 h-8 text-red-600" aria-hidden="true" />
          <h2 className="text-base font-semibold text-gray-900">PII scan failed</h2>
          <p className="text-sm text-muted-foreground">{error ?? 'Unknown error'}</p>
          <p className="text-xs text-muted-foreground">
            The pipeline is continuing without a completed PII check. Review the output
            carefully before sharing.
          </p>
        </div>
      </div>
    );
  }

  if (state === 'clean') {
    return (
      <div className="h-full flex items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3 text-center max-w-md">
          <ShieldCheck className="w-8 h-8 text-green-600" aria-hidden="true" />
          <h2 className="text-base font-semibold text-gray-900">No sensitive information detected</h2>
          <p className="text-sm text-muted-foreground">
            The pattern-based scan found no emails, phone numbers, or similar identifiers.
            False negatives are possible — review the output before sharing.
          </p>
        </div>
      </div>
    );
  }

  if (state === 'approved' || state === 'awaiting' || state === 'denied') {
    const heading =
      state === 'denied'
        ? 'Processing cancelled'
        : state === 'approved'
          ? 'Processing continuing with flagged content'
          : 'Potentially sensitive information detected';
    const Icon = state === 'denied' ? XCircle : ShieldAlert;
    const iconColor = state === 'denied' ? 'text-red-600' : 'text-amber-600';

    return (
      <div className="h-full overflow-y-auto">
        <div className="max-w-2xl mx-auto p-6 flex flex-col gap-4">
          <div className="flex items-start gap-3">
            <Icon className={`w-7 h-7 shrink-0 mt-0.5 ${iconColor}`} aria-hidden="true" />
            <div>
              <h2 className="text-base font-semibold text-gray-900">{heading}</h2>
              <p className="text-sm text-muted-foreground mt-1">
                {findings.length} match{findings.length === 1 ? '' : 'es'} from a pattern-based
                scan (Microsoft Presidio). False positives are possible.
              </p>
            </div>
          </div>

          <div className="border rounded-lg bg-white">
            <div className="flex items-center justify-between px-4 py-2 border-b">
              <h3 className="text-sm font-medium text-gray-900">Findings</h3>
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
            <ul className="divide-y">
              {grouped.map(([entityType, items]) => {
                const open = expanded[entityType] ?? false;
                const label = ENTITY_LABELS[entityType] ?? entityType;
                return (
                  <li key={entityType}>
                    <button
                      onClick={() => setExpanded((s) => ({ ...s, [entityType]: !open }))}
                      className="w-full flex items-center justify-between px-4 py-2 hover:bg-gray-50"
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
                      <ul className="px-4 pb-2 space-y-1 text-xs font-mono text-gray-700">
                        {items.slice(0, 50).map((f, idx) => (
                          <li key={idx} className="flex items-center justify-between gap-2 py-0.5">
                            <span className="truncate">
                              {revealed ? f.text : maskSnippet(f.text, f.entity_type)}
                            </span>
                            <span className="text-[10px] text-muted-foreground shrink-0">
                              {Math.round(f.score * 100)}%
                            </span>
                          </li>
                        ))}
                        {items.length > 50 && (
                          <li className="text-[11px] text-muted-foreground py-0.5">
                            + {items.length - 50} more
                          </li>
                        )}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>

          {state === 'awaiting' && (
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => onDecision('denied')}>
                Cancel processing
              </Button>
              <Button onClick={() => onDecision('approved')}>
                Continue anyway
              </Button>
            </div>
          )}
          {state === 'approved' && (
            <p className="text-xs text-muted-foreground">
              You chose to continue. Processing is resuming with the flagged content.
            </p>
          )}
          {state === 'denied' && (
            <p className="text-xs text-muted-foreground">
              You cancelled processing. No document data was sent to the AI pipeline.
            </p>
          )}
        </div>
      </div>
    );
  }

  return null;
}
