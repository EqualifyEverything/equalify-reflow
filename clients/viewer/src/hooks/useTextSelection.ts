import { useState, useEffect, useCallback, type RefObject } from 'react';

export interface TextSelectionState {
  text: string;
  rect: DOMRect;
  prefix: string;
  suffix: string;
}

/**
 * Hook that tracks text selection within a container element.
 * Returns selection details (text, position rect, surrounding context)
 * when the user selects text via mouse.
 */
export function useTextSelection(containerRef: RefObject<HTMLElement | null>) {
  const [selection, setSelection] = useState<TextSelectionState | null>(null);

  const clearSelection = useCallback(() => {
    setSelection(null);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleMouseUp = () => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        return;
      }

      const range = sel.getRangeAt(0);

      // Only track selections inside our container
      if (!container.contains(range.commonAncestorContainer)) {
        return;
      }

      const text = sel.toString().trim();
      if (!text) return;

      // Get bounding rect relative to viewport
      const rect = range.getBoundingClientRect();

      // Compute prefix/suffix from surrounding text
      const prefix = getContext(range, 'before', 20);
      const suffix = getContext(range, 'after', 20);

      setSelection({ text, rect, prefix, suffix });
    };

    const handleMouseDown = () => {
      // Clear previous selection on new click
      setSelection(null);
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setSelection(null);
        window.getSelection()?.removeAllRanges();
      }
    };

    container.addEventListener('mouseup', handleMouseUp);
    container.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      container.removeEventListener('mouseup', handleMouseUp);
      container.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [containerRef]);

  return { selection, clearSelection };
}

/**
 * Extract ~charCount characters of text before or after a Range.
 */
function getContext(range: Range, direction: 'before' | 'after', charCount: number): string {
  try {
    const container = range.commonAncestorContainer;
    // Walk up to a block-level element to get text context
    let block = container.nodeType === Node.ELEMENT_NODE
      ? container as HTMLElement
      : container.parentElement;
    // Walk up until we find a reasonable block container
    while (block && !isBlockElement(block) && block.parentElement) {
      block = block.parentElement;
    }
    if (!block) return '';

    const fullText = block.textContent ?? '';
    const selectedText = range.toString();
    const idx = fullText.indexOf(selectedText);
    if (idx === -1) return '';

    if (direction === 'before') {
      const start = Math.max(0, idx - charCount);
      return fullText.slice(start, idx);
    } else {
      const end = idx + selectedText.length;
      return fullText.slice(end, end + charCount);
    }
  } catch {
    return '';
  }
}

function isBlockElement(el: HTMLElement): boolean {
  const display = window.getComputedStyle(el).display;
  return display === 'block' || display === 'flex' || display === 'grid' || el.tagName === 'DIV' || el.tagName === 'P';
}
