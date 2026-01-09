import { useState, useCallback, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
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
  RotateCcw,
  Settings,
  X,
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
  const [maxRounds, setMaxRounds] = useState(1);
  const [generateDebugBundle, setGenerateDebugBundle] = useState(false);
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
  const [isProcessingComplete, setIsProcessingComplete] = useState(false);

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
        formData.append('max_rounds', String(maxRounds));
        formData.append('generate_debug_bundle', String(generateDebugBundle));

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
    [apiKey, maxRounds, generateDebugBundle, clearEvents, connect, setSearchParams]
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

  // Handle new job - reset state for a fresh start
  const handleNewJob = useCallback(() => {
    // Disconnect if connected
    if (status === 'connected' || status === 'connecting') {
      disconnect();
    }
    // Clear all state
    clearEvents();
    setActiveJobId(null);
    setJobIdInput('');
    setSelectedFile(null);
    setMaxRounds(1);
    setGenerateDebugBundle(false);
    setIsProcessingComplete(false);
    // Clear URL params
    setSearchParams({});
    // Reset file input
    const fileInput = document.getElementById('file-upload') as HTMLInputElement;
    if (fileInput) {
      fileInput.value = '';
    }
  }, [status, disconnect, clearEvents, setSearchParams]);

  // Download markdown result
  const handleDownloadMarkdown = useCallback(async () => {
    if (!activeJobId || !apiKey) return;

    try {
      // Fetch job to get markdown URL
      const response = await fetch(`${API_URL}/api/v1/documents/${activeJobId}`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!response.ok) throw new Error('Failed to fetch job');

      const job = await response.json();
      if (job.markdown_url) {
        window.open(job.markdown_url, '_blank');
      } else {
        alert('Markdown not available yet');
      }
    } catch (error) {
      console.error('Download error:', error);
      alert('Failed to download markdown');
    }
  }, [activeJobId, apiKey]);

  // Download debug bundle
  const handleDownloadDebugBundle = useCallback(async () => {
    if (!activeJobId || !apiKey) return;

    try {
      // Direct download from debug-bundle endpoint
      const url = `${API_URL}/api/v1/documents/${activeJobId}/debug-bundle`;
      const response = await fetch(url, {
        headers: { 'X-API-Key': apiKey },
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to download debug bundle');
      }

      // Trigger download
      const blob = await response.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = `debug-bundle-${activeJobId}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      console.error('Download error:', error);
      alert(`Failed to download debug bundle: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }, [activeJobId, apiKey]);

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
      setIsProcessingComplete(true);
    } else if (errorEvent && !completionAnnouncement.includes('failed')) {
      setCompletionAnnouncement('Processing failed. Check events for details.');
    }
  }, [events, documentPlan, completionAnnouncement]);

  // Advanced Settings Modal
  const AdvancedSettingsModal = () => (
    <AnimatePresence>
      {showAdvancedSettings && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 bg-black/50 z-50"
            onClick={() => setShowAdvancedSettings(false)}
            aria-hidden="true"
          />

          {/* Modal Container - centered with flex */}
          <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none">
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-labelledby="settings-modal-title"
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              className="w-full max-w-md bg-white rounded-xl shadow-2xl overflow-hidden pointer-events-auto"
            >
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 bg-uic-blue text-white">
              <div className="flex items-center gap-2">
                <Settings className="w-5 h-5" />
                <h2 id="settings-modal-title" className="text-lg font-bold">Advanced Settings</h2>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setShowAdvancedSettings(false)}
                aria-label="Close dialog"
                className="text-white hover:bg-white/10"
              >
                <X className="w-5 h-5" />
              </Button>
            </div>

            {/* Content */}
            <div className="p-6 space-y-6">
              {/* Max Rounds */}
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700">Processing Rounds</label>
                <p className="text-xs text-slate-500">
                  Number of iterative refinement rounds. Higher values improve quality but increase cost.
                </p>
                <div className="flex items-center gap-4 mt-2">
                  <Input
                    type="number"
                    min={1}
                    max={5}
                    value={maxRounds}
                    onChange={(e) => setMaxRounds(Math.min(5, Math.max(1, parseInt(e.target.value) || 1)))}
                    className="w-20 text-center"
                  />
                  <span className="text-sm text-slate-500">rounds (1-5)</span>
                </div>
              </div>

              {/* Debug Bundle */}
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700">Debug Bundle</label>
                <p className="text-xs text-slate-500">
                  Capture all agent prompts, responses, and intermediate outputs for debugging.
                </p>
                <label className="flex items-center gap-3 mt-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={generateDebugBundle}
                    onChange={(e) => setGenerateDebugBundle(e.target.checked)}
                    className="w-4 h-4 rounded border-slate-300 text-uic-blue focus:ring-uic-blue"
                  />
                  <span className="text-sm text-slate-700">Enable debug bundle generation</span>
                </label>
              </div>
            </div>

            {/* Footer */}
            <div className="px-6 py-4 bg-slate-50 border-t border-slate-200 flex justify-end">
              <Button
                onClick={() => setShowAdvancedSettings(false)}
                className="bg-uic-blue hover:bg-uic-blue/90"
              >
                Done
              </Button>
            </div>
            </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>
  );

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

          {/* Advanced Settings Button */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowAdvancedSettings(true)}
            title="Advanced Settings"
            className="gap-2"
          >
            <Settings className="w-4 h-4" />
            <span className="text-xs">
              {maxRounds > 1 || generateDebugBundle ? (
                <span className="text-uic-blue font-medium">
                  {maxRounds > 1 ? `${maxRounds}R` : ''}
                  {maxRounds > 1 && generateDebugBundle ? ' · ' : ''}
                  {generateDebugBundle ? 'Debug' : ''}
                </span>
              ) : (
                'Settings'
              )}
            </span>
          </Button>

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

          {/* Control buttons - icon-only for compact layout */}
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon"
              onClick={handleNewJob}
              disabled={uploading}
              title="New Job - Clear current job and start fresh"
              aria-label="New Job"
            >
              <RotateCcw className="w-4 h-4" />
            </Button>
            {status === 'connected' ? (
              <Button
                variant="outline"
                size="icon"
                onClick={disconnect}
                title="Stop - Disconnect from stream"
                aria-label="Stop"
              >
                <Square className="w-4 h-4" />
              </Button>
            ) : (
              <Button
                variant="outline"
                size="icon"
                onClick={connect}
                disabled={!activeJobId || !apiKey || status === 'connecting'}
                title="Reconnect - Resume watching job"
                aria-label="Reconnect"
              >
                <RefreshCw className="w-4 h-4" />
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
                <MarkdownViewer
                  content={finalMarkdown}
                  className="flex-1"
                  isComplete={isProcessingComplete}
                  showDebugDownload={generateDebugBundle}
                  onDownloadMarkdown={handleDownloadMarkdown}
                  onDownloadDebug={handleDownloadDebugBundle}
                />
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

      {/* Advanced Settings Modal */}
      <AdvancedSettingsModal />
    </div>
  );
}
