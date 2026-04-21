import { useEffect, useState } from 'react';

/**
 * Reactive wrapper around `window.matchMedia`. Returns whether the given media
 * query currently matches and updates on viewport changes.
 *
 * Example: `const isDesktop = useMediaQuery('(min-width: 1024px)');`
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.matchMedia(query).matches : false,
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener('change', onChange);
    setMatches(mql.matches);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}
