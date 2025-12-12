import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, ProcessingPhasesResponse } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { JsonViewer } from '@/components/dev/JsonViewer'
import {
  ArrowLeft,
  FileSearch,
  FileText,
  Eye,
  GitMerge,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Loader2,
} from 'lucide-react'

function PhaseStatusBadge({ status }: { status: string }) {
  const variants: Record<string, { color: string; icon: React.ReactNode }> = {
    completed: { color: 'bg-green-500', icon: <CheckCircle2 className="h-3 w-3" /> },
    skipped: { color: 'bg-slate-400', icon: <XCircle className="h-3 w-3" /> },
    error: { color: 'bg-red-500', icon: <AlertTriangle className="h-3 w-3" /> },
    pending: { color: 'bg-yellow-500', icon: <Clock className="h-3 w-3" /> },
  }
  const { color, icon } = variants[status] || variants.pending
  return (
    <Badge className={`${color} text-white flex items-center gap-1`}>
      {icon}
      {status}
    </Badge>
  )
}

function AnalysisPhaseCard({ phase, showRaw }: { phase: ProcessingPhasesResponse['analysis']; showRaw: boolean }) {
  if (phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <FileSearch className="h-5 w-5" />
              Phase 1: Analysis
            </CardTitle>
            <PhaseStatusBadge status={phase.status} />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">No analysis data available</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <FileSearch className="h-5 w-5" />
            Phase 1: Analysis
          </CardTitle>
          <PhaseStatusBadge status={phase.status} />
        </div>
        <CardDescription>Document structure analysis and agent routing</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Document Info */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-xs text-muted-foreground">Title</p>
            <p className="font-medium truncate">{phase.document_title || 'Unknown'}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Type</p>
            <p className="font-medium">{phase.document_type || 'Unknown'}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Pages</p>
            <p className="font-medium">{phase.total_pages || 0}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Confidence</p>
            <p className="font-medium">{((phase.analysis_confidence || 0) * 100).toFixed(0)}%</p>
          </div>
        </div>

        {/* Required Agents */}
        {phase.required_agents.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-2">Required Agents</p>
            <div className="flex flex-wrap gap-2">
              {phase.required_agents.map((agent) => (
                <Badge key={agent} variant="outline">
                  {agent.replace(/_/g, ' ')}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Page Features */}
        {phase.page_features.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-2">Page Features</p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-2">Page</th>
                    <th className="text-center py-2">Images</th>
                    <th className="text-center py-2">Tables</th>
                    <th className="text-center py-2">Lists</th>
                    <th className="text-center py-2">Complexity</th>
                  </tr>
                </thead>
                <tbody>
                  {phase.page_features.map((pf) => (
                    <tr key={pf.page_num} className="border-b">
                      <td className="py-2">{pf.page_num}</td>
                      <td className="text-center">{pf.has_images ? `${pf.image_count}` : '-'}</td>
                      <td className="text-center">{pf.has_tables ? `${pf.table_count}` : '-'}</td>
                      <td className="text-center">{pf.has_lists ? 'Yes' : '-'}</td>
                      <td className="text-center">{pf.complexity_score.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Raw Manifest */}
        {showRaw && phase.raw_manifest && (
          <JsonViewer
            data={phase.raw_manifest}
            title="Raw Manifest"
            keyFields={['document_title', 'total_pages', 'required_agents']}
          />
        )}
      </CardContent>
    </Card>
  )
}

function ExtractionPhaseCard({ phase }: { phase: ProcessingPhasesResponse['extraction'] }) {
  if (phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Phase 2: Extraction
            </CardTitle>
            <PhaseStatusBadge status={phase.status} />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">No extraction data available</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5" />
            Phase 2: Extraction
          </CardTitle>
          <PhaseStatusBadge status={phase.status} />
        </div>
        <CardDescription>PDF to Markdown conversion (v0)</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-muted-foreground">Extraction Model</p>
            <p className="font-medium">{phase.extraction_model || 'Unknown'}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Confidence</p>
            <p className="font-medium">{((phase.confidence_score || 0) * 100).toFixed(0)}%</p>
          </div>
        </div>

        {phase.markdown_url && (
          <Button variant="outline" size="sm" asChild>
            <a href={phase.markdown_url} target="_blank" rel="noopener noreferrer">
              View Original Markdown (v0)
            </a>
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

function AgentsPhaseCard({ phase, showRaw }: { phase: ProcessingPhasesResponse['agents']; showRaw: boolean }) {
  if (phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5" />
              Phase 3: Specialized Agents
            </CardTitle>
            <PhaseStatusBadge status={phase.status} />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">No agent observations available</p>
        </CardContent>
      </Card>
    )
  }

  const severityColors: Record<string, string> = {
    critical: 'text-red-600',
    high: 'text-orange-600',
    medium: 'text-yellow-600',
    low: 'text-blue-600',
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <Eye className="h-5 w-5" />
            Phase 3: Specialized Agents
          </CardTitle>
          <PhaseStatusBadge status={phase.status} />
        </div>
        <CardDescription>Visual-vs-markup discrepancy detection</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Agent Summary */}
        <div className="flex items-center gap-4">
          <div className="text-center">
            <span className="text-2xl font-bold text-purple-600">{phase.observation_count}</span>
            <p className="text-xs text-muted-foreground">observations</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {phase.agents_run.map((agent) => (
              <Badge key={agent} variant="secondary">
                {agent.replace(/_/g, ' ')}
              </Badge>
            ))}
          </div>
        </div>

        {/* Observations List */}
        {phase.observations.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Observations</p>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {phase.observations.map((obs) => (
                <div key={obs.id} className="border rounded p-3 text-sm">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-xs">{obs.agent}</Badge>
                      <span className={`font-medium ${severityColors[obs.severity] || ''}`}>
                        {obs.severity}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={obs.route === 'auto' ? 'default' : 'secondary'}>
                        {obs.route}
                      </Badge>
                      <span className="text-muted-foreground">{(obs.confidence * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                  {obs.visual_description && (
                    <p className="text-xs text-muted-foreground mt-1">
                      <strong>Visual:</strong> {obs.visual_description}
                    </p>
                  )}
                  {obs.markup_description && (
                    <p className="text-xs text-muted-foreground">
                      <strong>Markup:</strong> {obs.markup_description}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Raw Observations */}
        {showRaw && phase.raw_observations && (
          <JsonViewer
            data={phase.raw_observations}
            title="Raw Observations"
            keyFields={['id', 'agent', 'severity']}
          />
        )}
      </CardContent>
    </Card>
  )
}

function ConsolidationPhaseCard({ phase, showRaw }: { phase: ProcessingPhasesResponse['consolidation']; showRaw: boolean }) {
  if (phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <GitMerge className="h-5 w-5" />
              Phase 4: Consolidation
            </CardTitle>
            <PhaseStatusBadge status={phase.status} />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">No consolidation proposals available</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <GitMerge className="h-5 w-5" />
            Phase 4: Consolidation
          </CardTitle>
          <PhaseStatusBadge status={phase.status} />
        </div>
        <CardDescription>Edit proposals generation and routing</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Proposal Summary */}
        <div className="flex items-center gap-4">
          <div className="text-center">
            <span className="text-2xl font-bold text-purple-600">{phase.proposal_count}</span>
            <p className="text-xs text-muted-foreground">total</p>
          </div>
          <div className="text-center">
            <span className="text-2xl font-bold text-green-600">{phase.auto_count}</span>
            <p className="text-xs text-muted-foreground">auto</p>
          </div>
          <div className="text-center">
            <span className="text-2xl font-bold text-amber-600">{phase.manual_count}</span>
            <p className="text-xs text-muted-foreground">manual</p>
          </div>
        </div>

        {/* Proposals List */}
        {phase.proposals.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Proposals</p>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {phase.proposals.map((prop) => (
                <div key={prop.id} className="border rounded p-3 text-sm">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Badge variant={prop.route === 'auto' ? 'default' : 'secondary'}>
                        {prop.route}
                      </Badge>
                      <Badge variant="outline">{prop.status}</Badge>
                      <span className="text-muted-foreground text-xs">
                        Pages: {prop.page_nums.join(', ')}
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      resolves {prop.resolves_count} observation(s)
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="bg-red-50 border border-red-200 rounded p-2">
                      <p className="font-medium text-red-700 mb-1">Search:</p>
                      <code className="break-all">{prop.search_preview || '(empty)'}</code>
                    </div>
                    <div className="bg-green-50 border border-green-200 rounded p-2">
                      <p className="font-medium text-green-700 mb-1">Replace:</p>
                      <code className="break-all">{prop.replace_preview || '(empty)'}</code>
                    </div>
                  </div>
                  <p className="text-[10px] text-muted-foreground/70 mt-1 leading-snug line-clamp-2">{prop.justification}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Raw Proposals */}
        {showRaw && phase.raw_proposals && (
          <JsonViewer
            data={phase.raw_proposals}
            title="Raw Proposals"
            keyFields={['id', 'route', 'status']}
          />
        )}
      </CardContent>
    </Card>
  )
}

export function ProcessingPhasesPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [showRaw, setShowRaw] = useState(false)
  const [activeTab, setActiveTab] = useState('all')

  const {
    data: phases,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['job-phases', jobId, showRaw],
    queryFn: () => api.getJobPhases(jobId!, showRaw),
    enabled: !!jobId,
  })

  if (!jobId) {
    return (
      <div className="max-w-6xl mx-auto">
        <p className="text-red-600">Invalid job ID</p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="max-w-6xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center">
            <Loader2 className="h-12 w-12 text-slate-400 mx-auto mb-4 animate-spin" />
            <p className="text-muted-foreground">Loading processing phases...</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !phases) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" asChild>
            <Link to={`/job/${jobId}`}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Job
            </Link>
          </Button>
        </div>
        <Card>
          <CardContent className="p-8 text-center">
            <XCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
            <h2 className="text-xl font-bold text-red-600 mb-2">Unable to Load Phases</h2>
            <p className="text-muted-foreground mb-4">
              {error instanceof Error ? error.message : 'Failed to load processing phases'}
            </p>
            <Button onClick={() => refetch()}>Retry</Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="outline" asChild>
            <Link to={`/job/${jobId}`}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Job
            </Link>
          </Button>
          <div>
            <h1 className="text-3xl font-bold text-uic-navy">Processing Phases</h1>
            <p className="text-muted-foreground">
              {phases.filename} &middot; {formatDate(phases.created_at)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center space-x-2">
            <Checkbox
              id="show-raw"
              checked={showRaw}
              onCheckedChange={(checked) => setShowRaw(checked === true)}
            />
            <Label htmlFor="show-raw">Show Raw Data</Label>
          </div>
          <Badge variant={phases.status === 'completed' ? 'default' : 'secondary'}>
            {phases.status}
          </Badge>
        </div>
      </div>

      {/* Tabs for each phase */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="all">All Phases</TabsTrigger>
          <TabsTrigger value="analysis">Analysis</TabsTrigger>
          <TabsTrigger value="extraction">Extraction</TabsTrigger>
          <TabsTrigger value="agents">Agents</TabsTrigger>
          <TabsTrigger value="consolidation">Consolidation</TabsTrigger>
        </TabsList>

        <TabsContent value="all" className="space-y-4">
          <AnalysisPhaseCard phase={phases.analysis} showRaw={showRaw} />
          <ExtractionPhaseCard phase={phases.extraction} />
          <AgentsPhaseCard phase={phases.agents} showRaw={showRaw} />
          <ConsolidationPhaseCard phase={phases.consolidation} showRaw={showRaw} />
        </TabsContent>

        <TabsContent value="analysis">
          <AnalysisPhaseCard phase={phases.analysis} showRaw={showRaw} />
        </TabsContent>

        <TabsContent value="extraction">
          <ExtractionPhaseCard phase={phases.extraction} />
        </TabsContent>

        <TabsContent value="agents">
          <AgentsPhaseCard phase={phases.agents} showRaw={showRaw} />
        </TabsContent>

        <TabsContent value="consolidation">
          <ConsolidationPhaseCard phase={phases.consolidation} showRaw={showRaw} />
        </TabsContent>
      </Tabs>

      {/* LLM Cost Summary */}
      {phases.total_llm_cost && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">LLM Cost Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex gap-6 text-sm">
              <div>
                <span className="text-muted-foreground">Input tokens:</span>{' '}
                <span className="font-mono">{phases.total_llm_cost.input_tokens.toLocaleString()}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Output tokens:</span>{' '}
                <span className="font-mono">{phases.total_llm_cost.output_tokens.toLocaleString()}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Total cost:</span>{' '}
                <span className="font-mono">${phases.total_llm_cost.estimated_cost_dollars.toFixed(4)}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Full Response JSON */}
      {showRaw && (
        <JsonViewer
          data={phases}
          title="Full API Response"
          keyFields={['job_id', 'status', 'filename']}
        />
      )}
    </div>
  )
}
