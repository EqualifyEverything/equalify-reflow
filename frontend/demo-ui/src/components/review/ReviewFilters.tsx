import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  Eye,
  Layers,
  FileText,
  X,
  Filter,
} from 'lucide-react'

export interface FilterState {
  category?: string
  agent?: string
  page?: number
  status?: 'pending' | 'completed'
}

interface ReviewFiltersProps {
  categories: string[]
  agents: string[]
  pages: number[]
  activeFilters: FilterState
  onFilterChange: (filters: FilterState) => void
}

export function ReviewFilters({
  categories,
  agents,
  pages,
  activeFilters,
  onFilterChange,
}: ReviewFiltersProps) {
  const hasActiveFilters =
    activeFilters.category ||
    activeFilters.agent ||
    activeFilters.page ||
    activeFilters.status

  const handleClearFilters = () => {
    onFilterChange({})
  }

  const handleCategoryClick = (category: string) => {
    onFilterChange({
      ...activeFilters,
      category: activeFilters.category === category ? undefined : category,
    })
  }

  const handleAgentClick = (agent: string) => {
    onFilterChange({
      ...activeFilters,
      agent: activeFilters.agent === agent ? undefined : agent,
    })
  }

  const handlePageClick = (page: number) => {
    onFilterChange({
      ...activeFilters,
      page: activeFilters.page === page ? undefined : page,
    })
  }

  const handleStatusClick = (status: 'pending' | 'completed') => {
    onFilterChange({
      ...activeFilters,
      status: activeFilters.status === status ? undefined : status,
    })
  }

  return (
    <div className="space-y-4 bg-slate-50 rounded-lg p-4 border">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <Filter className="h-4 w-4" />
          Filters
        </div>
        {hasActiveFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClearFilters}
            className="text-xs text-muted-foreground"
          >
            <X className="h-3 w-3 mr-1" />
            Clear all
          </Button>
        )}
      </div>

      {/* Status filter */}
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground font-medium">Status</p>
        <div className="flex flex-wrap gap-2">
          <Badge
            variant={activeFilters.status === 'pending' ? 'default' : 'outline'}
            className={cn(
              'cursor-pointer transition-colors',
              activeFilters.status === 'pending'
                ? 'bg-uic-navy'
                : 'hover:bg-slate-100'
            )}
            onClick={() => handleStatusClick('pending')}
          >
            Pending
          </Badge>
          <Badge
            variant={activeFilters.status === 'completed' ? 'default' : 'outline'}
            className={cn(
              'cursor-pointer transition-colors',
              activeFilters.status === 'completed'
                ? 'bg-green-500'
                : 'hover:bg-slate-100'
            )}
            onClick={() => handleStatusClick('completed')}
          >
            Completed
          </Badge>
        </div>
      </div>

      {/* Category filter */}
      {categories.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground font-medium flex items-center gap-1">
            <Layers className="h-3 w-3" />
            By Category
          </p>
          <div className="flex flex-wrap gap-2">
            {categories.map((category) => (
              <Badge
                key={category}
                variant={activeFilters.category === category ? 'default' : 'outline'}
                className={cn(
                  'cursor-pointer transition-colors capitalize',
                  activeFilters.category === category
                    ? 'bg-purple-600'
                    : 'hover:bg-slate-100'
                )}
                onClick={() => handleCategoryClick(category)}
              >
                {category.replace(/_/g, ' ')}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Agent filter */}
      {agents.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground font-medium flex items-center gap-1">
            <Eye className="h-3 w-3" />
            By Agent
          </p>
          <div className="flex flex-wrap gap-2">
            {agents.map((agent) => (
              <Badge
                key={agent}
                variant={activeFilters.agent === agent ? 'default' : 'outline'}
                className={cn(
                  'cursor-pointer transition-colors capitalize',
                  activeFilters.agent === agent
                    ? 'bg-blue-600'
                    : 'hover:bg-slate-100'
                )}
                onClick={() => handleAgentClick(agent)}
              >
                {agent.replace(/_/g, ' ')}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Page filter */}
      {pages.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground font-medium flex items-center gap-1">
            <FileText className="h-3 w-3" />
            By Page
          </p>
          <div className="flex flex-wrap gap-2">
            {pages.sort((a, b) => a - b).map((page) => (
              <Badge
                key={page}
                variant={activeFilters.page === page ? 'default' : 'outline'}
                className={cn(
                  'cursor-pointer transition-colors',
                  activeFilters.page === page
                    ? 'bg-amber-600'
                    : 'hover:bg-slate-100'
                )}
                onClick={() => handlePageClick(page)}
              >
                Page {page}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Active filters summary */}
      {hasActiveFilters && (
        <div className="pt-2 border-t">
          <p className="text-xs text-muted-foreground">
            Active filters:{' '}
            {[
              activeFilters.status && `status: ${activeFilters.status}`,
              activeFilters.category && `category: ${activeFilters.category}`,
              activeFilters.agent && `agent: ${activeFilters.agent}`,
              activeFilters.page && `page: ${activeFilters.page}`,
            ]
              .filter(Boolean)
              .join(', ')}
          </p>
        </div>
      )}
    </div>
  )
}
