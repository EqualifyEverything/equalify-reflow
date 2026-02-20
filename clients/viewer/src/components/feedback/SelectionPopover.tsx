import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { Pencil, MessageSquare } from 'lucide-react';

interface SelectionPopoverProps {
  /** Bounding rect of the selected text (viewport-relative) */
  rect: DOMRect;
  /** Container element to position relative to */
  containerRef: React.RefObject<HTMLElement | null>;
  onEdit: () => void;
  onComment: () => void;
  onDismiss: () => void;
}

export function SelectionPopover({
  rect,
  containerRef,
  onEdit,
  onComment,
  onDismiss,
}: SelectionPopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null);

  // Dismiss on click outside
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onDismiss();
      }
    };
    // Delay to avoid catching the mouseup that created us
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClick);
    }, 50);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClick);
    };
  }, [onDismiss]);

  // Dismiss on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onDismiss();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onDismiss]);

  // Position relative to container
  const container = containerRef.current;
  if (!container) return null;

  const containerRect = container.getBoundingClientRect();
  const top = rect.bottom - containerRect.top + container.scrollTop + 4;
  const left = rect.left - containerRect.left + rect.width / 2;

  return (
    <motion.div
      ref={popoverRef}
      initial={{ opacity: 0, scale: 0.9, y: -4 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.12 }}
      className="absolute z-50 flex items-center gap-1 px-1.5 py-1 bg-white border border-gray-200 rounded-lg shadow-lg"
      style={{
        top,
        left,
        transform: 'translateX(-50%)',
      }}
    >
      <button
        onClick={onEdit}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-gray-700 rounded-md hover:bg-blue-50 hover:text-blue-700 transition-colors"
        title="Edit this text"
      >
        <Pencil className="w-3.5 h-3.5" />
        Edit
      </button>
      <div className="w-px h-4 bg-gray-200" />
      <button
        onClick={onComment}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-gray-700 rounded-md hover:bg-purple-50 hover:text-purple-700 transition-colors"
        title="Add a comment"
      >
        <MessageSquare className="w-3.5 h-3.5" />
        Comment
      </button>
    </motion.div>
  );
}
