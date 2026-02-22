import { useState, useCallback, useRef } from 'react';
import type {
  FeedbackPhase,
  FeedbackItem,
  CandidateChange,
  ChangeReview,
  ChangeDecision,
  ReviewAction,
  FeedbackResponse,
  ReviewResponse,
} from '@/types/feedback';

const API_URL = import.meta.env.VITE_API_URL ?? '';

interface UseFeedbackSessionOptions {
  onVersionUpdate?: (key: string, markdown: string) => void;
}

export function useFeedbackSession(
  sessionId: string | null,
  options: UseFeedbackSessionOptions = {},
) {
  const [phase, setPhase] = useState<FeedbackPhase | null>(null);
  const [pendingItems, setPendingItems] = useState<FeedbackItem[]>([]);
  const [candidates, setCandidates] = useState<CandidateChange[]>([]);
  const [reviews, setReviews] = useState<Map<string, ChangeReview>>(new Map());
  const [revisionRound, setRevisionRound] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [feedbackVersion, setFeedbackVersion] = useState<string | null>(null);

  // Use a ref for the callback to avoid re-creating memoized functions
  // when the caller passes a new options object each render.
  const onVersionUpdateRef = useRef(options.onVersionUpdate);
  onVersionUpdateRef.current = options.onVersionUpdate;

  const enterFeedbackMode = useCallback(() => {
    setPhase('collecting');
    setPendingItems([]);
    setCandidates([]);
    setReviews(new Map());
    setError(null);
  }, []);

  const exitFeedbackMode = useCallback(() => {
    setPhase(null);
    setPendingItems([]);
    setCandidates([]);
    setReviews(new Map());
    setError(null);
    setFeedbackVersion(null);
  }, []);

  const backToCollecting = useCallback(() => {
    setPhase('collecting');
    setPendingItems([]);
    setCandidates([]);
    setReviews(new Map());
    setError(null);
  }, []);

  const addFeedbackItem = useCallback((item: FeedbackItem) => {
    setPendingItems((prev) => [...prev, item]);
  }, []);

  const removeFeedbackItem = useCallback((id: string) => {
    setPendingItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const submitFeedback = useCallback(async () => {
    if (!sessionId || pendingItems.length === 0) return;

    setPhase('submitting');
    setError(null);

    try {
      const res = await fetch(`${API_URL}/api/v1/pipeline/sessions/${sessionId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: pendingItems }),
      });

      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`Feedback submission failed (${res.status}): ${detail}`);
      }

      const data: FeedbackResponse = await res.json();
      setCandidates(data.candidates);
      setRevisionRound(data.revision_round);
      setReviews(new Map());
      setPhase('reviewing');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setPhase('collecting');
    }
  }, [sessionId, pendingItems]);

  const setReviewDecision = useCallback(
    (changeId: string, decision: ChangeDecision, comment?: string) => {
      setReviews((prev) => {
        const next = new Map(prev);
        next.set(changeId, {
          change_id: changeId,
          decision,
          comment: comment ?? null,
        });
        return next;
      });
    },
    [],
  );

  const submitReviews = useCallback(
    async (action: ReviewAction) => {
      if (!sessionId || reviews.size === 0) return;

      setPhase('applying');
      setError(null);

      try {
        const reviewList = Array.from(reviews.values());
        const res = await fetch(`${API_URL}/api/v1/pipeline/sessions/${sessionId}/review`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reviews: reviewList, action }),
        });

        if (!res.ok) {
          const detail = await res.text();
          throw new Error(`Review submission failed (${res.status}): ${detail}`);
        }

        const data: ReviewResponse = await res.json();

        // If a new version was produced, notify the parent
        if (data.new_version && onVersionUpdateRef.current) {
          setFeedbackVersion(data.new_version);

          // Fetch the updated state to get the new markdown
          const stateRes = await fetch(
            `${API_URL}/api/v1/pipeline/sessions/${sessionId}/state`,
          );
          if (stateRes.ok) {
            const state = await stateRes.json();
            onVersionUpdateRef.current(data.new_version, state.latest_markdown);
          }
        }

        if (data.finalized) {
          setPhase('finalized');
        } else {
          // Back to collecting for another round
          setPendingItems([]);
          setCandidates([]);
          setReviews(new Map());
          setPhase('collecting');
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
        setPhase('reviewing');
      }
    },
    [sessionId, reviews],
  );

  const approveSession = useCallback(async () => {
    if (!sessionId) return;

    setError(null);

    try {
      const res = await fetch(`${API_URL}/api/v1/pipeline/sessions/${sessionId}/approve`, {
        method: 'POST',
      });

      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`Approval failed (${res.status}): ${detail}`);
      }

      await res.json();
      setPhase('finalized');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, [sessionId]);

  return {
    phase,
    pendingItems,
    candidates,
    reviews,
    revisionRound,
    error,
    feedbackVersion,
    enterFeedbackMode,
    exitFeedbackMode,
    backToCollecting,
    addFeedbackItem,
    removeFeedbackItem,
    submitFeedback,
    setReviewDecision,
    submitReviews,
    approveSession,
  };
}
