import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { ThemeToggle } from '@/components/ui/ThemeToggle'

export function Header() {
  return (
    <header className="bg-background border-b border-border">
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
            <Link to="/" className="w-12 h-12 bg-uic-red rounded-full flex items-center justify-center shadow-sm hover:bg-uic-red/90 transition-colors">
              <span className="text-white font-bold text-lg">UIC</span>
            </Link>

            {/* Divider */}
            <div className="hidden md:block w-px h-10 bg-border" />

            {/* Technology Solutions - links to dashboard (matches UIC OSF pattern) */}
            <Link to="/" className="hidden md:block text-xl font-bold text-primary hover:underline">
              Technology Solutions
            </Link>

            {/* Demo badge */}
            <span className="demo-badge">
              Demo
            </span>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.5, delay: 0.1 }}
            className="flex items-center gap-4"
          >
            <ThemeToggle className="text-foreground hover:bg-muted" />
            <span className="text-sm text-muted-foreground hidden md:block">
              Developer Testing Interface
            </span>
          </motion.div>
        </div>
      </div>
    </header>
  )
}
