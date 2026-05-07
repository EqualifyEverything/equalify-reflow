// Single thin wrapper around fetch for all viewer → /api/* calls.
//
// Behaviour:
//   - credentials: 'include' so the session cookie travels on every request.
//   - On state-changing methods, echo the X-CSRF-Token header from the
//     reflow_session_csrf cookie (NOT HttpOnly so we can read it here).
//   - On 401, capture pathname+search and redirect to /login?next=…. Skipped
//     for /api/v1/auth/* itself so login attempts surface the error inline.
//
// Returns the raw Response so callers using ReadableStream (SSE) keep working.

const API_URL = import.meta.env.VITE_API_URL ?? ''

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

function readCookie(name: string): string | null {
  const prefix = `${name}=`
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim()
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length))
    }
  }
  return null
}

export async function apiFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const url = input.startsWith('http') ? input : `${API_URL}${input}`
  const method = (init.method ?? 'GET').toUpperCase()

  const headers = new Headers(init.headers)
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie('reflow_session_csrf')
    if (csrf && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrf)
    }
  }

  const response = await fetch(url, {
    ...init,
    method,
    credentials: 'include',
    headers,
  })

  if (
    response.status === 401 &&
    typeof window !== 'undefined' &&
    !url.includes('/api/v1/auth/')
  ) {
    const next = encodeURIComponent(window.location.pathname + window.location.search)
    // Only redirect if we're not already on /login — avoids a redirect loop
    // when the login page itself triggers a 401-bearing api call.
    if (!window.location.pathname.startsWith('/login')) {
      window.location.assign(`/login?next=${next}`)
    }
  }

  return response
}
