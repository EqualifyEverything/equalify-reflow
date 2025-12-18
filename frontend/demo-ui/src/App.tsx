import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { DashboardLayout } from '@/components/layout/DashboardLayout'
import { Dashboard } from '@/pages/Dashboard'
import { JobDetailPage } from '@/pages/JobDetailPage'
import { ApprovalReviewPage } from '@/pages/ApprovalReviewPage'
import { CorrectionReviewPage } from '@/pages/CorrectionReviewPage'
import { ProcessingPhasesPage } from '@/pages/ProcessingPhasesPage'
import { ReviewChecklistPage } from '@/pages/ReviewChecklistPage'
import { queryClient } from '@/lib/queryClient'

// Base path for routing - must match Vite's base config
const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={basename}>
        <Routes>
          {/* Routes with dashboard layout */}
          <Route
            path="/"
            element={
              <DashboardLayout>
                <Dashboard />
              </DashboardLayout>
            }
          />
          <Route
            path="/job/:jobId"
            element={
              <DashboardLayout>
                <JobDetailPage />
              </DashboardLayout>
            }
          />
          {/* PII Approval review - standalone page (no sidebar) */}
          <Route
            path="/review/:token"
            element={
              <div className="min-h-screen bg-uic-light-gray">
                <header className="bg-uic-navy text-white py-4 px-6 mb-6">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-white rounded-full flex items-center justify-center">
                      <span className="text-uic-navy font-bold text-xl">UIC</span>
                    </div>
                    <h1 className="text-2xl font-bold text-white">
                      Equalify PDF Converter - PII Review
                    </h1>
                  </div>
                </header>
                <div className="p-6">
                  <ApprovalReviewPage />
                </div>
              </div>
            }
          />

          {/* Correction review - with dashboard layout */}
          <Route
            path="/corrections/:jobId/review"
            element={
              <DashboardLayout>
                <CorrectionReviewPage />
              </DashboardLayout>
            }
          />

          {/* Processing phases - with dashboard layout */}
          <Route
            path="/job/:jobId/phases"
            element={
              <DashboardLayout>
                <ProcessingPhasesPage />
              </DashboardLayout>
            }
          />

          {/* Human review checklist - with dashboard layout */}
          <Route
            path="/job/:jobId/review"
            element={
              <DashboardLayout>
                <ReviewChecklistPage />
              </DashboardLayout>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
