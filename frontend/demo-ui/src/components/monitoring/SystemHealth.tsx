import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { CheckCircle2, XCircle, Activity } from 'lucide-react'
import { useSystemHealth } from '@/hooks/useSystemHealth'

export function SystemHealth() {
  const { data: health, isLoading } = useSystemHealth()

  if (isLoading || !health) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-uic-navy flex items-center gap-2">
            <Activity className="h-5 w-5" />
            System Health
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">Loading...</p>
        </CardContent>
      </Card>
    )
  }

  const services = [
    { name: 'API', status: health.status === 'healthy', key: 'api' },
    { name: 'Redis', status: health.checks.redis, key: 'redis' },
    { name: 'S3', status: health.checks.s3, key: 's3' },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-uic-navy flex items-center gap-2">
          <Activity className="h-5 w-5" />
          System Health
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {services.map((service) => (
          <div key={service.key} className="flex items-center justify-between">
            <span className="font-medium">{service.name}</span>
            {service.status ? (
              <Badge className="bg-green-500 text-white flex items-center gap-1">
                <CheckCircle2 className="h-3 w-3" />
                Healthy
              </Badge>
            ) : (
              <Badge variant="destructive" className="flex items-center gap-1">
                <XCircle className="h-3 w-3" />
                Unhealthy
              </Badge>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
