import { useState, type FormEvent, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { apiFetch } from './apiFetch'
import { useAuth } from './AuthContext'
import type { Identity } from './types'

export function LoginPage() {
  const { config, identity, setIdentity } = useAuth()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const next = searchParams.get('next') || '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Already logged in → bounce to next.
  useEffect(() => {
    if (identity) {
      navigate(next, { replace: true })
    }
  }, [identity, next, navigate])

  // AUTH_MODE=none → there's nothing to log into; send the user home.
  useEffect(() => {
    if (config && config.mode === 'none') {
      navigate('/', { replace: true })
    }
  }, [config, navigate])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return
    setError(null)
    setSubmitting(true)
    try {
      const res = await apiFetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!res.ok) {
        setError(res.status === 401 ? 'Invalid username or password.' : 'Login failed.')
        return
      }
      const id = (await res.json()) as Identity
      setIdentity(id)
      navigate(next, { replace: true })
    } catch {
      setError('Network error. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  if (!config) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    )
  }

  if (config.mode === 'oidc') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background px-4">
        <section className="w-full max-w-sm space-y-6 rounded-lg border border-border bg-card p-8 shadow-lg">
          <BrandHeader />
          <h1 className="sr-only">Sign in to Equalify Reflow</h1>
          <ul className="space-y-3">
            {config.providers.map((provider) => {
              const url = new URL(provider.login_url, window.location.origin)
              url.searchParams.set('next', next)
              return (
                <li key={provider.id}>
                  <Button
                    asChild
                    variant="primary"
                    size="lg"
                    className="w-full"
                  >
                    <a href={url.toString()}>{provider.display_name}</a>
                  </Button>
                </li>
              )
            })}
          </ul>
        </section>
      </main>
    )
  }

  // Basic mode form.
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <section className="w-full max-w-sm space-y-6 rounded-lg border border-border bg-card p-8 shadow-lg">
        <BrandHeader />
        <h1 className="sr-only">Sign in to Equalify Reflow</h1>
        <form className="space-y-4" onSubmit={handleSubmit} noValidate>
          <label className="block space-y-1">
            <span className="text-sm font-medium">Username</span>
            <Input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              disabled={submitting}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">Password</span>
            <Input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={submitting}
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="w-full"
            disabled={submitting || !username || !password}
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </section>
    </main>
  )
}

// Mirrors the header on PipelineViewerPage so the brand reads continuous
// between login and the rest of the app. UIC logo is conditional on the same
// VITE_SHOW_UIC_LOGO flag the post-login header reads.
function BrandHeader() {
  return (
    <div className="flex items-center justify-center gap-2 text-uic-blue">
      {import.meta.env.VITE_SHOW_UIC_LOGO === 'true' && (
        <img
          src="/uic-logo.png"
          alt="University of Illinois Chicago"
          className="h-8 w-8"
        />
      )}
      <span className="text-xl font-bold">Equalify Reflow</span>
      <span className="text-[10px] font-semibold uppercase tracking-wider bg-uic-blue/10 text-uic-blue border border-uic-blue/20 px-2 py-0.5 rounded-full">
        Beta
      </span>
    </div>
  )
}
