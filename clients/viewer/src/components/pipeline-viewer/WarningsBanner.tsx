import { AlertTriangle, X } from 'lucide-react';
import { useState } from 'react';

interface WarningsBannerProps {
  warnings: string[];
}

export function WarningsBanner({ warnings }: WarningsBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed || warnings.length === 0) return null;

  return (
    <div className="flex items-start gap-3 px-6 py-3 bg-amber-50 border-b border-amber-200 text-sm text-amber-800">
      <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-600" />
      <div className="flex-1 min-w-0">
        {warnings.length === 1 ? (
          <p>{warnings[0]}</p>
        ) : (
          <ul className="list-disc list-inside space-y-0.5">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="flex-shrink-0 p-0.5 rounded hover:bg-amber-100 transition-colors"
        aria-label="Dismiss warnings"
      >
        <X className="w-3.5 h-3.5 text-amber-600" />
      </button>
    </div>
  );
}
