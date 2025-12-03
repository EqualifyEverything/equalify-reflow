import { useState } from 'react'
import { cn } from '@/lib/utils'
import { ChevronDown, ChevronRight, Copy, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface JsonViewerProps {
  data: unknown
  title?: string
  defaultExpanded?: boolean
  keyFields?: string[]
  className?: string
}

export function JsonViewer({
  data,
  title = 'API Response',
  defaultExpanded = false,
  keyFields = ['job_id', 'status', 'error'],
  className
}: JsonViewerProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [copied, setCopied] = useState(false)

  const jsonString = JSON.stringify(data, null, 2)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(jsonString)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Extract key fields for summary
  const keyInfo = keyFields
    .filter(key => data && typeof data === 'object' && key in data)
    .map(key => ({
      key,
      value: (data as Record<string, unknown>)[key]
    }))

  return (
    <div className={cn('border rounded-lg bg-slate-50', className)}>
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-slate-100 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-slate-500" />
          ) : (
            <ChevronRight className="h-4 w-4 text-slate-500" />
          )}
          <span className="text-sm font-medium text-slate-700">{title}</span>
        </div>

        {/* Key info badges */}
        <div className="flex items-center gap-2">
          {!expanded && keyInfo.map(({ key, value }) => (
            <span
              key={key}
              className={cn(
                'text-xs px-2 py-0.5 rounded font-mono',
                key === 'status' && typeof value === 'string' ? getStatusClasses(value) : 'bg-slate-200 text-slate-600'
              )}
            >
              {key === 'status' ? String(value) : `${key}: ${truncate(String(value), 12)}`}
            </span>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={(e) => {
              e.stopPropagation()
              handleCopy()
            }}
          >
            {copied ? (
              <Check className="h-3 w-3 text-green-500" />
            ) : (
              <Copy className="h-3 w-3 text-slate-400" />
            )}
          </Button>
        </div>
      </div>

      {/* Expanded JSON */}
      {expanded && (
        <div className="border-t">
          <pre className="p-3 text-xs font-mono overflow-x-auto max-h-96 overflow-y-auto">
            <code>
              {formatJson(data)}
            </code>
          </pre>
        </div>
      )}
    </div>
  )
}

// Syntax highlighting for JSON
function formatJson(data: unknown): React.ReactNode {
  const json = JSON.stringify(data, null, 2)
  const lines = json.split('\n')

  return lines.map((line, i) => (
    <div key={i} className="leading-5">
      {highlightLine(line)}
    </div>
  ))
}

function highlightLine(line: string): React.ReactNode {
  // Match different JSON parts
  const keyMatch = line.match(/^(\s*)"([^"]+)"(:)/)
  const stringMatch = line.match(/:\s*"([^"]*)"(,?)$/)

  if (keyMatch) {
    const [, indent, key, colon] = keyMatch
    const rest = line.slice(keyMatch[0].length)
    return (
      <>
        <span>{indent}</span>
        <span className="text-purple-600">"{key}"</span>
        <span>{colon}</span>
        {highlightValue(rest)}
      </>
    )
  }

  // Standalone value (array item)
  if (stringMatch && !keyMatch) {
    return <span className="text-green-600">{line}</span>
  }

  return <span className="text-slate-600">{line}</span>
}

function highlightValue(value: string): React.ReactNode {
  const trimmed = value.trim()

  if (trimmed.startsWith('"')) {
    return <span className="text-green-600">{value}</span>
  }
  if (/^\d/.test(trimmed) || /^-\d/.test(trimmed)) {
    return <span className="text-blue-600">{value}</span>
  }
  if (trimmed.startsWith('true') || trimmed.startsWith('false')) {
    return <span className="text-orange-600">{value}</span>
  }
  if (trimmed.startsWith('null')) {
    return <span className="text-red-600">{value}</span>
  }

  return <span className="text-slate-600">{value}</span>
}

function getStatusClasses(status: string): string {
  switch (status) {
    case 'completed':
      return 'bg-green-100 text-green-700'
    case 'processing':
    case 'pii_scanning':
      return 'bg-blue-100 text-blue-700'
    case 'awaiting_approval':
    case 'awaiting_correction_approval':
      return 'bg-yellow-100 text-yellow-700'
    case 'failed':
    case 'denied':
      return 'bg-red-100 text-red-700'
    default:
      return 'bg-slate-200 text-slate-600'
  }
}

function truncate(str: string, len: number): string {
  return str.length > len ? str.slice(0, len) + '...' : str
}

// Simplified version for inline use
export function JsonViewerInline({ data }: { data: unknown }) {
  return <JsonViewer data={data} defaultExpanded={false} className="mt-2" />
}
