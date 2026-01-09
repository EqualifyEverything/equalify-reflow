import { useState, useCallback, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from 'react-resizable-panels';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { EventLog } from '@/components/viewer/EventLog';
import { MarkdownViewer } from '@/components/viewer/MarkdownViewer';
import { AgentDetailModal } from '@/components/viewer/AgentDetailModal';
import { DecisionList } from '@/components/viewer/DecisionList';
import { JobNavigationPanel } from '@/components/viewer/JobNavigationPanel';
import { SkipNav } from '@/components/viewer/SkipNav';
import { useStream } from '@/hooks/useStream';
import type { AgentThinkingData } from '@/types/events';

// Skip navigation destinations
const skipLinks = [
  { id: 'controls', label: 'Controls' },
  { id: 'document-nav', label: 'Document Navigation' },
  { id: 'preview', label: 'Preview' },
  { id: 'events', label: 'Events & Decisions' },
];
import {
  Play,
  Square,
  RefreshCw,
  Upload,
  Wifi,
  WifiOff,
  AlertCircle,
  Loader2,
  DollarSign,
  FileEdit,
  FileText,
  Activity,
  ListChecks,
} from 'lucide-react';

// Get API URL from environment or default
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';
const API_KEY_STORAGE_KEY = 'pipeline-viewer-api-key';

export function ViewerPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [apiKey, setApiKey] = useState(() => {
    // Priority: URL param > localStorage > empty
    return searchParams.get('api_key') || localStorage.getItem(API_KEY_STORAGE_KEY) || '';
  });
  const [jobIdInput, setJobIdInput] = useState(searchParams.get('job_id') || '');
  const [activeJobId, setActiveJobId] = useState<string | null>(searchParams.get('job_id'));
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  // Persist API key to localStorage
  useEffect(() => {
    if (apiKey) {
      localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
    }
  }, [apiKey]);

  // Agent detail modal state
  const [agentModal, setAgentModal] = useState<{
    isOpen: boolean;
    jobId: string;
    page: number;
    events: AgentThinkingData[];
  }>({
    isOpen: false,
    jobId: '',
    page: 0,
    events: [],
  });

  const {
    events,
    decisions,
    agentActivity,
    finalMarkdown,
    status,
    statusMessage,
    currentPage,
    totalCost,
    totalEdits,
    documentPlan,
    jobs,
    verification,
    connect,
    disconnect,
    clearEvents,
  } = useStream({
    apiUrl: API_URL,
    apiKey,
    jobId: activeJobId,
  });

  // Handle file upload
  const handleFileUpload = useCallback(
    async (file: File) => {
      if (!apiKey) {
        alert('Please enter an API key');
        return;
      }

      setUploading(true);
      clearEvents();

      try {
        const formData = new FormData();
        formData.append('file', file);
        // Use agentic pipeline directly (skip PII scanning for demo viewer)
        formData.append('skip_pii_scan', 'true');
        formData.append('skip_reason', 'Pipeline viewer direct submission');
        formData.append('review_mode', 'auto');

        const response = await fetch(`${API_URL}/api/v1/documents/submit`, {
          method: 'POST',
          headers: {
            'X-API-Key': apiKey,
          },
          body: formData,
        });

        if (!response.ok) {
          throw new Error(`Upload failed: ${response.statusText}`);
        }

        const data = await response.json();
        const newJobId = data.job_id;

        setActiveJobId(newJobId);
        setJobIdInput(newJobId);
        setSearchParams({ job_id: newJobId, api_key: apiKey });

        // Start streaming
        setTimeout(() => connect(), 500);
      } catch (error) {
        console.error('Upload error:', error);
        alert(`Upload failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
      } finally {
        setUploading(false);
      }
    },
    [apiKey, clearEvents, connect, setSearchParams]
  );

  // Handle watch job
  const handleWatchJob = useCallback(() => {
    if (!jobIdInput || !apiKey) {
      alert('Please enter both a Job ID and API key');
      return;
    }

    setActiveJobId(jobIdInput);
    setSearchParams({ job_id: jobIdInput, api_key: apiKey });
    clearEvents();
    setTimeout(() => connect(), 100);
  }, [jobIdInput, apiKey, clearEvents, connect, setSearchParams]);

  // Handle job click in event log
  const handleJobClick = useCallback(
    (jobId: string, page: number) => {
      const events = agentActivity.get(jobId) || [];
      setAgentModal({
        isOpen: true,
        jobId,
        page,
        events,
      });
    },
    [agentActivity]
  );

  // Connection status indicator
  const StatusIndicator = () => {
    const statusConfig = {
      connecting: { icon: Loader2, color: 'text-amber-500', animate: true },
      connected: { icon: Wifi, color: 'text-green-500', animate: false },
      disconnected: { icon: WifiOff, color: 'text-slate-400', animate: false },
      error: { icon: AlertCircle, color: 'text-uic-red', animate: false },
    };

    const config = statusConfig[status];
    const Icon = config.icon;

    return (
      <div className={cn('flex items-center gap-2', config.color)}>
        <Icon className={cn('w-4 h-4', config.animate && 'animate-spin')} />
        <span className="text-sm font-medium">{statusMessage}</span>
      </div>
    );
  };

  // Live region announcement for status changes
  const [statusAnnouncement, setStatusAnnouncement] = useState('');
  const prevStatus = useRef(status);

  // Announce connection status changes
  useEffect(() => {
    if (prevStatus.current !== status) {
      const announcements: Record<string, string> = {
        connected: 'Connected to pipeline',
        connecting: 'Connecting to pipeline...',
        disconnected: 'Disconnected from pipeline',
        error: 'Connection error',
      };
      setStatusAnnouncement(announcements[status] || '');
      prevStatus.current = status;
    }
  }, [status]);

  // Announce processing completion
  const [completionAnnouncement, setCompletionAnnouncement] = useState('');
  useEffect(() => {
    const completeEvent = events.find((e) => e.type === 'processing:complete');
    const errorEvent = events.find((e) => e.type === 'processing:error');

    if (completeEvent && !completionAnnouncement.includes('complete')) {
      const pageCount = documentPlan?.totalPages || 0;
      setCompletionAnnouncement(`Processing complete. ${pageCount} pages converted.`);
    } else if (errorEvent && !completionAnnouncement.includes('failed')) {
      setCompletionAnnouncement('Processing failed. Check events for details.');
    }
  }, [events, documentPlan, completionAnnouncement]);

  return (
    <div className="flex flex-col h-screen bg-slate-100">
      {/* Skip Navigation Menu - Ctrl+/ or Cmd+/ to open */}
      <SkipNav links={skipLinks} />

      {/* Live region for connection status announcements */}
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {statusAnnouncement}
      </div>

      {/* Live region for processing completion announcements */}
      <div role="status" aria-live="polite" className="sr-only">
        {completionAnnouncement}
      </div>

      {/* Header */}
      <header className="bg-uic-blue text-white px-6 py-4 shadow-lg">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            {/* UIC Logo Circle */}
            <div className="w-12 h-12 bg-uic-red rounded-full flex items-center justify-center shadow-md">
              <span className="text-white font-bold text-lg">UIC</span>
            </div>
            <div>
              <h1 className="text-xl font-bold">Pipeline Viewer</h1>
              <p className="text-sm text-white/70">Real-time document processing monitor</p>
            </div>
          </div>

          {/* Status Bar */}
          <div className="flex items-center gap-6">
            {currentPage && (
              <div className="flex items-center gap-2 text-white/80">
                <FileText className="w-4 h-4" />
                <span className="text-sm">Page {currentPage}</span>
              </div>
            )}
            {totalEdits > 0 && (
              <div className="flex items-center gap-2 text-white/80">
                <FileEdit className="w-4 h-4" />
                <span className="text-sm">{totalEdits} edits</span>
              </div>
            )}
            {totalCost > 0 && (
              <div className="flex items-center gap-2 text-green-300">
                <DollarSign className="w-4 h-4" />
                <span className="text-sm">${totalCost.toFixed(3)}</span>
              </div>
            )}
            <div className="h-6 w-px bg-white/20" />
            <StatusIndicator />
          </div>
        </div>
      </header>

      {/* Control Bar */}
      <div id="controls" tabIndex={-1} className="bg-white border-b px-6 py-3 shadow-sm">
        <div className="flex items-center gap-4">
          {/* API Key Input */}
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium text-slate-600 whitespace-nowrap">API Key:</label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter API key"
              className="w-48"
            />
          </div>

          <div className="h-6 w-px bg-slate-200" />

          {/* Job ID Input */}
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium text-slate-600 whitespace-nowrap">Job ID:</label>
            <Input
              value={jobIdInput}
              onChange={(e) => setJobIdInput(e.target.value)}
              placeholder="Enter job ID to watch"
              className="w-72"
            />
            <Button
              onClick={handleWatchJob}
              disabled={!jobIdInput || !apiKey || status === 'connected'}
              className="bg-uic-blue hover:bg-uic-blue/90"
            >
              <Play className="w-4 h-4 mr-2" />
              Watch
            </Button>
          </div>

          <div className="h-6 w-px bg-slate-200" />

          {/* File Upload */}
          <div className="flex items-center gap-2">
            <input
              type="file"
              accept=".pdf"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  setSelectedFile(file);
                  handleFileUpload(file);
                }
              }}
              className="hidden"
              id="file-upload"
            />
            <label htmlFor="file-upload">
              <Button
                asChild
                disabled={uploading || !apiKey}
                className="bg-uic-red hover:bg-uic-red/90 cursor-pointer"
              >
                <span>
                  {uploading ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <Upload className="w-4 h-4 mr-2" />
                  )}
                  {selectedFile ? selectedFile.name : 'Upload PDF'}
                </span>
              </Button>
            </label>
          </div>

          <div className="flex-1" />

          {/* Control buttons */}
          <div className="flex items-center gap-2">
            {status === 'connected' ? (
              <Button variant="outline" onClick={disconnect}>
                <Square className="w-4 h-4 mr-2" />
                Stop
              </Button>
            ) : (
              <Button
                variant="outline"
                onClick={connect}
                disabled={!activeJobId || !apiKey || status === 'connecting'}
              >
                <RefreshCw className="w-4 h-4 mr-2" />
                Reconnect
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Main Content - Fixed sidebar + resizable main area */}
      <main className="flex-1 overflow-hidden flex">
        {/* Left Sidebar - Fixed width Job Navigation */}
        <nav
          id="document-nav"
          tabIndex={-1}
          aria-label="Document navigation"
          className="w-72 flex-shrink-0 h-full m-2 mr-1"
        >
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            className="h-full rounded-lg shadow-md overflow-hidden border border-slate-200 bg-white"
          >
            <JobNavigationPanel
            documentPlan={documentPlan}
            jobs={jobs}
            verification={verification}
            currentPage={currentPage}
            onJobClick={handleJobClick}
          />
          </motion.div>
        </nav>

        {/* Right area - Resizable panels for content */}
        <div className="flex-1 overflow-hidden">
          <PanelGroup orientation="horizontal" className="h-full">
            {/* Center Panel - Markdown Viewer */}
            <Panel defaultSize={55} minSize={30}>
              <section
                id="preview"
                tabIndex={-1}
                aria-label="Document preview"
                className="h-full"
              >
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1 }}
                  className="h-full flex flex-col bg-white m-2 mx-1 rounded-lg shadow-md overflow-hidden"
                >
                {/* Panel Header */}
                <div className="flex items-center gap-2 px-4 py-3 bg-gradient-to-r from-uic-blue to-uic-blue/90 text-white">
                  <FileText className="w-5 h-5" />
                  <span className="font-semibold">Markdown Output</span>
                </div>
                <MarkdownViewer content={finalMarkdown} className="flex-1" />
                </motion.div>
              </section>
            </Panel>

            {/* Resize Handle */}
            <PanelResizeHandle className="w-1 bg-transparent hover:bg-uic-blue/20 transition-colors cursor-col-resize" />

            {/* Right Panel - Events & Decisions */}
            <Panel defaultSize={45} minSize={25}>
              <section
                id="events"
                tabIndex={-1}
                aria-label="Events and decisions"
                className="h-full flex flex-col m-2 ml-1 gap-2"
              >
                {/* Events Panel */}
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.2 }}
                  className="flex-[2] flex flex-col bg-white rounded-lg shadow-md overflow-hidden"
                >
                  <div className="flex items-center gap-2 px-4 py-3 bg-gradient-to-r from-slate-700 to-slate-600 text-white">
                    <Activity className="w-5 h-5" />
                    <span className="font-semibold">Events</span>
                    <span className="ml-auto text-xs bg-white/20 px-2 py-0.5 rounded-full">
                      {events.filter((e) => e.type !== 'agent:thinking').length}
                    </span>
                  </div>
                  <EventLog events={events} onJobClick={handleJobClick} className="flex-1" />
                </motion.div>

                {/* Decisions Panel */}
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.3 }}
                  className="flex-1 flex flex-col bg-white rounded-lg shadow-md overflow-hidden"
                >
                  <div className="flex items-center gap-2 px-4 py-3 bg-gradient-to-r from-green-600 to-green-500 text-white">
                    <ListChecks className="w-5 h-5" />
                    <span className="font-semibold">Decisions</span>
                    <span className="text-xs text-white/70 ml-1">(click for details)</span>
                    <span className="ml-auto text-xs bg-white/20 px-2 py-0.5 rounded-full">
                      {decisions.length}
                    </span>
                  </div>
                  <DecisionList decisions={decisions} className="flex-1" />
                </motion.div>
              </section>
            </Panel>
          </PanelGroup>
        </div>
      </main>

      {/* Agent Detail Modal */}
      <AgentDetailModal
        isOpen={agentModal.isOpen}
        onClose={() => setAgentModal((prev) => ({ ...prev, isOpen: false }))}
        jobId={agentModal.jobId}
        page={agentModal.page}
        events={agentModal.events}
      />
    </div>
  );
}
