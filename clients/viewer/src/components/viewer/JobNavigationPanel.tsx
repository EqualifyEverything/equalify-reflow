import { cn } from '@/lib/utils';
import type { DocumentPlan, JobStatus, JobStatusType, VerificationSummary } from '@/hooks/useStream';
import {
  FileText,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  AlertTriangle,
  ChevronRight,
  BookOpen,
  Layers,
  Shield,
} from 'lucide-react';

interface JobNavigationPanelProps {
  documentPlan: DocumentPlan | null;
  jobs: JobStatus[];
  verification: VerificationSummary | null;
  currentPage: number | null;
  onJobClick: (jobId: string, page: number) => void;
  className?: string;
}

const statusConfig: Record<JobStatusType, { icon: typeof CheckCircle2; color: string; bgColor: string }> = {
  pending: { icon: Clock, color: 'text-slate-400', bgColor: 'bg-slate-100' },
  running: { icon: Loader2, color: 'text-amber-500', bgColor: 'bg-amber-50' },
  completed: { icon: CheckCircle2, color: 'text-green-500', bgColor: 'bg-green-50' },
  failed: { icon: XCircle, color: 'text-red-500', bgColor: 'bg-red-50' },
};

export function JobNavigationPanel({
  documentPlan,
  jobs,
  verification,
  currentPage,
  onJobClick,
  className,
}: JobNavigationPanelProps) {
  // Calculate job status counts
  const statusCounts = jobs.reduce(
    (acc, job) => {
      acc[job.status]++;
      return acc;
    },
    { pending: 0, running: 0, completed: 0, failed: 0 } as Record<JobStatusType, number>
  );

  // Group jobs by page
  const jobsByPage = jobs.reduce(
    (acc, job) => {
      if (!acc[job.page]) {
        acc[job.page] = [];
      }
      acc[job.page].push(job);
      return acc;
    },
    {} as Record<number, JobStatus[]>
  );

  const pages = Object.keys(jobsByPage)
    .map(Number)
    .sort((a, b) => a - b);

  return (
    <div className={cn('flex flex-col h-full bg-white', className)}>
      {/* Document Plan Header */}
      <div className="px-4 py-3 bg-gradient-to-r from-uic-blue to-uic-blue/90 text-white">
        <div className="flex items-center gap-2 mb-2">
          <BookOpen className="w-5 h-5" />
          <span className="font-semibold">Document Plan</span>
        </div>
        {documentPlan ? (
          <div className="space-y-1">
            <p className="text-sm font-medium truncate" title={documentPlan.title}>
              {documentPlan.title || 'Analyzing...'}
            </p>
            <div className="flex items-center gap-3 text-xs text-white/70">
              <span className="capitalize">{documentPlan.documentType}</span>
              <span>|</span>
              <span>{documentPlan.totalPages} pages</span>
              <span>|</span>
              <span>{documentPlan.totalJobs} jobs</span>
            </div>
          </div>
        ) : (
          <p className="text-sm text-white/70">Waiting for document...</p>
        )}
      </div>

      {/* Status Summary Bar */}
      {jobs.length > 0 && (
        <div className="px-4 py-2 bg-slate-50 border-b flex items-center gap-4">
          <div className="flex items-center gap-4 text-xs">
            {statusCounts.completed > 0 && (
              <div className="flex items-center gap-1 text-green-600">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>{statusCounts.completed}</span>
              </div>
            )}
            {statusCounts.running > 0 && (
              <div className="flex items-center gap-1 text-amber-600">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>{statusCounts.running}</span>
              </div>
            )}
            {statusCounts.pending > 0 && (
              <div className="flex items-center gap-1 text-slate-400">
                <Clock className="w-3.5 h-3.5" />
                <span>{statusCounts.pending}</span>
              </div>
            )}
            {statusCounts.failed > 0 && (
              <div className="flex items-center gap-1 text-red-600">
                <XCircle className="w-3.5 h-3.5" />
                <span>{statusCounts.failed}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Verification Summary (if available) */}
      {verification && (
        <div
          className={cn(
            'px-4 py-2 border-b flex items-center gap-2 text-sm',
            verification.passed ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
          )}
        >
          <Shield className="w-4 h-4" />
          <span className="font-medium">Verification:</span>
          {verification.passed ? (
            <span>Passed ({verification.pagesPassed}/{verification.pagesPassed + verification.pagesFailed})</span>
          ) : (
            <span>
              {verification.totalIssues} issues, {verification.criticalIssues.length} critical
            </span>
          )}
        </div>
      )}

      {/* Job List by Page */}
      <div className="flex-1 overflow-y-auto">
        {pages.length === 0 ? (
          <div className="p-4 text-center text-slate-400 text-sm">
            <Layers className="w-8 h-8 mx-auto mb-2 opacity-50" />
            <p>No jobs created yet</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {pages.map((pageNum) => {
              const pageJobs = jobsByPage[pageNum];
              const isCurrentPage = currentPage === pageNum;
              const pageVerification = verification?.pageResults.find((p) => p.pageNum === pageNum);

              return (
                <div key={pageNum} className={cn(isCurrentPage && 'bg-amber-50/50')}>
                  {/* Page Header */}
                  <div
                    className={cn(
                      'px-4 py-2 flex items-center gap-2 text-sm font-medium',
                      isCurrentPage ? 'text-amber-700 bg-amber-100/50' : 'text-slate-600 bg-slate-50'
                    )}
                  >
                    <FileText className="w-4 h-4" />
                    <span>Page {pageNum}</span>
                    {isCurrentPage && (
                      <span className="ml-auto text-xs bg-amber-200 text-amber-800 px-1.5 py-0.5 rounded">
                        Processing
                      </span>
                    )}
                    {pageVerification && (
                      <span
                        className={cn(
                          'ml-auto text-xs px-1.5 py-0.5 rounded',
                          pageVerification.passed
                            ? 'bg-green-100 text-green-700'
                            : 'bg-red-100 text-red-700'
                        )}
                      >
                        {pageVerification.passed ? 'Verified' : `${pageVerification.issues.length} issues`}
                      </span>
                    )}
                  </div>

                  {/* Jobs for this page */}
                  <div className="pl-6">
                    {pageJobs.map((job) => {
                      const config = statusConfig[job.status];
                      const Icon = config.icon;

                      return (
                        <button
                          key={job.jobId}
                          onClick={() => onJobClick(job.jobId, job.page)}
                          className={cn(
                            'w-full px-3 py-2 flex items-center gap-2 text-left text-sm',
                            'hover:bg-slate-50 transition-colors group',
                            job.status === 'running' && 'bg-amber-50/30'
                          )}
                        >
                          <Icon
                            className={cn(
                              'w-4 h-4 flex-shrink-0',
                              config.color,
                              job.status === 'running' && 'animate-spin'
                            )}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-slate-700 capitalize">
                                {job.jobType}
                              </span>
                              {job.editsMade > 0 && (
                                <span className="text-xs text-green-600">
                                  {job.editsMade} edit{job.editsMade > 1 ? 's' : ''}
                                </span>
                              )}
                            </div>
                            {job.tasks.length > 0 && (
                              <p className="text-xs text-slate-400 truncate">
                                {job.tasks.slice(0, 3).join(', ')}
                                {job.tasks.length > 3 && ` +${job.tasks.length - 3} more`}
                              </p>
                            )}
                          </div>
                          <ChevronRight className="w-4 h-4 text-slate-300 group-hover:text-slate-500 flex-shrink-0" />
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Verification Issues (if failed) */}
      {verification && !verification.passed && verification.criticalIssues.length > 0 && (
        <div className="border-t bg-red-50 px-4 py-3 max-h-32 overflow-y-auto">
          <div className="flex items-center gap-2 text-red-700 text-sm font-medium mb-2">
            <AlertTriangle className="w-4 h-4" />
            <span>Critical Issues</span>
          </div>
          <ul className="space-y-1">
            {verification.criticalIssues.slice(0, 5).map((issue, idx) => (
              <li key={idx} className="text-xs text-red-600 truncate" title={issue}>
                {issue}
              </li>
            ))}
            {verification.criticalIssues.length > 5 && (
              <li className="text-xs text-red-500">
                +{verification.criticalIssues.length - 5} more issues
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
