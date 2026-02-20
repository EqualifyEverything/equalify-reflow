import { useState, useEffect, useCallback, type RefObject } from 'react';

export interface TextSelectionState {
  text: string;
  /** Container-relative position (accounts for scroll at capture time). */
  rect: { top: number; left: number; width: number; height: number; bottom: number };
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

      // Convert viewport-relative rect to container-relative coordinates
      // at capture time so it stays correct after scroll.
      const viewportRect = range.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      const rect = {
        top: viewportRect.top - containerRect.top + container.scrollTop,
        left: viewportRect.left - containerRect.left + container.scrollLeft,
        width: viewportRect.width,
        height: viewportRect.height,
        bottom: viewportRect.bottom - containerRect.top + container.scrollTop,
      };

      // Compute prefix/suffix using Range character offsets
      const prefix = getContextFromRange(range, 'before', 20);
      const suffix = getContextFromRange(range, 'after', 20);

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
 * Extract ~charCount characters of text before or after a Range,
 * using the Range's actual character offset within the block element.
 */
function getContextFromRange(range: Range, direction: 'before' | 'after', charCount: number): string {
  try {
    const container = range.commonAncestorContainer;
    let block = container.nodeType === Node.ELEMENT_NODE
      ? container as HTMLElement
      : container.parentElement;
    while (block && !isBlockElement(block) && block.parentElement) {
      block = block.parentElement;
    }
    if (!block) return '';

    const fullText = block.textContent ?? '';

    // Compute the character offset of the selection start/end within
    // the block element's textContent by creating temporary ranges.
    const preRange = document.createRange();
    preRange.selectNodeContents(block);
    preRange.setEnd(range.startContainer, range.startOffset);
    const startOffset = preRange.toString().length;

    const endOffset = startOffset + range.toString().length;

    if (direction === 'before') {
      const start = Math.max(0, startOffset - charCount);
      return fullText.slice(start, startOffset);
    } else {
      return fullText.slice(endOffset, endOffset + charCount);
    }
  } catch {
    return '';
  }
}

function isBlockElement(el: HTMLElement): boolean {
  const display = window.getComputedStyle(el).display;
  return display === 'block' || display === 'flex' || display === 'grid' || el.tagName === 'DIV' || el.tagName === 'P';
}
