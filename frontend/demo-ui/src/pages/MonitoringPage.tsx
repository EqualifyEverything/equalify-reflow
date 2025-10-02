import { SystemHealth } from '@/components/monitoring/SystemHealth'
import { QueueMonitor } from '@/components/monitoring/QueueMonitor'
import { WorkerStatus } from '@/components/monitoring/WorkerStatus'

export function MonitoringPage() {
  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-uic-navy mb-2">System Monitoring</h1>
        <p className="text-muted-foreground">
          Real-time system health and performance metrics
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <SystemHealth />
        <WorkerStatus />
      </div>

      <QueueMonitor />
    </div>
  )
}
