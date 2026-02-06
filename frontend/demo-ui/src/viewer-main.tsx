import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ViewerPage } from '@/pages/ViewerPage'
import { PipelineViewerPage } from '@/pages/PipelineViewerPage'
import './index.css'

// Standalone Pipeline Viewer app
// Served at /viewer with minimal dependencies

const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={basename}>
      <Routes>
        <Route path="/minimal" element={<PipelineViewerPage />} />
        <Route path="/*" element={<ViewerPage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
