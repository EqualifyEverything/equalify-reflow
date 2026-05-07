import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { PipelineViewerPage } from '@/pages/PipelineViewerPage'
import { MinimalPage } from '@/pages/MinimalPage'
import { AuthProvider, RequireAuth } from '@/auth/AuthContext'
import { LoginPage } from '@/auth/LoginPage'
import { CallbackPage } from '@/auth/CallbackPage'
import './index.css'

// Pipeline Viewer app
// Served at the root (/) by FastAPI in both dev and prod. basename is
// derived from Vite's base config via import.meta.env.BASE_URL.

const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={basename}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/auth/callback" element={<CallbackPage />} />
          <Route
            path="/minimal"
            element={
              <RequireAuth>
                <MinimalPage />
              </RequireAuth>
            }
          />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <PipelineViewerPage />
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
