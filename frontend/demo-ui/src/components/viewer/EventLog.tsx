import { useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import type { StreamEvent, JobStartedData, JobCompletedData } from '@/types/events';
import {
  CheckCircle,
  XCircle,
  Clock,
  Play,
  FileText,
  Building2,
  Pencil,
  Wrench,
  PartyPopper,
  Brain,
  AlertCircle,
} from 'lucide-react';

interface EventLogProps {
  events: StreamEvent[];
  onJobClick?: (jobId: string, page: number) => void;
  className?: string;
}

const EVENT_ICONS: Record<string, React.ReactNode> = {
  check: <CheckCircle className="w-4 h-4" />,
  x: <XCircle className="w-4 h-4" />,
  clock: <Clock className="w-4 h-4" />,
  play: <Play className="w-4 h-4" />,
  page: <FileText className="w-4 h-4" />,
  structure: <Building2 className="w-4 h-4" />,
  pencil: <Pencil className="w-4 h-4" />,
  wrench: <Wrench className="w-4 h-4" />,
  party: <PartyPopper className="w-4 h-4" />,
  brain: <Brain className="w-4 h-4" />,
  error: <AlertCircle className="w-4 h-4" />,
};

function formatTime(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

interface EventItemProps {
  event: StreamEvent;
  onJobClick?: (jobId: string, page: number) => void;
}

function EventItem({ event, onJobClick }: EventItemProps) {
  const { type, data, timestamp } = event;

  let icon: React.ReactNode = null;
  let content: React.ReactNode = null;
  let variant: 'default' | 'success' | 'warning' | 'error' | 'info' | 'clickable' = 'default';
  let jobId: string | null = null;
  let page: number | null = null;

  switch (type) {
    case 'docling:started':
      icon = EVENT_ICONS.clock;
      content = 'Converting PDF...';
      variant = 'warning';
      break;

    case 'docling:complete': {
      const d = data as { pages_extracted: number };
      icon = EVENT_ICONS.check;
      content = `${d.pages_extracted} pages extracted`;
      variant = 'success';
      break;
    }

    case 'planning:started':
      icon = EVENT_ICONS.clock;
      content = 'Planning document...';
      variant = 'warning';
      break;

    case 'planning:structure': {
      const d = data as { document_title: string; document_type: string };
      icon = EVENT_ICONS.structure;
      content = (
        <>
          <span className="font-semibold">{d.document_title?.slice(0, 40)}</span>
          <span className="text-muted-foreground ml-1">({d.document_type})</span>
        </>
      );
      variant = 'info';
      break;
    }

    case 'planning:page_summarized': {
      const d = data as { page_num: number; summary: string };
      icon = EVENT_ICONS.page;
      content = (
        <>
          <span className="text-uic-blue font-medium">Page {d.page_num}:</span>{' '}
          <span className="text-muted-foreground">{d.summary}</span>
        </>
      );
      break;
    }

    case 'planning:complete': {
      const d = data as { total_jobs: number };
      icon = EVENT_ICONS.check;
      content = `Planning complete: ${d.total_jobs} jobs`;
      variant = 'success';
      break;
    }

    case 'job:started': {
      const d = data as JobStartedData;
      icon = EVENT_ICONS.play;
      jobId = d.job_id;
      page = d.page;
      content = (
        <>
          <span className="font-semibold">Processing page {d.page}</span>
          <span className="text-xs text-muted-foreground ml-2 italic">click for details</span>
        </>
      );
      variant = 'clickable';
      break;
    }

    case 'job:completed': {
      const d = data as JobCompletedData;
      icon = EVENT_ICONS.check;
      jobId = d.job_id;
      page = d.page;
      content = (
        <>
          <span>Page {d.page} done</span>
          <span className="text-muted-foreground ml-1">({d.edits_made} edits)</span>
        </>
      );
      variant = 'clickable';
      break;
    }

    case 'edit:committed': {
      const d = data as { ledger_entry: { target: string; action: string } };
      icon = EVENT_ICONS.pencil;
      content = (
        <>
          <span className="font-medium">{d.ledger_entry.target}</span>
          <span className="text-muted-foreground ml-1">[{d.ledger_entry.action}]</span>
        </>
      );
      variant = 'info';
      break;
    }

    case 'edit:validated': {
      const d = data as { target: string; approved: boolean };
      icon = d.approved ? EVENT_ICONS.check : EVENT_ICONS.x;
      content = (
        <>
          {d.approved ? 'Approved: ' : 'Rejected: '}
          <span className={d.approved ? 'text-green-600' : 'text-uic-red'}>{d.target}</span>
        </>
      );
      variant = d.approved ? 'success' : 'error';
      break;
    }

    case 'verification:started':
      icon = EVENT_ICONS.clock;
      content = 'Verifying output...';
      variant = 'warning';
      break;

    case 'verification:page_verified': {
      const d = data as { page_num: number; passed: boolean; confidence: number };
      icon = d.passed ? EVENT_ICONS.check : EVENT_ICONS.x;
      content = (
        <>
          <span>Page {d.page_num}:</span>
          <span className="ml-1">{Math.round(d.confidence * 100)}% confidence</span>
        </>
      );
      variant = d.passed ? 'success' : 'error';
      break;
    }

    case 'verification:complete': {
      const d = data as { passed: boolean; pages_passed: number };
      icon = d.passed ? EVENT_ICONS.check : EVENT_ICONS.x;
      content = `Verification: ${d.pages_passed} passed`;
      variant = d.passed ? 'success' : 'warning';
      break;
    }

    case 'final_pass:started': {
      const d = data as { page_separators_found: number };
      icon = EVENT_ICONS.clock;
      content = `Optimizing for reading (${d.page_separators_found} page breaks)...`;
      variant = 'warning';
      break;
    }

    case 'final_pass:complete': {
      const d = data as { changes_made: number; success: boolean };
      icon = d.success ? EVENT_ICONS.check : EVENT_ICONS.x;
      content = `Optimized: ${d.changes_made} changes`;
      variant = d.success ? 'success' : 'warning';
      break;
    }

    case 'recovery:started': {
      const d = data as { pages_to_recover: number[] };
      icon = EVENT_ICONS.wrench;
      content = `Recovering ${d.pages_to_recover?.length || 0} pages...`;
      variant = 'warning';
      break;
    }

    case 'recovery:complete': {
      const d = data as { pages_recovered: number[] };
      icon = EVENT_ICONS.check;
      content = `Recovered ${d.pages_recovered?.length || 0} pages`;
      variant = 'success';
      break;
    }

    case 'processing:complete': {
      const d = data as { success: boolean; total_edits: number; total_cost: number; total_duration_ms: number };
      icon = d.success ? EVENT_ICONS.party : EVENT_ICONS.x;
      const duration = (d.total_duration_ms / 1000).toFixed(1);
      content = (
        <>
          <span className="font-bold">{d.success ? 'SUCCESS' : 'FAILED'}</span>
          <span className="ml-2 text-muted-foreground">
            {d.total_edits} edits | ${d.total_cost.toFixed(3)} | {duration}s
          </span>
        </>
      );
      variant = d.success ? 'success' : 'error';
      break;
    }

    case 'processing:error': {
      const d = data as { error: string };
      icon = EVENT_ICONS.error;
      content = `Error: ${d.error}`;
      variant = 'error';
      break;
    }

    case 'agent:thinking':
      // Skip agent:thinking events in the main log - they're shown in the modal
      return null;

    default:
      icon = EVENT_ICONS.brain;
      content = type;
      break;
  }

  const variantStyles = {
    default: 'text-foreground',
    success: 'text-green-600',
    warning: 'text-amber-600',
    error: 'text-uic-red',
    info: 'text-uic-blue',
    clickable: 'text-amber-600 hover:bg-uic-blue/5 cursor-pointer',
  };

  const isClickable = variant === 'clickable' && jobId && onJobClick;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        'flex items-start gap-3 py-2 px-3 rounded-lg transition-colors',
        variantStyles[variant],
        isClickable && 'border border-transparent hover:border-uic-blue/20'
      )}
      onClick={isClickable ? () => onJobClick(jobId!, page!) : undefined}
    >
      <span className="text-xs text-muted-foreground font-mono whitespace-nowrap mt-0.5">
        {formatTime(timestamp)}
      </span>
      <span className={cn('flex-shrink-0 mt-0.5', variantStyles[variant])}>{icon}</span>
      <span className="flex-1 text-sm leading-relaxed break-words">{content}</span>
    </motion.div>
  );
}

export function EventLog({ events, onJobClick, className }: EventLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  // Filter out agent:thinking events
  const visibleEvents = events.filter((e) => e.type !== 'agent:thinking');

  return (
    <div className={cn('flex flex-col h-full', className)}>
      <div className="flex-1 overflow-y-auto space-y-1 p-2">
        <AnimatePresence mode="popLayout">
          {visibleEvents.map((event) => (
            <EventItem key={event.id} event={event} onJobClick={onJobClick} />
          ))}
        </AnimatePresence>
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
