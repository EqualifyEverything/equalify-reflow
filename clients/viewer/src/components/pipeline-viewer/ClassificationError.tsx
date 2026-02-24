import { ShieldAlert, FileWarning, Lock, FileX, FileStack } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { StepResult } from '@/types/pipeline-viewer';

interface ClassificationErrorProps {
  step: StepResult;
  onReset: () => void;
}

const ICON_MAP: Record<string, typeof ShieldAlert> = {
  form_acroform: FileWarning,
  form_xfa: FileWarning,
  encrypted: Lock,
  empty_document: FileX,
  too_many_pages: FileStack,
};

function getIcon(findings: Array<{ code: string; severity: string }>) {
  for (const f of findings) {
    if (f.severity === 'error' && ICON_MAP[f.code]) {
      return ICON_MAP[f.code];
    }
  }
  return ShieldAlert;
}

export function ClassificationError({ step, onReset }: ClassificationErrorProps) {
  const findings = (step.metadata?.findings ?? []) as Array<{
    code: string;
    severity: string;
    message: string;
  }>;
  const docType = step.metadata?.document_type as string | undefined;
  const errors = findings.filter((f) => f.severity === 'error');
  const warnings = findings.filter((f) => f.severity === 'warning');
  const Icon = getIcon(findings);

  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="max-w-lg text-center">
        <div className="mx-auto w-16 h-16 rounded-full bg-red-50 flex items-center justify-center mb-4">
          <Icon className="w-8 h-8 text-red-500" />
        </div>

        <h2 className="text-lg font-semibold text-gray-900 mb-1">
          Unsupported Document
        </h2>

        {docType && docType !== 'unknown' && (
          <p className="text-xs font-medium text-red-600 uppercase tracking-wide mb-3">
            Detected type: {docType.replace('_', ' ')}
          </p>
        )}

        <div className="space-y-2 mb-4 text-left">
          {errors.map((f, i) => (
            <div
              key={i}
              className="flex items-start gap-2 px-4 py-2.5 rounded-lg bg-red-50 text-sm text-red-800"
            >
              <ShieldAlert className="w-4 h-4 mt-0.5 flex-shrink-0 text-red-500" />
              <span>{f.message}</span>
            </div>
          ))}
        </div>

        {warnings.length > 0 && (
          <div className="space-y-1.5 mb-4 text-left">
            {warnings.map((f, i) => (
              <p key={i} className="text-xs text-amber-700 px-4">
                {f.message}
              </p>
            ))}
          </div>
        )}

        <p className="text-sm text-muted-foreground mb-4">
          {step.elapsed_ms}ms classification time
        </p>

        <Button variant="outline" onClick={onReset}>
          Upload a Different PDF
        </Button>
      </div>
    </div>
  );
}
