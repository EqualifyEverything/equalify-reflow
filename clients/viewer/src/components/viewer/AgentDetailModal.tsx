import { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import type { AgentThinkingData } from '@/types/events';
import { X, Wrench, Brain, ArrowRight } from 'lucide-react';

interface AgentDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  jobId: string;
  page: number;
  events: AgentThinkingData[];
}

function AgentEventItem({ event }: { event: AgentThinkingData }) {
  const { tool_name, tool_args, tool_result_preview, message_type, content_preview } = event;

  if (tool_name) {
    // Tool call event
    return (
      <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
        {/* Tool header */}
        <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-50 to-purple-100 border-b border-slate-200">
          <Wrench className="w-4 h-4 text-purple-600" />
          <span className="font-mono font-semibold text-purple-700">{tool_name}()</span>
        </div>

        {/* Tool args */}
        {tool_args && Object.keys(tool_args).length > 0 && (
          <div className="px-4 py-3 border-b border-slate-100 bg-slate-50">
            <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
              Arguments
            </div>
            <div className="space-y-1">
              {Object.entries(tool_args)
                .slice(0, 5)
                .map(([key, value]) => (
                  <div key={key} className="flex items-start gap-2 text-sm">
                    <span className="text-slate-500 font-mono">{key}:</span>
                    <span className="text-slate-700 break-all">
                      {typeof value === 'string' ? value.slice(0, 80) : JSON.stringify(value).slice(0, 80)}
                      {(typeof value === 'string' ? value.length : JSON.stringify(value).length) > 80 && '...'}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Tool result */}
        {tool_result_preview && (
          <div className="px-4 py-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-green-600 uppercase tracking-wide mb-2">
              <ArrowRight className="w-3 h-3" />
              Result
            </div>
            <div className="text-sm text-green-700 bg-green-50 rounded px-3 py-2 font-mono break-all">
              {tool_result_preview.slice(0, 100)}
              {tool_result_preview.length > 100 && '...'}
            </div>
          </div>
        )}
      </div>
    );
  }

  // Thinking/message event
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-50 to-blue-100 border-b border-slate-200">
        <Brain className="w-4 h-4 text-uic-blue" />
        <span className="font-semibold text-uic-blue">{message_type || 'thinking'}</span>
      </div>
      {content_preview && (
        <div className="px-4 py-3">
          <p className="text-sm text-slate-600 whitespace-pre-wrap break-words">
            {content_preview.slice(0, 200)}
            {content_preview.length > 200 && '...'}
          </p>
        </div>
      )}
    </div>
  );
}

export function AgentDetailModal({ isOpen, onClose, jobId, page, events }: AgentDetailModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Focus trap and keyboard handling
  useEffect(() => {
    if (!isOpen) return;

    // Focus the close button when modal opens
    setTimeout(() => closeButtonRef.current?.focus(), 0);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }

      if (e.key === 'Tab') {
        const modal = modalRef.current;
        if (!modal) return;

        const focusableElements = modal.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        const firstElement = focusableElements[0] as HTMLElement;
        const lastElement = focusableElements[focusableElements.length - 1] as HTMLElement;

        if (e.shiftKey && document.activeElement === firstElement) {
          e.preventDefault();
          lastElement?.focus();
        } else if (!e.shiftKey && document.activeElement === lastElement) {
          e.preventDefault();
          firstElement?.focus();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 bg-black/50 z-50"
            onClick={onClose}
            aria-hidden="true"
          />

          {/* Modal */}
          <motion.div
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="agent-modal-title"
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className={cn(
              'fixed inset-4 md:inset-[10%] z-50',
              'bg-slate-100 rounded-xl shadow-2xl overflow-hidden',
              'flex flex-col'
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 bg-uic-blue text-white">
              <div>
                <h2 id="agent-modal-title" className="text-xl font-bold">Agent Activity</h2>
                <p className="text-sm text-white/80">
                  Page {page} • Job {jobId.slice(0, 8)}...
                </p>
              </div>
              <Button
                ref={closeButtonRef}
                variant="ghost"
                size="icon"
                onClick={onClose}
                aria-label="Close dialog"
                className="text-white hover:bg-white/10"
              >
                <X className="w-5 h-5" />
              </Button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-6">
              {events.length === 0 ? (
                <div className="flex items-center justify-center h-full text-slate-500">
                  <div className="text-center">
                    <Brain className="w-12 h-12 mx-auto mb-4 opacity-30" />
                    <p>No agent activity recorded for this job</p>
                  </div>
                </div>
              ) : (
                <div className="space-y-4 max-w-3xl mx-auto">
                  {events.map((event, index) => (
                    <motion.div
                      key={index}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.05 }}
                    >
                      <AgentEventItem event={event} />
                    </motion.div>
                  ))}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-6 py-4 bg-white border-t border-slate-200 flex items-center justify-between">
              <span className="text-sm text-slate-500">{events.length} events</span>
              <Button onClick={onClose} className="bg-uic-blue hover:bg-uic-blue/90">
                Close
              </Button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
