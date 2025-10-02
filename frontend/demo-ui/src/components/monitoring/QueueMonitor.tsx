import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Activity } from 'lucide-react'
import { useQueueMetrics } from '@/hooks/useQueueMetrics'

export function QueueMonitor() {
  const { data: metrics } = useQueueMetrics()

  if (!metrics) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Queue Depths
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Dev monitoring endpoints not available
          </p>
        </CardContent>
      </Card>
    )
  }

  const chartData = [
    { name: 'PII Scan', depth: metrics.queues.pii_scan },
    { name: 'Processing', depth: metrics.queues.processing },
    { name: 'Approval', depth: metrics.queues.approval_pending },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-uic-navy flex items-center gap-2">
          <Activity className="h-5 w-5" />
          Queue Depths
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Bar dataKey="depth" fill="#d50032" />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
