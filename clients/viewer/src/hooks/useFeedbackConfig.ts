import { useEffect, useState } from 'react';

const API_URL = import.meta.env.VITE_API_URL ?? '';

interface FeedbackConfig {
  enabled: boolean;
}

export function useFeedbackConfig() {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/v1/feedback/config`)
      .then((r) => (r.ok ? r.json() : { enabled: false }))
      .then((cfg: FeedbackConfig) => {
        if (!cancelled) setEnabled(!!cfg.enabled);
      })
      .catch(() => {
        if (!cancelled) setEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return enabled;
}
