import { Header } from './Header'

interface DashboardLayoutProps {
  children: React.ReactNode
}

export function DashboardLayout({ children }: DashboardLayoutProps) {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <Header />
      <main className="flex-1 relative overflow-hidden bg-gradient-to-br from-muted/50 to-muted/20 min-h-[calc(100vh-70px)]">
        {/* Decorative blur circles - UIC OSF style */}
        <div className="absolute inset-0 pointer-events-none overflow-hidden" aria-hidden="true">
          <div className="absolute top-[5%] right-[-5%] w-[500px] h-[500px] bg-gradient-to-br from-primary/5 to-transparent rounded-full blur-3xl" />
          <div className="absolute bottom-[5%] left-[-5%] w-[400px] h-[400px] bg-gradient-to-tr from-destructive/5 to-transparent rounded-full blur-3xl" />
          <div className="absolute top-[30%] left-[5%] w-24 h-24 bg-primary/5 rounded-full blur-2xl" />
          <div className="absolute bottom-[30%] right-[10%] w-32 h-32 bg-destructive/5 rounded-full blur-2xl" />
        </div>

        {/* Content */}
        <div className="relative z-10 p-6 py-10">
          <div className="max-w-6xl mx-auto">
            {children}
          </div>
        </div>
      </main>

      {/* Footer accent bar (UIC Blue) - matches UIC OSF */}
      <div className="bg-uic-blue h-2 w-full" />
    </div>
  )
}
