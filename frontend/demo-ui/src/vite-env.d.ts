/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_API_KEY: string
  readonly VITE_GRAFANA_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
