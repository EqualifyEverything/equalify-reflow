import { useState } from 'react'
import { motion } from 'framer-motion'
import { DocumentUpload } from '@/components/document/DocumentUpload'
import { JobList } from '@/components/document/JobList'

export function Dashboard() {
  const [latestJobId, setLatestJobId] = useState<string | null>(null)

  return (
    <div className="space-y-8">
      {/* Hero section */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="text-center mb-8"
      >
        <h1 className="text-4xl md:text-5xl font-bold text-uic-blue mb-4 tracking-tight">
          Equalify PDF Tool
        </h1>
        <p className="text-xl text-gray-600 font-light max-w-2xl mx-auto">
          Transform PDF documents into{' '}
          <span className="font-semibold text-uic-blue">accessible, semantic markup</span>{' '}
          for UIC course materials.
        </p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.1 }}
      >
        <DocumentUpload onUploadSuccess={setLatestJobId} />
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.2 }}
      >
        <JobList latestJobId={latestJobId} />
      </motion.div>
    </div>
  )
}
