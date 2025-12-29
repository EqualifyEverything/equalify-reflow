import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  api,
  AnalysisPhase,
  ExtractionPhase,
  RefinePhase,
  RemediationPhase,
  AgentsPhase,
  VerificationPhase,
} from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { JsonViewer } from '@/components/dev/JsonViewer'
import {
  ArrowLeft,
  FileSearch,
  FileText,
  RefreshCcw,
  Wrench,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Loader2,
  Bot,
  Sparkles,
  ShieldCheck,
} from 'lucide-react'

function PhaseStatusBadge({ status }: { status: string }) {
  const variants: Record<string, { color: string; icon: React.ReactNode }> = {
    completed: { color: 'bg-green-500', icon: <CheckCircle2 className="h-3 w-3" /> },
    skipped: { color: 'bg-muted-foreground', icon: <XCircle className="h-3 w-3" /> },
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

// ============================================================================
// Phase 1: Analyze
// ============================================================================
function AnalyzePhaseCard({ phase, showRaw }: { phase?: AnalysisPhase; showRaw: boolean }) {
  if (!phase || phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <FileSearch className="h-5 w-5" />
              Phase 1: Analyze
            </CardTitle>
            <PhaseStatusBadge status={phase?.status || 'skipped'} />
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
            Phase 1: Analyze
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

        {/* Phase 5: Document Summary (Glass-Box Transparency) */}
        {phase.document_summary && (
          <div className="border rounded-lg p-4 bg-muted/30 space-y-3">
            <h4 className="font-semibold flex items-center gap-2 text-sm">
              <Sparkles className="h-4 w-4 text-purple-500" />
              Document Summary
            </h4>

            {phase.document_summary.topic_summary && (
              <div>
                <p className="text-xs text-muted-foreground">Topic</p>
                <p className="text-sm">{phase.document_summary.topic_summary}</p>
              </div>
            )}

            {phase.document_summary.structure_summary && (
              <div>
                <p className="text-xs text-muted-foreground">Structure</p>
                <p className="text-sm">{phase.document_summary.structure_summary}</p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              {phase.document_summary.key_entities.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">Key Entities</p>
                  <div className="flex flex-wrap gap-1">
                    {phase.document_summary.key_entities.map((entity, idx) => (
                      <Badge key={idx} variant="outline" className="text-xs">
                        {entity}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {phase.document_summary.domain_terms.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">Domain Terms</p>
                  <div className="flex flex-wrap gap-1">
                    {phase.document_summary.domain_terms.map((term, idx) => (
                      <Badge key={idx} variant="secondary" className="text-xs">
                        {term}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex gap-4">
              {phase.document_summary.audience_level && (
                <div>
                  <p className="text-xs text-muted-foreground">Audience</p>
                  <Badge className="bg-blue-500 text-white">
                    {phase.document_summary.audience_level}
                  </Badge>
                </div>
              )}

              {phase.document_summary.expected_elements.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">Expected Elements</p>
                  <div className="flex flex-wrap gap-1">
                    {phase.document_summary.expected_elements.map((el, idx) => (
                      <Badge key={idx} variant="outline" className="text-xs bg-green-50 dark:bg-green-900/30">
                        {el}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

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

        {/* Phase 5: Timing/Cost Metrics */}
        {(phase.time_seconds || phase.cost_cents) && (
          <div className="flex gap-4 text-xs text-muted-foreground border-t pt-2">
            {phase.time_seconds && <span>Time: {phase.time_seconds.toFixed(1)}s</span>}
            {phase.cost_cents && <span>Cost: ${(phase.cost_cents / 100).toFixed(4)}</span>}
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

// ============================================================================
// Phase 2: Extract
// ============================================================================
function ExtractPhaseCard({ phase }: { phase?: ExtractionPhase }) {
  if (!phase || phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Phase 2: Extract
            </CardTitle>
            <PhaseStatusBadge status={phase?.status || 'skipped'} />
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
            Phase 2: Extract
          </CardTitle>
          <PhaseStatusBadge status={phase.status} />
        </div>
        <CardDescription>PDF to Markdown conversion (v0)</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-xs text-muted-foreground">Model</p>
            <p className="font-medium">{phase.extraction_model || 'Unknown'}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Confidence</p>
            <p className="font-medium">{((phase.confidence_score || 0) * 100).toFixed(0)}%</p>
          </div>
          {/* Phase 5: New metrics */}
          {phase.pages_extracted !== undefined && (
            <div>
              <p className="text-xs text-muted-foreground">Pages Extracted</p>
              <p className="font-medium">{phase.pages_extracted}</p>
            </div>
          )}
          {phase.correction_iterations !== undefined && (
            <div>
              <p className="text-xs text-muted-foreground">Correction Iterations</p>
              <p className="font-medium">{phase.correction_iterations}</p>
            </div>
          )}
        </div>

        {phase.markdown_url && (
          <Button variant="outline" size="sm" asChild>
            <a href={phase.markdown_url} target="_blank" rel="noopener noreferrer">
              View Original Markdown (v0)
            </a>
          </Button>
        )}

        {/* Phase 5: Timing/Cost Metrics */}
        {(phase.time_seconds || phase.cost_cents) && (
          <div className="flex gap-4 text-xs text-muted-foreground border-t pt-2">
            {phase.time_seconds && <span>Time: {phase.time_seconds.toFixed(1)}s</span>}
            {phase.cost_cents && <span>Cost: ${(phase.cost_cents / 100).toFixed(4)}</span>}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ============================================================================
// Phase 3: Refine (NEW - combines structure loop + agents)
// ============================================================================
function RefinePhaseCard({
  refinePhase,
  legacyAgentsPhase,
  showRaw,
}: {
  refinePhase?: RefinePhase
  legacyAgentsPhase?: AgentsPhase
  showRaw: boolean
}) {
  // Use Phase 5 refine data if available, otherwise fall back to legacy agents
  const hasRefineData = refinePhase && refinePhase.status !== 'skipped'
  const hasLegacyData = legacyAgentsPhase && legacyAgentsPhase.status !== 'skipped'

  if (!hasRefineData && !hasLegacyData) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <RefreshCcw className="h-5 w-5" />
              Phase 3: Refine
            </CardTitle>
            <PhaseStatusBadge status="skipped" />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">No refinement data available</p>
        </CardContent>
      </Card>
    )
  }

  // Phase 5 Refine format
  if (hasRefineData && refinePhase) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <RefreshCcw className="h-5 w-5" />
              Phase 3: Refine
            </CardTitle>
            <PhaseStatusBadge status={refinePhase.status} />
          </div>
          <CardDescription>Structure verification loop + specialized agents</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Summary Stats */}
          <div className="flex items-center gap-4">
            <div className="text-center">
              <span className="text-2xl font-bold text-purple-600 dark:text-purple-400">
                {refinePhase.total_observations}
              </span>
              <p className="text-xs text-muted-foreground">observations</p>
            </div>
            <div className="text-center">
              <span className="text-2xl font-bold text-green-600 dark:text-green-400">
                {refinePhase.total_auto_corrections}
              </span>
              <p className="text-xs text-muted-foreground">auto-fixed</p>
            </div>
            <div className="text-center">
              <span className="text-2xl font-bold text-amber-600 dark:text-amber-400">
                {refinePhase.total_review_items}
              </span>
              <p className="text-xs text-muted-foreground">need review</p>
            </div>
          </div>

          {/* Structure Verification Loop */}
          {refinePhase.structure_loop && (
            <div className="border rounded-lg p-4 bg-muted/30 space-y-2">
              <h4 className="font-semibold flex items-center gap-2 text-sm">
                <RefreshCcw className="h-4 w-4 text-blue-500" />
                Structure Verification Loop
              </h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-xs text-muted-foreground">Iterations</p>
                  <p className="font-medium">{refinePhase.structure_loop.iterations}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Lint Issues Found</p>
                  <p className="font-medium">{refinePhase.structure_loop.lint_issues_found}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Lint Issues Fixed</p>
                  <p className="font-medium">{refinePhase.structure_loop.lint_issues_fixed}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">OCR Suggestions</p>
                  <p className="font-medium">{refinePhase.structure_loop.ocr_suggestions_processed}</p>
                </div>
              </div>
              <div className="flex items-center gap-2 mt-2">
                <Badge
                  className={
                    refinePhase.structure_loop.final_lint_clean
                      ? 'bg-green-500 text-white'
                      : 'bg-amber-500 text-white'
                  }
                >
                  {refinePhase.structure_loop.final_lint_clean ? 'Lint Clean' : 'Has Lint Issues'}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {refinePhase.structure_loop.corrections_applied} corrections applied
                </span>
              </div>
            </div>
          )}

          {/* Specialized Agents */}
          {refinePhase.agents.length > 0 && (
            <div className="space-y-2">
              <h4 className="font-semibold flex items-center gap-2 text-sm">
                <Bot className="h-4 w-4 text-purple-500" />
                Specialized Agents ({refinePhase.agents.length})
              </h4>
              <div className="max-h-64 overflow-y-auto space-y-2">
                {refinePhase.agents.map((agent) => (
                  <div key={agent.agent_name} className="border rounded p-3 text-sm">
                    <div className="flex items-center justify-between mb-2">
                      <Badge variant="outline" className="capitalize">
                        {agent.agent_name.replace(/_/g, ' ')}
                      </Badge>
                      <span className="text-muted-foreground">
                        {(agent.confidence * 100).toFixed(0)}% confidence
                      </span>
                    </div>

                    {/* Glass-box: Reasoning Summary */}
                    {agent.reasoning_summary && (
                      <p className="text-xs text-muted-foreground bg-muted/50 rounded p-2 mb-2 italic">
                        "{agent.reasoning_summary}"
                      </p>
                    )}

                    <div className="flex gap-4 text-xs">
                      <span className="text-purple-600 dark:text-purple-400">
                        {agent.observations_count} observations
                      </span>
                      <span className="text-green-600 dark:text-green-400">
                        {agent.auto_corrections_count} auto-fixed
                      </span>
                      <span className="text-amber-600 dark:text-amber-400">
                        {agent.review_items_count} for review
                      </span>
                    </div>

                    {(agent.time_seconds || agent.cost_cents) && (
                      <div className="flex gap-4 text-xs text-muted-foreground mt-1">
                        {agent.time_seconds && <span>{agent.time_seconds.toFixed(1)}s</span>}
                        {agent.cost_cents && <span>${(agent.cost_cents / 100).toFixed(4)}</span>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    )
  }

  // Legacy Agents format (backward compatibility)
  if (hasLegacyData && legacyAgentsPhase) {
    const severityColors: Record<string, string> = {
      critical: 'text-red-600 dark:text-red-400',
      high: 'text-orange-600 dark:text-orange-400',
      medium: 'text-yellow-600 dark:text-yellow-400',
      low: 'text-blue-600 dark:text-blue-400',
    }

    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <RefreshCcw className="h-5 w-5" />
              Phase 3: Refine
            </CardTitle>
            <PhaseStatusBadge status={legacyAgentsPhase.status} />
          </div>
          <CardDescription>Visual-vs-markup discrepancy detection (legacy format)</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Agent Summary */}
          <div className="flex items-center gap-4">
            <div className="text-center">
              <span className="text-2xl font-bold text-purple-600 dark:text-purple-400">
                {legacyAgentsPhase.observation_count}
              </span>
              <p className="text-xs text-muted-foreground">observations</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {legacyAgentsPhase.agents_run.map((agent) => (
                <Badge key={agent} variant="secondary">
                  {agent.replace(/_/g, ' ')}
                </Badge>
              ))}
            </div>
          </div>

          {/* Observations List */}
          {legacyAgentsPhase.observations.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Observations</p>
              <div className="max-h-64 overflow-y-auto space-y-2">
                {legacyAgentsPhase.observations.map((obs) => (
                  <div key={obs.id} className="border rounded p-3 text-sm">
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs">
                          {obs.agent}
                        </Badge>
                        <span className={`font-medium ${severityColors[obs.severity] || ''}`}>
                          {obs.severity}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant={obs.route === 'auto' ? 'default' : 'secondary'}>
                          {obs.route}
                        </Badge>
                        <span className="text-muted-foreground">
                          {(obs.confidence * 100).toFixed(0)}%
                        </span>
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
          {showRaw && legacyAgentsPhase.raw_observations && (
            <JsonViewer
              data={legacyAgentsPhase.raw_observations}
              title="Raw Observations"
              keyFields={['id', 'agent', 'severity']}
            />
          )}
        </CardContent>
      </Card>
    )
  }

  return null
}

// ============================================================================
// Phase 4: Assemble (was Remediation)
// ============================================================================
function AssemblePhaseCard({ phase, showRaw }: { phase?: RemediationPhase; showRaw: boolean }) {
  if (!phase || phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Wrench className="h-5 w-5" />
              Phase 4: Assemble
            </CardTitle>
            <PhaseStatusBadge status={phase?.status || 'skipped'} />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">No assembly data available</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <Wrench className="h-5 w-5" />
            Phase 4: Assemble
          </CardTitle>
          <PhaseStatusBadge status={phase.status} />
        </div>
        <CardDescription>Apply auto-corrections and generate final markdown</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Correction Summary */}
        <div className="flex items-center gap-4">
          <div className="text-center">
            <span className="text-2xl font-bold text-purple-600 dark:text-purple-400">
              {phase.auto_correction_count}
            </span>
            <p className="text-xs text-muted-foreground">total</p>
          </div>
          <div className="text-center">
            <span className="text-2xl font-bold text-green-600 dark:text-green-400">
              {phase.applied_count}
            </span>
            <p className="text-xs text-muted-foreground">applied</p>
          </div>
          <div className="text-center">
            <span className="text-2xl font-bold text-amber-600 dark:text-amber-400">
              {phase.pending_count}
            </span>
            <p className="text-xs text-muted-foreground">pending</p>
          </div>
        </div>

        {/* Corrections List */}
        {phase.auto_corrections.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Auto-Corrections</p>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {phase.auto_corrections.map((corr) => (
                <div key={corr.id} className="border rounded p-3 text-sm">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="capitalize text-xs">
                        {corr.agent.replace(/_/g, ' ')}
                      </Badge>
                      <Badge variant={corr.applied ? 'default' : 'secondary'}>
                        {corr.applied ? 'Applied' : 'Pending'}
                      </Badge>
                      {corr.page_num && (
                        <span className="text-muted-foreground text-xs">Page {corr.page_num}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {/* Phase 5: Observation link */}
                      {corr.observation_id && (
                        <span
                          className="text-xs text-purple-600 dark:text-purple-400 font-mono"
                          title={`Observation: ${corr.observation_id}`}
                        >
                          obs:{corr.observation_id.slice(0, 8)}
                        </span>
                      )}
                      <span
                        className={
                          corr.confidence >= 0.95
                            ? 'text-green-600 dark:text-green-400'
                            : 'text-muted-foreground'
                        }
                      >
                        {(corr.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded p-2">
                      <p className="font-medium text-red-700 dark:text-red-300 mb-1">Search:</p>
                      <code className="break-all">{corr.search_preview || '(empty)'}</code>
                    </div>
                    <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded p-2">
                      <p className="font-medium text-green-700 dark:text-green-300 mb-1">Replace:</p>
                      <code className="break-all">{corr.replace_preview || '(empty)'}</code>
                    </div>
                  </div>
                  <p className="text-[10px] text-muted-foreground/70 mt-1 leading-snug line-clamp-2">
                    {corr.justification}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Raw Corrections */}
        {showRaw && phase.raw_corrections && (
          <JsonViewer
            data={phase.raw_corrections}
            title="Raw Corrections"
            keyFields={['id', 'agent', 'applied']}
          />
        )}
      </CardContent>
    </Card>
  )
}

// ============================================================================
// Phase 5: Verify (Final verification against source images)
// ============================================================================
function VerifyPhaseCard({ phase }: { phase?: VerificationPhase }) {
  if (!phase || phase.status === 'skipped') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5" />
              Phase 5: Verify
            </CardTitle>
            <PhaseStatusBadge status={phase?.status || 'skipped'} />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">No verification data available</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            Phase 5: Verify
          </CardTitle>
          <PhaseStatusBadge status={phase.status} />
        </div>
        <CardDescription>Final verification against source page images</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Summary Stats */}
        <div className="flex items-center gap-4">
          <div className="text-center">
            <span className="text-2xl font-bold text-green-600 dark:text-green-400">
              {phase.corrections_applied}
            </span>
            <p className="text-xs text-muted-foreground">corrections applied</p>
          </div>
          <div className="text-center">
            <span className="text-2xl font-bold text-red-600 dark:text-red-400">
              {phase.corrections_failed}
            </span>
            <p className="text-xs text-muted-foreground">failed</p>
          </div>
          <div className="text-center">
            <span className="text-2xl font-bold text-amber-600 dark:text-amber-400">
              {phase.issues_found}
            </span>
            <p className="text-xs text-muted-foreground">issues found</p>
          </div>
          <div className="ml-auto">
            <Badge
              className={
                phase.all_pages_accurate
                  ? 'bg-green-500 text-white'
                  : 'bg-amber-500 text-white'
              }
            >
              {phase.all_pages_accurate ? 'All Pages Accurate' : 'Issues Detected'}
            </Badge>
          </div>
        </div>

        {/* Page Results */}
        {phase.page_results.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Page-by-Page Results</p>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {phase.page_results.map((pr) => (
                <div key={pr.page_num} className="border rounded p-3 text-sm">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">Page {pr.page_num}</span>
                      <Badge
                        variant={pr.is_accurate ? 'default' : 'secondary'}
                        className={pr.is_accurate ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300' : ''}
                      >
                        {pr.is_accurate ? 'Accurate' : 'Has Issues'}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {pr.corrections_applied > 0 && (
                        <span className="text-green-600 dark:text-green-400">
                          +{pr.corrections_applied} fixed
                        </span>
                      )}
                      {pr.corrections_failed > 0 && (
                        <span className="text-red-600 dark:text-red-400">
                          {pr.corrections_failed} failed
                        </span>
                      )}
                      {pr.issues_count > 0 && (
                        <span className="text-amber-600 dark:text-amber-400">
                          {pr.issues_count} issues
                        </span>
                      )}
                    </div>
                  </div>
                  {pr.summary && (
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                      {pr.summary}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Cost */}
        {phase.cost_cents > 0 && (
          <div className="flex gap-4 text-xs text-muted-foreground border-t pt-2">
            <span>Pages verified: {phase.total_pages}</span>
            <span>Cost: ${(phase.cost_cents / 100).toFixed(4)}</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ============================================================================
// Main Page Component
// ============================================================================
export function ProcessingPhasesPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const showRaw = true // Always show raw data
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
            <Loader2 className="h-12 w-12 text-muted-foreground mx-auto mb-4 animate-spin" />
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
            <h2 className="text-xl font-bold text-destructive mb-2">Unable to Load Phases</h2>
            <p className="text-muted-foreground mb-4">
              {error instanceof Error ? error.message : 'Failed to load processing phases'}
            </p>
            <Button onClick={() => refetch()}>Retry</Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Backward-compatible data access: prefer Phase 5 names, fall back to legacy
  const analyzePhase = phases.analyze || phases.analysis
  const extractPhase = phases.extract || phases.extraction
  const refinePhase = phases.refine
  const legacyAgentsPhase = phases.agents
  const assemblePhase = phases.assemble || phases.remediation
  const verifyPhase = phases.verification

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
            <h1 className="text-3xl font-bold text-primary">Processing Phases</h1>
            <p className="text-muted-foreground">
              {phases.filename} &middot; {formatDate(phases.created_at)}
            </p>
          </div>
        </div>
        <Badge variant={phases.status === 'completed' ? 'default' : 'secondary'}>
          {phases.status}
        </Badge>
      </div>

      {/* Tabs for each phase - 5 phases + All */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsList className="grid w-full grid-cols-6">
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="analyze">Analyze</TabsTrigger>
          <TabsTrigger value="extract">Extract</TabsTrigger>
          <TabsTrigger value="refine">Refine</TabsTrigger>
          <TabsTrigger value="assemble">Assemble</TabsTrigger>
          <TabsTrigger value="verify">Verify</TabsTrigger>
        </TabsList>

        <TabsContent value="all" className="space-y-4">
          <AnalyzePhaseCard phase={analyzePhase} showRaw={showRaw} />
          <ExtractPhaseCard phase={extractPhase} />
          <RefinePhaseCard
            refinePhase={refinePhase}
            legacyAgentsPhase={legacyAgentsPhase}
            showRaw={showRaw}
          />
          <AssemblePhaseCard phase={assemblePhase} showRaw={showRaw} />
          <VerifyPhaseCard phase={verifyPhase} />
        </TabsContent>

        <TabsContent value="analyze">
          <AnalyzePhaseCard phase={analyzePhase} showRaw={showRaw} />
        </TabsContent>

        <TabsContent value="extract">
          <ExtractPhaseCard phase={extractPhase} />
        </TabsContent>

        <TabsContent value="refine">
          <RefinePhaseCard
            refinePhase={refinePhase}
            legacyAgentsPhase={legacyAgentsPhase}
            showRaw={showRaw}
          />
        </TabsContent>

        <TabsContent value="assemble">
          <AssemblePhaseCard phase={assemblePhase} showRaw={showRaw} />
        </TabsContent>

        <TabsContent value="verify">
          <VerifyPhaseCard phase={verifyPhase} />
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
                <span className="font-mono">
                  {phases.total_llm_cost.input_tokens.toLocaleString()}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">Output tokens:</span>{' '}
                <span className="font-mono">
                  {phases.total_llm_cost.output_tokens.toLocaleString()}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">Total cost:</span>{' '}
                <span className="font-mono">
                  ${phases.total_llm_cost.estimated_cost_dollars.toFixed(4)}
                </span>
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
