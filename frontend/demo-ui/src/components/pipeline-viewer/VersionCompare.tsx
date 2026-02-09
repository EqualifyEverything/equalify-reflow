import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { GitCompareArrows } from 'lucide-react';
import type { StepResult } from '@/types/pipeline-viewer';

interface VersionCompareProps {
  steps: StepResult[];
  compareMode: boolean;
  onToggleCompare: () => void;
  versionA: string;
  versionB: string;
  onChangeA: (v: string) => void;
  onChangeB: (v: string) => void;
}

export function VersionCompare({
  steps,
  compareMode,
  onToggleCompare,
  versionA,
  versionB,
  onChangeA,
  onChangeB,
}: VersionCompareProps) {
  // Collect all unique versions
  const versions = steps.map((s) => ({
    version: s.version_after,
    label: `${s.version_after} (${s.display_name})`,
  }));

  const disabled = versions.length < 2;

  return (
    <div className="flex items-center gap-2">
      <Button
        variant={compareMode ? 'default' : 'outline'}
        size="sm"
        className={cn('h-7 text-xs gap-1.5', compareMode && 'bg-uic-blue hover:bg-uic-blue/90')}
        disabled={disabled}
        onClick={onToggleCompare}
        title={disabled ? 'Need at least 2 versions to compare' : 'Toggle version comparison'}
      >
        <GitCompareArrows className="w-3.5 h-3.5" />
        Compare
      </Button>
      {compareMode && (
        <>
          <select
            value={versionA}
            onChange={(e) => onChangeA(e.target.value)}
            className="h-7 text-xs border rounded px-2"
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {v.label}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">vs</span>
          <select
            value={versionB}
            onChange={(e) => onChangeB(e.target.value)}
            className="h-7 text-xs border rounded px-2"
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {v.label}
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}
