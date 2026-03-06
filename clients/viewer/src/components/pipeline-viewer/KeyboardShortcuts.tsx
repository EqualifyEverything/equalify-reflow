import { useState } from 'react';
import { useHotkey } from '@tanstack/react-hotkeys';
import { Modal } from '@/components/ui/modal';
import { X, Keyboard } from 'lucide-react';

const isMac = typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.userAgent);
const MOD_LABEL = isMac ? '⌘' : 'Ctrl';

interface ShortcutDef {
  keys: string;
  label: string;
  description: string;
}

const SHORTCUTS: ShortcutDef[] = [
  { keys: `${MOD_LABEL}+1`, label: 'Page Picker', description: 'Jump to the page navigation sidebar' },
  { keys: `${MOD_LABEL}+2`, label: 'Stage Picker', description: 'Jump to the pipeline stage tabs' },
  { keys: `${MOD_LABEL}+3`, label: 'Rendered Preview', description: 'Jump to the rendered markdown preview' },
  { keys: `${MOD_LABEL}+4`, label: 'Changes Panel', description: 'Jump to the changes sidebar' },
  { keys: `${MOD_LABEL}+/`, label: 'Keyboard Shortcuts', description: 'Open this help dialog' },
];

interface KeyboardShortcutsProps {
  onFocusRegion: (region: 'pages' | 'stages' | 'preview' | 'changes') => void;
}

export function KeyboardShortcuts({ onFocusRegion }: KeyboardShortcutsProps) {
  const [helpOpen, setHelpOpen] = useState(false);

  useHotkey('Mod+1', (e) => {
    e.preventDefault();
    onFocusRegion('pages');
  });

  useHotkey('Mod+2', (e) => {
    e.preventDefault();
    onFocusRegion('stages');
  });

  useHotkey('Mod+3', (e) => {
    e.preventDefault();
    onFocusRegion('preview');
  });

  useHotkey('Mod+4', (e) => {
    e.preventDefault();
    onFocusRegion('changes');
  });

  useHotkey('Mod+/', (e) => {
    e.preventDefault();
    setHelpOpen((prev) => !prev);
  });

  return (
    <>
      {helpOpen && (
        <Modal onClose={() => setHelpOpen(false)} aria-label="Keyboard Shortcuts" className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4">
          <div className="flex items-center justify-between px-6 py-4 border-b">
            <div className="flex items-center gap-2">
              <Keyboard className="w-4 h-4 text-uic-blue" />
              <h2 className="text-base font-semibold text-gray-800">Keyboard Shortcuts</h2>
            </div>
            <button
              onClick={() => setHelpOpen(false)}
              className="p-1.5 rounded-md hover:bg-gray-100 transition-colors"
              aria-label="Close"
            >
              <X className="w-4 h-4 text-muted-foreground" />
            </button>
          </div>
          <div className="px-6 py-4 space-y-3">
            {SHORTCUTS.map((s) => (
              <div key={s.keys} className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-800">{s.label}</p>
                  <p className="text-xs text-muted-foreground">{s.description}</p>
                </div>
                <kbd className="px-2.5 py-1 rounded-md bg-gray-100 border text-xs font-mono font-medium text-gray-700 shrink-0 ml-4">
                  {s.keys}
                </kbd>
              </div>
            ))}
          </div>
          <div className="px-6 py-3 border-t bg-gray-50 rounded-b-xl">
            <p className="text-[10px] text-muted-foreground">
              These shortcuts are designed to avoid conflicts with screen readers (JAWS, NVDA, VoiceOver).
            </p>
          </div>
        </Modal>
      )}
    </>
  );
}
