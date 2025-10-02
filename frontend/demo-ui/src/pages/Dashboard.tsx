import { useState } from 'react'
import { DocumentUpload } from '@/components/document/DocumentUpload'
import { JobList } from '@/components/document/JobList'

export function Dashboard() {
  const [latestJobId, setLatestJobId] = useState<string | null>(null)

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-uic-navy mb-2">Dashboard</h1>
        <p className="text-muted-foreground">
          Upload PDF documents for accessibility conversion
        </p>
      </div>

      <DocumentUpload onUploadSuccess={setLatestJobId} />
      <JobList latestJobId={latestJobId} />
    </div>
  )
}
