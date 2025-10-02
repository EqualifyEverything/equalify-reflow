import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Activity, CheckCircle2 } from 'lucide-react'

const workers = [
  { name: 'PII Worker', description: 'Scans documents for PII' },
  { name: 'Processing Worker', description: 'AI enhancement pipeline' },
  { name: 'Timeout Worker', description: 'Cleanup and maintenance' },
]

export function WorkerStatus() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-uic-navy flex items-center gap-2">
          <Activity className="h-5 w-5" />
          Worker Status
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {workers.map((worker) => (
          <div key={worker.name} className="flex items-center justify-between">
            <div>
              <p className="font-medium">{worker.name}</p>
              <p className="text-xs text-muted-foreground">{worker.description}</p>
            </div>
            <Badge className="bg-green-500 text-white flex items-center gap-1">
              <CheckCircle2 className="h-3 w-3" />
              Running
            </Badge>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
