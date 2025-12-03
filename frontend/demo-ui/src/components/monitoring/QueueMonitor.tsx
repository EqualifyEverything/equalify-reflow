import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Activity, ChevronDown, ChevronRight, Shield, Cog, UserCheck, RefreshCw } from 'lucide-react'
import { useQueueMetrics } from '@/hooks/useQueueMetrics'
import { cn } from '@/lib/utils'

interface QueueSectionProps {
  name: string
  count: number
  icon: React.ReactNode
  color: string
  description: string
}

function QueueSection({ name, count, icon, color, description }: QueueSectionProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border rounded-lg overflow-hidden">
      <div
        className={cn(
          'flex items-center justify-between p-3 cursor-pointer hover:bg-slate-50 transition-colors',
          count > 0 && 'bg-slate-50'
        )}
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-slate-400" />
          ) : (
            <ChevronRight className="h-4 w-4 text-slate-400" />
          )}
          <div className={cn('p-2 rounded-lg', color)}>
            {icon}
          </div>
          <div>
            <p className="font-medium text-sm">{name}</p>
            <p className="text-xs text-muted-foreground">{description}</p>
          </div>
        </div>
        <Badge
          className={cn(
            'text-lg px-3 py-1',
            count > 0 ? 'bg-uic-red text-white' : 'bg-slate-200 text-slate-500'
          )}
        >
          {count}
        </Badge>
      </div>

      {expanded && (
        <div className="border-t bg-slate-50 p-3">
          {count === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-2">
              Queue is empty
            </p>
          ) : (
            <p className="text-sm text-muted-foreground text-center py-2">
              {count} job{count !== 1 ? 's' : ''} waiting to be processed
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export function QueueMonitor() {
  const { data: metrics, isLoading, refetch, dataUpdatedAt } = useQueueMetrics()
  const [showChart, setShowChart] = useState(false)

  const totalJobs = metrics
    ? metrics.queues.pii_scan + metrics.queues.processing + metrics.queues.approval_pending
    : 0

  if (!metrics && !isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Queue Monitor
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Dev monitoring endpoints not available. Make sure ENVIRONMENT=dev is set.
          </p>
        </CardContent>
      </Card>
    )
  }

  const chartData = metrics ? [
    { name: 'PII Scan', depth: metrics.queues.pii_scan },
    { name: 'Processing', depth: metrics.queues.processing },
    { name: 'Approval', depth: metrics.queues.approval_pending },
  ] : []

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : 'Never'

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-uic-navy flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Queue Monitor
            {totalJobs > 0 && (
              <Badge className="bg-uic-red text-white ml-2">
                {totalJobs} active
              </Badge>
            )}
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              Updated: {lastUpdated}
            </span>
            <button
              onClick={() => refetch()}
              className="p-1 hover:bg-slate-100 rounded transition-colors"
              title="Refresh"
            >
              <RefreshCw className={cn('h-4 w-4 text-slate-400', isLoading && 'animate-spin')} />
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {metrics && (
          <>
            {/* Queue sections */}
            <QueueSection
              name="PII Scan Queue"
              count={metrics.queues.pii_scan}
              icon={<Shield className="h-4 w-4 text-blue-600" />}
              color="bg-blue-100"
              description="Scanning documents for sensitive information"
            />
            <QueueSection
              name="Processing Queue"
              count={metrics.queues.processing}
              icon={<Cog className="h-4 w-4 text-purple-600" />}
              color="bg-purple-100"
              description="AI text correction in progress"
            />
            <QueueSection
              name="Approval Pending"
              count={metrics.queues.approval_pending}
              icon={<UserCheck className="h-4 w-4 text-yellow-600" />}
              color="bg-yellow-100"
              description="Waiting for human review"
            />

            {/* Toggle chart view */}
            <button
              onClick={() => setShowChart(!showChart)}
              className="text-xs text-muted-foreground hover:text-slate-700 transition-colors w-full text-center py-2"
            >
              {showChart ? 'Hide chart' : 'Show chart'}
            </button>

            {showChart && (
              <div className="pt-2">
                <ResponsiveContainer width="100%" height={150}>
                  <BarChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                    <YAxis allowDecimals={false} />
                    <Tooltip />
                    <Bar dataKey="depth" fill="#d50032" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </>
        )}

        {isLoading && !metrics && (
          <div className="flex items-center justify-center py-8">
            <RefreshCw className="h-6 w-6 text-slate-400 animate-spin" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// Compact version for sidebar
export function QueueMonitorCompact() {
  const { data: metrics } = useQueueMetrics()

  if (!metrics) return null

  const total = metrics.queues.pii_scan + metrics.queues.processing + metrics.queues.approval_pending

  return (
    <div className="flex items-center gap-2 text-xs">
      <Activity className="h-3 w-3" />
      <span>Queues:</span>
      <Badge variant="outline" className="text-xs py-0">
        {total} jobs
      </Badge>
    </div>
  )
}
