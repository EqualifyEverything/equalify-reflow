import { useState, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

interface CollapsibleRegionProps {
  /** Stable id used to derive heading and body element ids. */
  id: string;
  /** Visible heading text; also used as the landmark's accessible name. */
  title: string;
  defaultOpen?: boolean;
  className?: string;
  /** Applied to the expanded body wrapper (e.g. max-height, padding). */
  bodyClassName?: string;
  children: ReactNode;
}

/**
 * Disclosure that is also a labelled landmark. Renders a `<section>` with
 * `aria-labelledby` pointing at a visible `<h2>` inside the toggle button.
 * Collapsed state hides the body from both screen readers and sighted users;
 * the heading remains visible so the region stays discoverable.
 */
export function CollapsibleRegion({
  id,
  title,
  defaultOpen = false,
  className,
  bodyClassName,
  children,
}: CollapsibleRegionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const headingId = `${id}-heading`;
  const bodyId = `${id}-body`;

  return (
    <section
      id={id}
      aria-labelledby={headingId}
      className={cn('border-b bg-white', className)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-gray-50 focus:outline-2 focus:outline-uic-blue focus:outline-offset-[-2px]"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            'w-4 h-4 text-muted-foreground transition-transform flex-shrink-0',
            open && 'rotate-90',
          )}
        />
        <h2 id={headingId} className="text-sm font-medium">
          {title}
        </h2>
      </button>
      {open && (
        <div id={bodyId} className={cn('max-h-[50vh] overflow-auto', bodyClassName)}>
          {children}
        </div>
      )}
    </section>
  );
}
