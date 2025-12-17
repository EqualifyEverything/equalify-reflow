import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'

export function Header() {
  return (
    <header className="bg-white border-b border-gray-200">
      {/* Top accent bar (UIC Red) - matches UIC OSF */}
      <div className="bg-uic-red h-2 w-full" />

      <div className="px-6 py-4">
        <div className="flex items-center justify-between">
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.5 }}
            className="flex items-center gap-4"
          >
            {/* UIC Logo circle - links to dashboard */}
            <Link to="/" className="w-12 h-12 bg-uic-blue rounded-full flex items-center justify-center shadow-sm hover:bg-uic-blue/90 transition-colors">
              <span className="text-white font-bold text-lg">UIC</span>
            </Link>

            {/* Divider */}
            <div className="hidden md:block w-px h-8 bg-gray-300" />

            {/* Title - links to dashboard */}
            <Link to="/" className="flex flex-col hover:opacity-80 transition-opacity">
              <h1 className="text-xl font-bold text-uic-blue">
                Equalify PDF Converter
              </h1>
              <span className="text-xs text-gray-500 hidden sm:block">
                Technology Solutions
              </span>
            </Link>

            {/* Demo badge */}
            <span className="demo-badge ml-2">
              Demo
            </span>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.5, delay: 0.1 }}
            className="flex items-center gap-4"
          >
            <span className="text-sm text-gray-600 hidden md:block">
              Developer Testing Interface
            </span>
          </motion.div>
        </div>
      </div>
    </header>
  )
}
