import { Link, useLocation } from 'react-router-dom'
import { Home, Activity } from 'lucide-react'
import { cn } from '@/lib/utils'

const navigation = [
  { name: 'Dashboard', href: '/', icon: Home },
  { name: 'Monitoring', href: '/monitoring', icon: Activity },
]

export function Sidebar() {
  const location = useLocation()

  return (
    <aside className="w-64 bg-sidebar min-h-screen border-r border-sidebar-border">
      <nav className="p-4 space-y-2">
        {navigation.map((item) => {
          const isActive = location.pathname === item.href
          const Icon = item.icon

          return (
            <Link
              key={item.name}
              to={item.href}
              className={cn(
                'flex items-center gap-3 px-4 py-3 rounded-lg transition-colors font-medium',
                isActive
                  ? 'bg-sidebar-primary text-white'
                  : 'text-sidebar-foreground hover:bg-sidebar-accent'
              )}
            >
              <Icon className="h-5 w-5" />
              <span>{item.name}</span>
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}
