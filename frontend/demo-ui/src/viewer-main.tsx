import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { V5ViewerPage } from '@/pages/V5ViewerPage'
import './index.css'

// Standalone V5 Pipeline Viewer app
// Served at /viewer with minimal dependencies

const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={basename}>
      <Routes>
        <Route path="/*" element={<V5ViewerPage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
