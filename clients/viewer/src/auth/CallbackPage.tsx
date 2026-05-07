// OIDC callback intermediate. The backend's /api/v1/auth/callback/{provider_id}
// handler does the real work: validate state, exchange code, set cookies, then
// 302 to the original ?next path. This page only renders if a redirect chain
// somehow lands the browser here directly (refresh after the cookie set, etc.)
// — it surfaces a friendly message and bounces home.
//
// Lands fully in PR2 alongside the OIDC provider; included now as a stub so
// the route shape is reserved.

import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export function CallbackPage() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/', { replace: true })
  }, [navigate])

  return (
    <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
      Completing sign-in…
    </div>
  )
}
