import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { PipelineViewerPage } from '@/pages/PipelineViewerPage'
import { MinimalPage } from '@/pages/MinimalPage'
import './index.css'

// Pipeline Viewer app
// Served at the root (/) by FastAPI in both dev and prod. basename is
// derived from Vite's base config via import.meta.env.BASE_URL.

const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={basename}>
      <Routes>
        <Route path="/minimal" element={<MinimalPage />} />
        <Route path="/*" element={<PipelineViewerPage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
