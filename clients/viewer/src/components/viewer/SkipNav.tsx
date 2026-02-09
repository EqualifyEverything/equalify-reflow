import { useEffect, useRef, useState } from 'react';

interface SkipNavLink {
  id: string;
  label: string;
}

interface SkipNavProps {
  links: SkipNavLink[];
}

/**
 * Skip Navigation Menu - Accessible navigation hub
 *
 * Appears when:
 * - User presses Ctrl+/ (Windows/Linux) or Cmd+/ (Mac)
 * - User tabs into the skip nav links (focus-within)
 *
 * Allows keyboard users to quickly jump between major sections.
 */
export function SkipNav({ links }: SkipNavProps) {
  const [isVisible, setIsVisible] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd+/ (Mac) or Ctrl+/ (Windows/Linux)
      if ((e.metaKey || e.ctrlKey) && e.key === '/') {
        e.preventDefault();
        setIsVisible(true);
        // Focus first link after state update
        setTimeout(() => firstLinkRef.current?.focus(), 0);
      }
      // Escape closes menu
      if (e.key === 'Escape' && isVisible) {
        setIsVisible(false);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isVisible]);

  // Hide when focus leaves menu entirely
  const handleBlur = (e: React.FocusEvent) => {
    // Check if new focus target is outside the menu
    if (!menuRef.current?.contains(e.relatedTarget as Node)) {
      setIsVisible(false);
    }
  };

  const handleLinkClick = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    setIsVisible(false);
    const target = document.getElementById(id);
    if (target) {
      target.focus();
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  return (
    <div
      ref={menuRef}
      onBlur={handleBlur}
      className={`
        fixed top-0 left-0 right-0 z-50 bg-uic-blue text-white px-4 py-2
        transform transition-transform duration-150 ease-out
        ${isVisible ? 'translate-y-0' : '-translate-y-full'}
        focus-within:translate-y-0
      `}
      role="navigation"
      aria-label="Skip navigation"
    >
      <div className="max-w-screen-xl mx-auto flex items-center gap-4">
        <span className="text-sm font-medium text-white/70">Jump to:</span>
        {links.map((link, index) => (
          <a
            key={link.id}
            ref={index === 0 ? firstLinkRef : undefined}
            href={`#${link.id}`}
            onClick={(e) => handleLinkClick(e, link.id)}
            className="
              px-3 py-1 text-sm rounded
              bg-white/10 hover:bg-white/20
              focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2 focus:ring-offset-uic-blue
              transition-colors
            "
          >
            {link.label}
          </a>
        ))}
        <span className="ml-auto text-xs text-white/50">Esc to close</span>
      </div>
    </div>
  );
}
