import { useState, useCallback, useRef, useEffect } from 'react';
import type {
  V5Event,
  V5EventType,
  V5EventData,
  ConnectionStatus,
  Decision,
  AgentThinkingData,
} from '@/types/v5-events';

interface UseV5StreamOptions {
  apiUrl: string;
  apiKey: string;
  jobId: string | null;
  onEvent?: (event: V5Event) => void;
}

// Document plan information
export interface DocumentPlan {
  title: string;
  documentType: string;
  totalPages: number;
  totalJobs: number;
  pageSummaries: Map<number, string>;
}

// Job status tracking
export type JobStatusType = 'pending' | 'running' | 'completed' | 'failed';

export interface JobStatus {
  jobId: string;
  page: number;
  jobType: string;
  status: JobStatusType;
  editsMade: number;
  tasks: string[];
}

// Page verification result
export interface PageVerification {
  pageNum: number;
  passed: boolean;
  issues: string[];
}

// Verification summary
export interface VerificationSummary {
  passed: boolean;
  pagesPassed: number;
  pagesFailed: number;
  totalIssues: number;
  criticalIssues: string[];
  pageResults: PageVerification[];
}

interface UseV5StreamReturn {
  events: V5Event[];
  decisions: Decision[];
  agentActivity: Map<string, AgentThinkingData[]>;
  pageMarkdowns: Map<number, string>;
  finalMarkdown: string;
  status: ConnectionStatus;
  statusMessage: string;
  currentPage: number | null;
  totalCost: number;
  totalEdits: number;
  // New: Document plan and job tracking
  documentPlan: DocumentPlan | null;
  jobs: JobStatus[];
  verification: VerificationSummary | null;
  connect: () => void;
  disconnect: () => void;
  clearEvents: () => void;
  fetchMarkdown: () => Promise<void>;
}

let eventCounter = 0;

