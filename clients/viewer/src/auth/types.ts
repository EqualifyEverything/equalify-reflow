// Mirrors src/auth/routes.py response shapes. Keep these in lockstep with
// AuthConfigResponse / IdentityResponse on the backend.

export type AuthMode = 'none' | 'basic' | 'oidc'

export interface ProviderInfo {
  id: string
  display_name: string
  login_url: string
}

export interface AuthConfig {
  mode: AuthMode
  providers: ProviderInfo[]
}

export interface Identity {
  sub: string
  email: string | null
  name: string | null
  provider_id: string
  expires_at: string
}
