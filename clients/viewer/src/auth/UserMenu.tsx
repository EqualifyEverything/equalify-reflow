import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'

import { Button } from '@/components/ui/button'

import { apiFetch } from './apiFetch'
import { useAuth } from './AuthContext'

// Compact identity + sign-out chip for the post-login header. Renders nothing
// when AUTH_MODE=none (no identity to show). Uses apiFetch so the X-CSRF-Token
// header is sourced from the companion cookie automatically; on success the
// browser is bounced to /login (which redirects back to / when AUTH_MODE=none,
// so this stays correct if an operator flips auth off without rebuilding).
export function UserMenu() {
  const { identity, setIdentity } = useAuth()
  const navigate = useNavigate()
  const [signingOut, setSigningOut] = useState(false)

  if (!identity) return null

  const handleSignOut = async () => {
    if (signingOut) return
    setSigningOut(true)
    try {
      const res = await apiFetch('/api/v1/auth/logout', { method: 'POST' })
      // Treat any 2xx as success; cookies are cleared server-side either way.
      if (res.ok) {
        const body = (await res.json().catch(() => ({}))) as { logout_url?: string | null }
        setIdentity(null)
        if (body.logout_url) {
          // PR3 will populate this for OIDC providers that advertise an
          // end_session_endpoint. For now it stays null.
          window.location.href = body.logout_url
          return
        }
        navigate('/login', { replace: true })
      }
    } finally {
      setSigningOut(false)
    }
  }

  const label = identity.name || identity.email || identity.sub

  return (
    <div className="ml-auto flex items-center gap-3 text-sm">
      <span className="hidden sm:inline text-muted-foreground">
        Signed in as <span className="font-medium text-foreground">{label}</span>
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={handleSignOut}
        disabled={signingOut}
        aria-label="Sign out"
      >
        <LogOut className="h-4 w-4 sm:mr-2" aria-hidden="true" />
        <span className="hidden sm:inline">{signingOut ? 'Signing out…' : 'Sign out'}</span>
      </Button>
    </div>
  )
}