export function useV5Stream({
  apiUrl,
  apiKey,
  jobId,
  onEvent,
}: UseV5StreamOptions): UseV5StreamReturn {
  const [events, setEvents] = useState<V5Event[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [agentActivity, setAgentActivity] = useState<Map<string, AgentThinkingData[]>>(new Map());
  const [pageMarkdowns, setPageMarkdowns] = useState<Map<number, string>>(new Map());
  const [finalMarkdown, setFinalMarkdown] = useState<string>('');
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const [statusMessage, setStatusMessage] = useState('Ready');
  const [currentPage, setCurrentPage] = useState<number | null>(null);
  const [totalCost, setTotalCost] = useState(0);
  const [totalEdits, setTotalEdits] = useState(0);

  // New: Document plan, jobs, and verification tracking
  const [documentPlan, setDocumentPlan] = useState<DocumentPlan | null>(null);
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [verification, setVerification] = useState<VerificationSummary | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const currentJobIdRef = useRef<string | null>(null);

  // Fetch final markdown from API
  const fetchMarkdown = useCallback(async () => {
    if (!jobId || !apiKey) return;

    try {
      const response = await fetch(`${apiUrl}/api/v5/jobs/${jobId}/markdown`, {
        headers: {
          'X-API-Key': apiKey,
        },
      });

      if (response.ok) {
        const markdown = await response.text();
        setFinalMarkdown(markdown);
      } else {
        console.error('Failed to fetch markdown:', response.statusText);
      }
    } catch (error) {
      console.error('Error fetching markdown:', error);
    }
  }, [jobId, apiKey, apiUrl]);

  const addEvent = useCallback(
    (type: V5EventType, data: V5EventData) => {
      const event: V5Event = {
        type,
        data,
        timestamp: new Date(),
        id: `event-${++eventCounter}`,
      };

      setEvents((prev) => [...prev, event]);
      onEvent?.(event);

      // Handle specific event types
      switch (type) {
        // Planning phase events
        case 'planning:structure': {
          const structureData = data as {
            document_title: string;
            document_type: string;
          };
          setDocumentPlan((prev) => ({
            title: structureData.document_title,
            documentType: structureData.document_type,
            totalPages: prev?.totalPages || 0,
            totalJobs: prev?.totalJobs || 0,
            pageSummaries: prev?.pageSummaries || new Map(),
          }));
          break;
        }

        case 'planning:page_summarized': {
          const pageData = data as { page_num: number; summary: string };
          setDocumentPlan((prev) => {
            if (!prev) return null;
            const summaries = new Map(prev.pageSummaries);
            summaries.set(pageData.page_num, pageData.summary);
            return {
              ...prev,
              totalPages: Math.max(prev.totalPages, pageData.page_num),
              pageSummaries: summaries,
            };
          });
          break;
        }

        case 'planning:complete': {
          const planData = data as { total_jobs: number; total_pages?: number };
          setDocumentPlan((prev) => ({
            title: prev?.title || 'Unknown',
            documentType: prev?.documentType || 'document',
            totalPages: planData.total_pages || prev?.totalPages || 0,
            totalJobs: planData.total_jobs,
            pageSummaries: prev?.pageSummaries || new Map(),
          }));
          break;
        }

        // Job lifecycle events
        case 'job:created': {
          const jobData = data as {
            job_id: string;
            page: number;
            job_type: string;
            tasks: string[];
          };
          setJobs((prev) => [
            ...prev,
            {
              jobId: jobData.job_id,
              page: jobData.page,
              jobType: jobData.job_type || 'content',
              status: 'pending',
              editsMade: 0,
              tasks: jobData.tasks || [],
            },
          ]);
          break;
        }

        case 'job:started': {
          const jobData = data as { job_id: string; page: number };
          currentJobIdRef.current = jobData.job_id;
          setCurrentPage(jobData.page);
          setAgentActivity((prev) => {
            const next = new Map(prev);
            next.set(jobData.job_id, []);
            return next;
          });
          // Update job status to running
          setJobs((prev) =>
            prev.map((job) =>
              job.jobId === jobData.job_id ? { ...job, status: 'running' as JobStatusType } : job
            )
          );
          break;
        }

        case 'job:completed': {
          const jobData = data as { job_id: string; page: number; edits_made: number };
          // Update job status to completed
          setJobs((prev) =>
            prev.map((job) =>
              job.jobId === jobData.job_id
                ? { ...job, status: 'completed' as JobStatusType, editsMade: jobData.edits_made }
                : job
            )
          );
          break;
        }

        case 'agent:thinking': {
          const thinkingData = data as AgentThinkingData;
          const jobId = thinkingData.job_id || currentJobIdRef.current;
          if (jobId) {
            setAgentActivity((prev) => {
              const next = new Map(prev);
              const existing = next.get(jobId) || [];
              next.set(jobId, [...existing, thinkingData]);
              return next;
            });
          }
          break;
        }

        case 'edit:committed': {
          const editData = data as {
            ledger_entry: {
              target: string;
              action: string;
              page: number;
              before: string;
              after: string;
              reasoning: string;
            };
          };
          const entry = editData.ledger_entry;

          const decision: Decision = {
            id: `decision-${++eventCounter}`,
            approved: true,
            target: entry.target,
            page: entry.page,
            action: entry.action,
            before: entry.before,
            after: entry.after,
            reasoning: entry.reasoning,
          };
          setDecisions((prev) => [...prev, decision]);

          // Update page markdown
          setPageMarkdowns((prev) => {
            const next = new Map(prev);
            const current = next.get(entry.page) || '';
            if (entry.before && current.includes(entry.before)) {
              next.set(entry.page, current.replace(entry.before, entry.after));
            }
            return next;
          });

          setTotalEdits((prev) => prev + 1);
          break;
        }

        // Verification phase events
        case 'verification:page_verified': {
          const verifyData = data as {
            page_num: number;
            passed: boolean;
            issues: string[];
          };
          setVerification((prev) => {
            const results = prev?.pageResults || [];
            return {
              passed: prev?.passed ?? true,
              pagesPassed: prev?.pagesPassed || 0,
              pagesFailed: prev?.pagesFailed || 0,
              totalIssues: prev?.totalIssues || 0,
              criticalIssues: prev?.criticalIssues || [],
              pageResults: [
                ...results,
                {
                  pageNum: verifyData.page_num,
                  passed: verifyData.passed,
                  issues: verifyData.issues,
                },
              ],
            };
          });
          break;
        }

        case 'verification:complete': {
          const verifyData = data as {
            passed: boolean;
            pages_passed: number;
            pages_failed: number;
            total_issues: number;
            critical_issues: string[];
          };
          setVerification((prev) => ({
            passed: verifyData.passed,
            pagesPassed: verifyData.pages_passed,
            pagesFailed: verifyData.pages_failed,
            totalIssues: verifyData.total_issues,
            criticalIssues: verifyData.critical_issues,
            pageResults: prev?.pageResults || [],
          }));
          break;
        }

        case 'processing:complete': {
          const completeData = data as {
            success: boolean;
            total_cost: number;
            total_edits: number;
          };
          setTotalCost(completeData.total_cost);
          setTotalEdits(completeData.total_edits);
          setStatusMessage(completeData.success ? 'Complete' : 'Failed');
          setCurrentPage(null);
          // Fetch final markdown when processing completes
          fetchMarkdown();
          break;
        }

        case 'processing:error': {
          const errorData = data as { error: string };
          setStatusMessage(`Error: ${errorData.error}`);
          // Mark any running jobs as failed
          setJobs((prev) =>
            prev.map((job) =>
              job.status === 'running' ? { ...job, status: 'failed' as JobStatusType } : job
            )
          );
          break;
        }
      }
    },
    [onEvent, fetchMarkdown]
  );

  const connect = useCallback(() => {
    if (!jobId || !apiKey) {
      setStatus('error');
      setStatusMessage('Missing job ID or API key');
      return;
    }

    // Close existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    setStatus('connecting');
    setStatusMessage('Connecting...');

    const url = `${apiUrl}/api/v5/jobs/${jobId}/stream`;

    // Create EventSource with credentials
    // Note: EventSource doesn't support custom headers natively,
    // so we pass the API key as a query param (backend should support this)
    const eventSource = new EventSource(`${url}?api_key=${encodeURIComponent(apiKey)}`);
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      setStatus('connected');
      setStatusMessage('Streaming...');
    };

    eventSource.onerror = () => {
      setStatus('error');
      setStatusMessage('Connection error');
      eventSource.close();
    };

    // Listen for all event types
    const eventTypes: V5EventType[] = [
      'docling:started',
      'docling:complete',
      'planning:started',
      'planning:structure',
      'planning:page_summarized',
      'planning:complete',
      'job:created',
      'job:started',
      'job:completed',
      'edit:committed',
      'edit:validated',
      'agent:thinking',
      'verification:started',
      'verification:page_verified',
      'verification:complete',
      'final_pass:started',
      'final_pass:complete',
      'recovery:started',
      'recovery:complete',
      'processing:complete',
      'processing:error',
    ];

    eventTypes.forEach((eventType) => {
      eventSource.addEventListener(eventType, (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data);
          addEvent(eventType, data);
        } catch {
          console.warn(`Failed to parse event data for ${eventType}`);
        }
      });
    });

    // Handle done event
    eventSource.addEventListener('done', () => {
      setStatus('disconnected');
      setStatusMessage('Complete');
      eventSource.close();
    });
  }, [jobId, apiKey, apiUrl, addEvent]);

  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStatus('disconnected');
    setStatusMessage('Disconnected');
  }, []);

  const clearEvents = useCallback(() => {
    setEvents([]);
    setDecisions([]);
    setAgentActivity(new Map());
    setPageMarkdowns(new Map());
    setFinalMarkdown('');
    setTotalCost(0);
    setTotalEdits(0);
    setCurrentPage(null);
    setStatusMessage('Ready');
    // Clear new state
    setDocumentPlan(null);
    setJobs([]);
    setVerification(null);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return {
    events,
    decisions,
    agentActivity,
    pageMarkdowns,
    finalMarkdown,
    status,
    statusMessage,
    currentPage,
    totalCost,
    totalEdits,
    // New: Document plan and job tracking
    documentPlan,
    jobs,
    verification,
    connect,
    disconnect,
    clearEvents,
    fetchMarkdown,
  };
}
