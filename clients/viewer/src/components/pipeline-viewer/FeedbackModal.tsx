import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { X, Loader2, CheckCircle2 } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL ?? '';

const CATEGORIES = [
  { value: 'content', label: 'Content — incorrect text, missing content, OCR errors' },
  { value: 'formatting', label: 'Formatting — layout issues, broken tables, misplaced images' },
  { value: 'accessibility', label: 'Accessibility — missing alt text, heading levels, labels' },
  { value: 'structure', label: 'Structure — reading order, misplaced sections, broken lists' },
  { value: 'other', label: 'Other' },
] as const;

type Category = (typeof CATEGORIES)[number]['value'];

interface FeedbackModalProps {
  onClose: () => void;
  sessionId: string | null;
  documentTitle: string | null;
  currentPage: number | null;
  currentStage: string | null;
}

export function FeedbackModal({
  onClose,
  sessionId,
  documentTitle,
  currentPage,
  currentStage,
}: FeedbackModalProps) {
  const [category, setCategory] = useState<Category>('content');
  const [description, setDescription] = useState('');
  const [page, setPage] = useState<string>(currentPage ? String(currentPage) : '');
  const [website, setWebsite] = useState(''); // honeypot
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const successRef = useRef<HTMLDivElement>(null);

  // When submission succeeds, move focus to the confirmation so keyboard
  // users land somewhere meaningful, and rely on role="status" +
  // aria-live="polite" to announce it to screen readers. Then auto-close
  // after enough time for assistive tech to finish reading — Modal's
  // unmount hook restores focus back to the Feedback button.
  useEffect(() => {
    if (!success) return;
    successRef.current?.focus();
    const t = setTimeout(onClose, 2500);
    return () => clearTimeout(t);
  }, [success, onClose]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (description.trim().length < 10) {
      setError('Please describe the issue in at least 10 characters.');
      return;
    }
    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch(`${API_URL}/api/v1/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category,
          description: description.trim(),
          session_id: sessionId,
          document_title: documentTitle,
          page: page ? parseInt(page, 10) : null,
          section: currentStage,
          website,
        }),
      });

      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`Submission failed (${res.status}): ${detail}`);
      }

      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal onClose={onClose} aria-label="Report an issue" className="bg-white rounded-lg shadow-xl w-full max-w-lg mx-4">
      <div className="flex items-center justify-between px-5 py-3 border-b">
        <h2 className="text-base font-semibold text-gray-900">Report an issue</h2>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-gray-100"
          aria-label="Close dialog"
        >
          <X className="w-4 h-4 text-muted-foreground" />
        </button>
      </div>

      {success ? (
        <div
          ref={successRef}
          role="status"
          aria-live="polite"
          tabIndex={-1}
          className="flex flex-col items-center gap-3 px-5 py-10 text-center outline-none focus:ring-2 focus:ring-uic-blue rounded-md"
        >
          <CheckCircle2 className="w-10 h-10 text-green-600" aria-hidden="true" />
          <p className="text-sm font-medium text-gray-900">Thanks — feedback submitted.</p>
          <p className="text-xs text-muted-foreground">
            Reports like this help us prioritize pipeline improvements. This dialog will close shortly.
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          <div>
            <label htmlFor="feedback-category" className="block text-xs font-medium text-gray-700 mb-1">
              Category
            </label>
            <select
              id="feedback-category"
              value={category}
              onChange={(e) => setCategory(e.target.value as Category)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              disabled={submitting}
            >
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="feedback-description" className="block text-xs font-medium text-gray-700 mb-1">
              What went wrong?
            </label>
            <textarea
              id="feedback-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={5}
              minLength={10}
              maxLength={5000}
              required
              disabled={submitting}
              placeholder="Describe the issue — be as specific as you can. The more detail the better."
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-y"
            />
            <p className="text-[11px] text-muted-foreground mt-1">
              {description.length}/5000 characters · minimum 10
            </p>
          </div>

          <div>
            <label htmlFor="feedback-page" className="block text-xs font-medium text-gray-700 mb-1">
              Page (optional)
            </label>
            <Input
              id="feedback-page"
              type="number"
              min={1}
              value={page}
              onChange={(e) => setPage(e.target.value)}
              disabled={submitting}
              className="w-32"
            />
          </div>

          {/* Honeypot — hidden from real users, bots will fill it. */}
          <div aria-hidden="true" className="hidden">
            <label htmlFor="feedback-website">Website</label>
            <input
              id="feedback-website"
              type="text"
              tabIndex={-1}
              autoComplete="off"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
            />
          </div>

          {error && (
            <div role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
              {error}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2 border-t">
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || description.trim().length < 10}>
              {submitting ? (
                <>
                  <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
                  Sending...
                </>
              ) : (
                'Submit feedback'
              )}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
