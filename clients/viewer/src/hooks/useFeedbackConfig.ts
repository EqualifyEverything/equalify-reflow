import { useEffect, useState } from 'react';
import { apiFetch } from '@/auth/apiFetch';

interface FeedbackConfig {
  enabled: boolean;
}

export function useFeedbackConfig() {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiFetch('/api/v1/feedback/config')
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
