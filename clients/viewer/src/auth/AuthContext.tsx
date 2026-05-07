import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { apiFetch } from './apiFetch'
import type { AuthConfig, Identity } from './types'

interface AuthContextValue {
  // Loading: initial /auth/config fetch in flight.
  loading: boolean
  config: AuthConfig | null
  identity: Identity | null
  refreshIdentity: () => Promise<void>
  // Mark identity present after a successful login from the LoginPage.
  setIdentity: (identity: Identity | null) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth() must be used inside <AuthProvider>')
  }
  return ctx
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true)
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [identity, setIdentity] = useState<Identity | null>(null)

  const refreshIdentity = async () => {
    if (!config || config.mode === 'none') {
      setIdentity(null)
      return
    }
    try {
      const res = await apiFetch('/api/v1/auth/me')
      if (res.ok) {
        setIdentity((await res.json()) as Identity)
      } else {
        setIdentity(null)
      }
    } catch {
      setIdentity(null)
    }
  }

  // Fetch /auth/config on mount; if mode != none, also fetch /me to discover
  // whether the browser already has a valid session.
  useEffect(() => {
    let cancelled = false
    const init = async () => {
      try {
        const res = await apiFetch('/api/v1/auth/config')
        if (cancelled) return
        if (!res.ok) {
          // Treat unreachable config as 'none' — never block the SPA from
          // rendering when the auth subsystem is mis-deployed.
          setConfig({ mode: 'none', providers: [] })
          return
        }
        const cfg = (await res.json()) as AuthConfig
        setConfig(cfg)
        if (cfg.mode !== 'none') {
          const meRes = await apiFetch('/api/v1/auth/me')
          if (!cancelled && meRes.ok) {
            setIdentity((await meRes.json()) as Identity)
          }
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void init()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <AuthContext.Provider
      value={{ loading, config, identity, refreshIdentity, setIdentity }}
    >
      {children}
    </AuthContext.Provider>
  )
}

// Wrapper that gates a route on either AUTH_MODE=none (open) or a present
// identity. Renders children when allowed; otherwise redirects to /login.
export function RequireAuth({ children }: { children: ReactNode }) {
  const { loading, config, identity } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    )
  }

  if (!config || config.mode === 'none') {
    return <>{children}</>
  }

  if (identity === null) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }

  return <>{children}</>
}
