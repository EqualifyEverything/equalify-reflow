import { useState } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import type { FeedbackItemType, FeedbackCategory, TextSelector } from '@/types/feedback';

const CATEGORIES: { value: FeedbackCategory; label: string }[] = [
  { value: 'content', label: 'Content' },
  { value: 'formatting', label: 'Formatting' },
  { value: 'accessibility', label: 'A11y' },
  { value: 'structure', label: 'Structure' },
];

interface InlineFeedbackFormProps {
  type: FeedbackItemType;
  selectedText: string;
  selector: TextSelector;
  rect: DOMRect;
  containerRef: React.RefObject<HTMLElement | null>;
  page: number;
  onSubmit: (data: {
    type: FeedbackItemType;
    selector: TextSelector;
    newText: string | null;
    description: string;
    category: FeedbackCategory | null;
    page: number;
  }) => void;
  onCancel: () => void;
}

export function InlineFeedbackForm({
  type,
  selectedText,
  selector,
  rect,
  containerRef,
  page,
  onSubmit,
  onCancel,
}: InlineFeedbackFormProps) {
  const [text, setText] = useState(type === 'edit' ? selectedText : '');
  const [category, setCategory] = useState<FeedbackCategory | null>(null);

  const handleSubmit = () => {
    if (type === 'edit' && text === selectedText) return; // no change
    if (type === 'comment' && !text.trim()) return; // empty comment

    onSubmit({
      type,
      selector,
      newText: type === 'edit' ? text : null,
      description: type === 'edit' ? `Replace "${truncate(selectedText, 30)}" with new text` : text,
      category,
      page,
    });
  };

  // Position relative to container
  const container = containerRef.current;
  if (!container) return null;

  const containerRect = container.getBoundingClientRect();
  const top = rect.bottom - containerRect.top + container.scrollTop + 8;
  const left = Math.max(
    8,
    Math.min(
      rect.left - containerRect.left,
      containerRect.width - 256,
    ),
  );

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.12 }}
      className="absolute z-50 w-60 bg-white border border-gray-200 rounded-lg shadow-lg"
      style={{ top, left }}
    >
      {/* Header */}
      <div className="px-3 py-2 border-b bg-gray-50 rounded-t-lg">
        <span className={cn(
          'text-xs font-medium',
          type === 'edit' ? 'text-blue-700' : 'text-purple-700',
        )}>
          {type === 'edit' ? 'Edit Text' : 'Add Comment'}
        </span>
      </div>

      {/* Body */}
      <div className="p-3 space-y-2">
        {/* Selected text preview (for comments) */}
        {type === 'comment' && (
          <div className="text-[10px] text-muted-foreground bg-gray-50 rounded px-2 py-1 line-clamp-2">
            "{truncate(selectedText, 60)}"
          </div>
        )}

        {/* Text input */}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={type === 'edit' ? 'Enter replacement text...' : 'Describe the issue...'}
          className="w-full h-20 px-2 py-1.5 text-xs border rounded resize-none focus:outline-none focus:ring-1 focus:ring-blue-400"
          autoFocus
        />

        {/* Category pills */}
        <div className="flex flex-wrap gap-1">
          {CATEGORIES.map((cat) => (
            <button
              key={cat.value}
              onClick={() => setCategory(category === cat.value ? null : cat.value)}
              className={cn(
                'px-2 py-0.5 rounded text-[10px] font-medium transition-colors',
                category === cat.value
                  ? 'bg-blue-100 text-blue-700'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200',
              )}
            >
              {cat.label}
            </button>
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 pt-1">
          <Button
            size="sm"
            variant="primary"
            className="h-7 text-xs flex-1"
            onClick={handleSubmit}
            disabled={
              (type === 'edit' && text === selectedText) ||
              (type === 'comment' && !text.trim())
            }
          >
            {type === 'edit' ? 'Add Edit' : 'Add Comment'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            onClick={onCancel}
          >
            Cancel
          </Button>
        </div>
      </div>
    </motion.div>
  );
}

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + '\u2026';
}
