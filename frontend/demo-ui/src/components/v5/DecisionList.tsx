import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import type { Decision } from '@/types/v5-events';
import { CheckCircle, XCircle, ChevronRight, X } from 'lucide-react';

interface DecisionListProps {
  decisions: Decision[];
  className?: string;
}

interface DecisionDetailModalProps {
  decision: Decision | null;
  onClose: () => void;
}

function DecisionDetailModal({ decision, onClose }: DecisionDetailModalProps) {
  if (!decision) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 bg-black/50 z-50"
        onClick={onClose}
      />
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        className="fixed inset-4 md:inset-[15%] z-50 bg-white rounded-xl shadow-2xl overflow-hidden flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-uic-blue text-white">
          <div>
            <h2 className="text-lg font-bold">Decision Details</h2>
            <p className="text-sm text-white/80">{decision.target}</p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} className="text-white hover:bg-white/10">
            <X className="w-5 h-5" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Status */}
          <div
            className={cn(
              'flex items-center gap-2 text-lg font-semibold',
              decision.approved ? 'text-green-600' : 'text-uic-red'
            )}
          >
            {decision.approved ? (
              <CheckCircle className="w-6 h-6" />
            ) : (
              <XCircle className="w-6 h-6" />
            )}
            {decision.approved ? 'Approved' : 'Rejected'}
          </div>

          {/* Target Info */}
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">Target</h3>
            <p className="text-slate-700">
              Page {decision.page} • {decision.target} • {decision.action}
            </p>
          </div>

          {/* Before */}
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">Before</h3>
            <pre className="bg-red-50 text-red-700 p-4 rounded-lg text-sm font-mono whitespace-pre-wrap break-words border border-red-200">
              {decision.before || '(empty)'}
            </pre>
          </div>

          {/* After */}
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">After</h3>
            <pre className="bg-green-50 text-green-700 p-4 rounded-lg text-sm font-mono whitespace-pre-wrap break-words border border-green-200">
              {decision.after || '(empty)'}
            </pre>
          </div>

          {/* Reasoning */}
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">Reasoning</h3>
            <p className="text-slate-700 italic bg-slate-50 p-4 rounded-lg">
              {decision.reasoning || 'No reasoning provided'}
            </p>
          </div>

          {/* Feedback (if rejected) */}
          {!decision.approved && decision.feedback && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-uic-red uppercase tracking-wide">
                Rejection Reason
              </h3>
              <p className="text-uic-red bg-red-50 p-4 rounded-lg">{decision.feedback}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-slate-50 border-t">
          <Button onClick={onClose} className="w-full bg-uic-blue hover:bg-uic-blue/90">
            Close
          </Button>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}

function DecisionItem({
  decision,
  onClick,
}: {
  decision: Decision;
  onClick: () => void;
}) {
  return (
    <motion.button
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      whileHover={{ x: 4 }}
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors',
        'hover:bg-slate-100 group'
      )}
    >
      {decision.approved ? (
        <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
      ) : (
        <XCircle className="w-4 h-4 text-uic-red flex-shrink-0" />
      )}
      <span
        className={cn(
          'flex-1 text-sm truncate',
          decision.approved ? 'text-slate-700' : 'text-uic-red'
        )}
      >
        {decision.target}
      </span>
      <ChevronRight className="w-4 h-4 text-slate-400 group-hover:text-uic-blue flex-shrink-0" />
    </motion.button>
  );
}

export function DecisionList({ decisions, className }: DecisionListProps) {
  const [selectedDecision, setSelectedDecision] = useState<Decision | null>(null);

  return (
    <>
      <div className={cn('flex flex-col h-full', className)}>
        {decisions.length === 0 ? (
          <div className="flex items-center justify-center h-full p-4 text-slate-500 text-sm">
            No decisions yet
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            <AnimatePresence mode="popLayout">
              {decisions.map((decision) => (
                <DecisionItem
                  key={decision.id}
                  decision={decision}
                  onClick={() => setSelectedDecision(decision)}
                />
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>

      {selectedDecision && (
        <DecisionDetailModal
          decision={selectedDecision}
          onClose={() => setSelectedDecision(null)}
        />
      )}
    </>
  );
}
