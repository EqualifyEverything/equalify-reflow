import { useState, useMemo } from 'react'
import { cn } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ChevronDown, ChevronRight, FileText, Image, ExternalLink } from 'lucide-react'

interface CorrectionDiffProps {
  originalMarkdown: string
  correctedMarkdown: string
  corrections: {
    page: number
    type: string
    original_snippet: string
    corrected_snippet: string
    confidence: number
    explanation: string
    is_auto_applied: boolean
  }[]
  pageImages?: string[]
  className?: string
}

export function CorrectionDiff({
  originalMarkdown,
  correctedMarkdown,
  corrections,
  pageImages = [],
  className
}: CorrectionDiffProps) {
  const [viewMode, setViewMode] = useState<'split' | 'unified'>('split')
  const [showImages, setShowImages] = useState(false)

  // Simple diff: split into lines and highlight differences
  const { originalLines, correctedLines, diffLines } = useMemo(() => {
    const origLines = originalMarkdown.split('\n')
    const corrLines = correctedMarkdown.split('\n')

    // Create unified diff (simplified - just highlights additions/removals)
    const unified: Array<{ type: 'same' | 'removed' | 'added'; content: string }> = []
    const maxLen = Math.max(origLines.length, corrLines.length)

    for (let i = 0; i < maxLen; i++) {
      const orig = origLines[i] ?? ''
      const corr = corrLines[i] ?? ''

      if (orig === corr) {
        unified.push({ type: 'same', content: orig })
      } else {
        if (orig) unified.push({ type: 'removed', content: orig })
        if (corr) unified.push({ type: 'added', content: corr })
      }
    }

    return {
      originalLines: origLines,
      correctedLines: corrLines,
      diffLines: unified
    }
  }, [originalMarkdown, correctedMarkdown])

  return (
    <div className={cn('space-y-4', className)}>
      {/* View controls */}
      <div className="flex items-center justify-between">
        <Tabs value={viewMode} onValueChange={(v) => setViewMode(v as 'split' | 'unified')}>
          <TabsList>
            <TabsTrigger value="split">Side by Side</TabsTrigger>
            <TabsTrigger value="unified">Unified</TabsTrigger>
          </TabsList>
        </Tabs>

        {pageImages.length > 0 && (
          <button
            onClick={() => setShowImages(!showImages)}
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-slate-700"
          >
            <Image className="h-4 w-4" />
            {showImages ? 'Hide' : 'Show'} Page Images ({pageImages.length})
          </button>
        )}
      </div>

      {/* Page images */}
      {showImages && pageImages.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm">Page Images</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {pageImages.map((url, idx) => (
                <a
                  key={idx}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="border rounded-lg overflow-hidden hover:ring-2 ring-uic-navy transition-all"
                >
                  <img
                    src={url}
                    alt={`Page ${idx + 1}`}
                    className="w-full h-auto"
                  />
                  <div className="p-2 bg-slate-50 text-xs text-center">
                    Page {idx + 1}
                    <ExternalLink className="h-3 w-3 inline ml-1" />
                  </div>
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Diff view */}
      {viewMode === 'split' ? (
        <div className="grid grid-cols-2 gap-4">
          {/* Original */}
          <Card>
            <CardHeader className="py-3 bg-red-50">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="h-4 w-4" />
                Original
                <Badge variant="outline" className="ml-auto">
                  {originalLines.length} lines
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <pre className="text-xs font-mono p-4 overflow-auto max-h-96 bg-slate-50">
                {originalLines.map((line, idx) => (
                  <div key={idx} className="leading-5">
                    <span className="text-slate-400 select-none mr-3 w-6 inline-block text-right">
                      {idx + 1}
                    </span>
                    {line || ' '}
                  </div>
                ))}
              </pre>
            </CardContent>
          </Card>

          {/* Corrected */}
          <Card>
            <CardHeader className="py-3 bg-green-50">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="h-4 w-4" />
                Corrected
                <Badge variant="outline" className="ml-auto">
                  {correctedLines.length} lines
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <pre className="text-xs font-mono p-4 overflow-auto max-h-96 bg-slate-50">
                {correctedLines.map((line, idx) => (
                  <div key={idx} className="leading-5">
                    <span className="text-slate-400 select-none mr-3 w-6 inline-block text-right">
                      {idx + 1}
                    </span>
                    {line || ' '}
                  </div>
                ))}
              </pre>
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <FileText className="h-4 w-4" />
              Unified Diff
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <pre className="text-xs font-mono p-4 overflow-auto max-h-96">
              {diffLines.map((line, idx) => (
                <div
                  key={idx}
                  className={cn('leading-5 px-2 -mx-2', {
                    'bg-red-100 text-red-800': line.type === 'removed',
                    'bg-green-100 text-green-800': line.type === 'added',
                  })}
                >
                  <span className="select-none mr-2 text-slate-400">
                    {line.type === 'removed' ? '-' : line.type === 'added' ? '+' : ' '}
                  </span>
                  {line.content || ' '}
                </div>
              ))}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Correction details */}
      {corrections.length > 0 && (
        <CorrectionsList corrections={corrections} />
      )}
    </div>
  )
}

interface CorrectionsListProps {
  corrections: CorrectionDiffProps['corrections']
}

function CorrectionsList({ corrections }: CorrectionsListProps) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)

  // Group by type
  const byType = corrections.reduce((acc, c) => {
    acc[c.type] = (acc[c.type] || 0) + 1
    return acc
  }, {} as Record<string, number>)

  // Count auto vs manual
  const autoCount = corrections.filter(c => c.is_auto_applied).length
  const manualCount = corrections.length - autoCount

  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-sm flex items-center gap-2">
          Corrections ({corrections.length})
          <Badge className="bg-green-100 text-green-700 text-xs ml-2">
            {autoCount} auto
          </Badge>
          <Badge className="bg-amber-100 text-amber-700 text-xs">
            {manualCount} review
          </Badge>
          <div className="flex gap-1 ml-auto">
            {Object.entries(byType).map(([type, count]) => (
              <Badge key={type} variant="outline" className="text-xs">
                {type}: {count}
              </Badge>
            ))}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="divide-y max-h-64 overflow-y-auto">
          {corrections.map((correction, idx) => (
            <div key={idx} className="hover:bg-slate-50">
              <div
                className="flex items-center justify-between p-3 cursor-pointer"
                onClick={() => setExpandedIdx(expandedIdx === idx ? null : idx)}
              >
                <div className="flex items-center gap-3">
                  {expandedIdx === idx ? (
                    <ChevronDown className="h-4 w-4 text-slate-400" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-slate-400" />
                  )}
                  <Badge variant="outline">{correction.type}</Badge>
                  {correction.is_auto_applied ? (
                    <Badge className="bg-green-100 text-green-700 text-xs">
                      Auto
                    </Badge>
                  ) : (
                    <Badge className="bg-amber-100 text-amber-700 text-xs">
                      Review
                    </Badge>
                  )}
                  <span className="text-xs text-muted-foreground">
                    Page {correction.page}
                  </span>
                </div>
                <Badge
                  className={cn(
                    'text-xs',
                    correction.confidence >= 0.9
                      ? 'bg-green-100 text-green-700'
                      : correction.confidence >= 0.7
                      ? 'bg-yellow-100 text-yellow-700'
                      : 'bg-red-100 text-red-700'
                  )}
                >
                  {(correction.confidence * 100).toFixed(0)}%
                </Badge>
              </div>

              {expandedIdx === idx && (
                <div className="px-3 pb-3 space-y-2">
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="bg-red-50 p-2 rounded">
                      <p className="font-medium text-red-700 mb-1">Original</p>
                      <pre className="whitespace-pre-wrap font-mono text-red-800">
                        {correction.original_snippet}
                      </pre>
                    </div>
                    <div className="bg-green-50 p-2 rounded">
                      <p className="font-medium text-green-700 mb-1">Corrected</p>
                      <pre className="whitespace-pre-wrap font-mono text-green-800">
                        {correction.corrected_snippet}
                      </pre>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground italic">
                    {correction.explanation}
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

// Summary card for quick view
export function CorrectionsSummary({
  totalCorrections,
  autoAppliedCount,
  manualReviewCount,
  confidence,
  byType
}: {
  totalCorrections: number
  autoAppliedCount: number
  manualReviewCount: number
  confidence: number
  byType: Record<string, number>
}) {
  return (
    <div className="flex items-center gap-4 p-3 bg-slate-50 rounded-lg">
      <div className="text-center">
        <p className="text-2xl font-bold text-uic-navy">{totalCorrections}</p>
        <p className="text-xs text-muted-foreground">total</p>
      </div>
      <div className="text-center">
        <p className="text-2xl font-bold text-green-600">{autoAppliedCount}</p>
        <p className="text-xs text-muted-foreground">auto-applied</p>
      </div>
      <div className="text-center">
        <p className="text-2xl font-bold text-amber-600">{manualReviewCount}</p>
        <p className="text-xs text-muted-foreground">need review</p>
      </div>
      <div className="text-center">
        <p className={cn(
          'text-2xl font-bold',
          confidence >= 0.9 ? 'text-green-600' : confidence >= 0.7 ? 'text-yellow-600' : 'text-red-600'
        )}>
          {(confidence * 100).toFixed(0)}%
        </p>
        <p className="text-xs text-muted-foreground">confidence</p>
      </div>
      <div className="flex-1 flex flex-wrap gap-1">
        {Object.entries(byType).map(([type, count]) => (
          <Badge key={type} variant="outline" className="text-xs">
            {type}: {count}
          </Badge>
        ))}
      </div>
    </div>
  )
}
