# PRD-009B: Demo REST API Testing UI

## Overview
**Epic**: Developer Tools & Stakeholder Demo
**Phase**: 3 - Integration & Demo
**Estimated Effort**: 8-12 hours
**Dependencies**: PRD-004 (API Endpoints), PRD-006 (Approval API), PRD-009A (Grafana - optional but recommended)
**Blocks**: PRD-010 (End-to-End Integration)
**Can Run in Parallel With**: PRD-009A (Grafana Stack)

## Problem Statement

Currently, the PDF converter API can only be tested via:
- **cURL commands**: Tedious, not stakeholder-friendly
- **Postman/Insomnia**: External tools, no branding, not integrated
- **OpenAPI docs**: Good for technical docs, poor for demos

**Key Issues:**
1. **No visual workflow demonstration** for stakeholders
2. **Difficult to debug** document processing pipeline visually
3. **No real-time job monitoring** without manual API polling
4. **Cannot demonstrate PII review workflow** interactively
5. **No system observability UI** for queues, workers, health

**Important:** This is a **development/demo tool only**, NOT the production UIC interface. Production will use Canvas LMS integration. This UI exists for:
- Developer testing during implementation
- Stakeholder presentations and demos
- QA validation of API functionality
- Debugging document processing issues

## Success Criteria
- [ ] Complete document workflow (upload → status → approval → results) in browser
- [ ] Real-time job status updates without manual refresh
- [ ] Visual PII review and approval interface
- [ ] System monitoring dashboard (queues, health, workers)
- [ ] Mobile-responsive for presentations on tablets/phones
- [ ] UIC branding with navy (#001e62) and red (#d50032) colors
- [ ] Accessible UI (WCAG 2.1 AA compliant components)
- [ ] Docker-integrated (runs in same network as API)
- [ ] Zero backend code changes (pure frontend addition)
- [ ] Clear "DEMO ONLY" branding (not mistaken for production)

## Technical Requirements

### Tech Stack Decision

**Framework**: Vite + React + TypeScript
- **Why Vite**: Instant hot reload, faster than Create React App
- **Why React**: Component reusability, large ecosystem
- **Why TypeScript**: Type safety for API contracts

**UI Library**: ShadCN UI (Radix + Tailwind)
- **Why ShadCN**: Copy-paste components (not npm dependency hell)
- **Why Radix**: Accessibility built-in (WCAG 2.1 AA)
- **Why Tailwind**: UIC color customization with OKLCH

**State Management**: React Query + Zustand
- **React Query**: Perfect for API polling, caching, refetching
- **Zustand**: Lightweight global state (better than Context for dashboards)

**Data Visualization**: Recharts (ShadCN Charts)
- Pre-built chart components
- Responsive design
- Theme-aware

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Docker Compose Network                     │
│                                                             │
│  ┌──────────────┐            ┌──────────────┐              │
│  │ demo-ui      │────HTTP───►│ api-gateway  │              │
│  │ :5173        │  (fetch)   │ :8000        │              │
│  │              │            │              │              │
│  │ React        │            │ FastAPI      │              │
│  │ Vite dev     │            │ /api/*       │              │
│  └──────────────┘            └──────────────┘              │
│        │                                                    │
│        │ (optional)                                         │
│        │                                                    │
│        └────────────────────►┌──────────────┐              │
│                              │ grafana      │              │
│                              │ :3000        │              │
│                              │              │              │
│                              │ (embedded)   │              │
│                              └──────────────┘              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
         │                              │
         │                              │
         ▼                              ▼
    localhost:5173              localhost:8080
    (User's browser)           (API requests from browser)
```

**Key Insight:** Frontend runs in Docker BUT user accesses it in browser at `localhost:5173`. Browser makes API calls to `localhost:8080` (which Docker exposes).

### Application Structure

```
/frontend/demo-ui/
├── Dockerfile.dev                     # Docker container for hot reload
├── package.json                       # Dependencies
├── vite.config.ts                     # Vite configuration
├── tsconfig.json                      # TypeScript config
├── tailwind.config.js                 # UIC branding colors
├── components.json                    # ShadCN configuration
├── .env.example                       # Environment template
├── README.md                          # Setup + demo script
│
├── public/
│   ├── uic-logo.svg                   # UIC branding
│   └── favicon.ico
│
├── src/
│   ├── main.tsx                       # App entry point
│   ├── App.tsx                        # Root component + routing
│   ├── index.css                      # Global styles + UIC tokens
│   │
│   ├── components/
│   │   ├── ui/                        # ShadCN primitives (generated)
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── badge.tsx
│   │   │   ├── input.tsx
│   │   │   ├── alert.tsx
│   │   │   ├── radio-group.tsx
│   │   │   ├── textarea.tsx
│   │   │   ├── label.tsx
│   │   │   ├── table.tsx
│   │   │   └── chart.tsx
│   │   │
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx            # Navigation sidebar
│   │   │   ├── Header.tsx             # Top header with "DEMO" badge
│   │   │   └── DashboardLayout.tsx    # Main layout wrapper
│   │   │
│   │   ├── document/
│   │   │   ├── DocumentUpload.tsx     # Drag-drop upload
│   │   │   ├── JobList.tsx            # Real-time job list
│   │   │   ├── JobCard.tsx            # Single job display
│   │   │   ├── JobDetail.tsx          # Detailed job view
│   │   │   ├── PIIReview.tsx          # PII findings list
│   │   │   ├── ApprovalForm.tsx       # Approve/deny form
│   │   │   └── ResultsViewer.tsx      # Processing output
│   │   │
│   │   └── monitoring/
│   │       ├── SystemHealth.tsx       # API/Redis/S3 status
│   │       ├── QueueMonitor.tsx       # Queue depth charts
│   │       ├── WorkerStatus.tsx       # Worker health indicators
│   │       ├── JobAnalytics.tsx       # Job stats (total, rate, etc.)
│   │       └── GrafanaEmbed.tsx       # Embedded Grafana iframe (optional)
│   │
│   ├── pages/
│   │   ├── Dashboard.tsx              # Main view (upload + jobs)
│   │   ├── JobDetailPage.tsx          # Single job view
│   │   ├── ApprovalReviewPage.tsx     # PII approval (token-based URL)
│   │   ├── MonitoringPage.tsx         # System monitoring dashboard
│   │   └── ResultsPage.tsx            # Processing results viewer
│   │
│   ├── hooks/
│   │   ├── useJobs.ts                 # React Query job polling
│   │   ├── useJob.ts                  # Single job polling
│   │   ├── useQueueMetrics.ts         # Queue depth polling
│   │   ├── useSystemHealth.ts         # Health check polling
│   │   └── useApi.ts                  # Generic API wrapper
│   │
│   ├── lib/
│   │   ├── api.ts                     # Typed API client
│   │   ├── utils.ts                   # Helper functions
│   │   └── queryClient.ts             # React Query config
│   │
│   └── types/
│       ├── api.ts                     # API response types
│       ├── job.ts                     # Job model types
│       └── monitoring.ts              # Monitoring types
│
└── tests/
    └── components/
        ├── DocumentUpload.test.tsx
        └── PIIReview.test.tsx
```

### UIC Branding Configuration

**Tailwind Config (tailwind.config.js):**
```javascript
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // UIC Brand Colors
        'uic-navy': '#001e62',
        'uic-red': '#d50032',
        'uic-light-gray': '#f8f8f8',

        // OKLCH Semantic Colors
        background: 'oklch(0 0 0)',
        foreground: 'oklch(1 0 0)',

        card: {
          DEFAULT: 'oklch(1 0 0)',
          foreground: 'oklch(0.1 0 0)',
        },

        primary: {
          DEFAULT: 'oklch(0.1 0 0)',  // Near black
          foreground: 'oklch(1 0 0)',
        },

        secondary: {
          DEFAULT: 'oklch(0.95 0 0)',
          foreground: 'oklch(0.1 0 0)',
        },

        muted: {
          DEFAULT: 'oklch(0.95 0 0)',
          foreground: 'oklch(0.4 0 0)',
        },

        accent: {
          DEFAULT: 'oklch(0.95 0 0)',
          foreground: 'oklch(0.1 0 0)',
        },

        destructive: {
          DEFAULT: 'oklch(0.577 0.245 27.325)',
          foreground: 'oklch(1 0 0)',
        },

        border: 'oklch(0 0 0 / 10%)',
        input: 'oklch(0 0 0 / 10%)',
        ring: 'oklch(0.4 0 0)',

        // Chart colors
        chart: {
          '1': 'oklch(0.488 0.243 264.376)',
          '2': 'oklch(0.696 0.17 162.48)',
          '3': 'oklch(0.769 0.188 70.08)',
          '4': 'oklch(0.627 0.265 303.9)',
          '5': 'oklch(0.645 0.246 16.439)',
        },
      },

      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },

      borderRadius: {
        lg: '0.625rem',  // 10px
        md: '0.5rem',    // 8px
        sm: '0.375rem',  // 6px
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
```

**Global Styles (src/index.css):**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap');

@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --radius: 0.625rem;
  }

  body {
    @apply bg-uic-light-gray text-foreground font-sans;
  }

  h1, h2, h3, h4, h5, h6 {
    @apply text-uic-navy font-bold;
  }

  a {
    @apply text-uic-red hover:underline font-semibold;
  }
}

@layer components {
  .demo-badge {
    @apply bg-uic-red text-white px-3 py-1 rounded-full text-sm font-bold uppercase tracking-wide;
  }

  .uic-divider {
    @apply h-1 bg-uic-red max-w-32 mx-auto;
  }
}
```

### API Client Implementation

**src/lib/api.ts:**
```typescript
/**
 * Typed API client for Equalify PDF Converter API.
 *
 * All routes verified against OpenAPI spec:
 * - POST /api/documents/submit
 * - GET /api/documents/{job_id}/status
 * - GET /api/documents/{job_id}/result
 * - GET /api/approval/review/{token}
 * - POST /api/approval/{token}/approve
 * - GET /health
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';

// Types (from OpenAPI spec)
export interface JobSubmissionResponse {
  job_id: string;
  status: string;
  message: string;
  approval_url?: string;  // If PII detected
}

export interface JobStatus {
  job_id: string;
  status: 'pending' | 'processing' | 'awaiting_approval' | 'complete' | 'failed' | 'denied';
  created_at: string;
  updated_at: string;
  pii_detected?: boolean;
  error?: string;
}

export interface JobResult {
  job_id: string;
  status: string;
  output_url?: string;
  processing_time?: number;
  confidence_score?: number;
  error?: string;
}

export interface PIIReviewData {
  job_id: string;
  document_filename: string;
  pii_findings: Array<{
    entity_type: string;
    text: string;
    score: number;
    start: number;
    end: number;
  }>;
  detected_at: string;
}

export interface ApprovalDecision {
  decision: 'approved' | 'denied';
  justification: string;
  reviewed_by: string;
}

export interface HealthStatus {
  status: string;
  redis: boolean;
  s3: boolean;
  timestamp: string;
}

// API Client
class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  // Document submission
  async submitDocument(file: File): Promise<JobSubmissionResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${this.baseUrl}/api/documents/submit`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Upload failed');
    }

    return response.json();
  }

  // Job status
  async getJobStatus(jobId: string): Promise<JobStatus> {
    const response = await fetch(`${this.baseUrl}/api/documents/${jobId}/status`);

    if (!response.ok) {
      throw new Error('Failed to fetch job status');
    }

    return response.json();
  }

  // Job result
  async getJobResult(jobId: string): Promise<JobResult> {
    const response = await fetch(`${this.baseUrl}/api/documents/${jobId}/result`);

    if (!response.ok) {
      throw new Error('Failed to fetch job result');
    }

    return response.json();
  }

  // PII review data
  async getReviewData(token: string): Promise<PIIReviewData> {
    const response = await fetch(`${this.baseUrl}/api/approval/review/${token}`);

    if (!response.ok) {
      throw new Error('Invalid or expired token');
    }

    return response.json();
  }

  // Submit approval decision
  async submitApproval(token: string, decision: ApprovalDecision): Promise<{ status: string }> {
    const response = await fetch(`${this.baseUrl}/api/approval/${token}/approve`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(decision),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Approval failed');
    }

    return response.json();
  }

  // System health
  async getHealth(): Promise<HealthStatus> {
    const response = await fetch(`${this.baseUrl}/health`);
    return response.json();
  }
}

export const api = new ApiClient(API_BASE_URL);
```

### React Query Hooks

**src/hooks/useJobs.ts:**
```typescript
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

/**
 * Poll for job status with automatic refetching.
 *
 * Refetch every 2 seconds while job is pending/processing.
 * Stop refetching when complete/failed.
 */
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.getJobStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (data) => {
      // Stop polling if job is terminal state
      if (!data) return false;
      const terminalStates = ['complete', 'failed', 'denied'];
      return terminalStates.includes(data.status) ? false : 2000;
    },
  });
}
```

**src/hooks/useSystemHealth.ts:**
```typescript
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

/**
 * Poll system health every 5 seconds.
 */
export function useSystemHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 5000,
  });
}
```

### Key Components

**src/components/layout/Header.tsx:**
```typescript
import { Badge } from '@/components/ui/badge';

export function Header() {
  return (
    <header className="bg-uic-navy text-white py-4 px-6 flex items-center justify-between">
      <div className="flex items-center gap-4">
        <img src="/uic-logo.svg" alt="UIC" className="h-10" />
        <h1 className="text-2xl font-bold text-white">
          Equalify PDF Converter
        </h1>
        <Badge variant="destructive" className="demo-badge">
          DEMO ONLY
        </Badge>
      </div>
      <div className="text-sm opacity-75">
        Developer Testing Interface
      </div>
    </header>
  );
}
```

**src/components/document/DocumentUpload.tsx:**
```typescript
import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { api } from '@/lib/api';

export function DocumentUpload({ onUploadSuccess }: { onUploadSuccess: (jobId: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleUpload = async () => {
    if (!file) return;

    setIsUploading(true);
    setError(null);

    try {
      const response = await api.submitDocument(file);
      onUploadSuccess(response.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-uic-navy">Upload PDF Document</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <Input
            type="file"
            accept=".pdf"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            disabled={isUploading}
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button
          onClick={handleUpload}
          disabled={!file || isUploading}
          className="bg-uic-red hover:bg-uic-red/90"
        >
          {isUploading ? 'Uploading...' : 'Upload PDF'}
        </Button>
      </CardContent>
    </Card>
  );
}
```

**src/components/document/JobCard.tsx:**
```typescript
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { JobStatus } from '@/lib/api';

const statusColors = {
  pending: 'bg-gray-500',
  processing: 'bg-blue-500',
  awaiting_approval: 'bg-yellow-500',
  complete: 'bg-green-500',
  failed: 'bg-red-500',
  denied: 'bg-red-700',
};

export function JobCard({ job }: { job: JobStatus }) {
  return (
    <Card className="hover:shadow-lg transition-shadow cursor-pointer">
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <p className="font-mono text-sm text-muted-foreground">
              {job.job_id}
            </p>
            <p className="text-xs text-muted-foreground">
              {new Date(job.created_at).toLocaleString()}
            </p>
          </div>

          <div className="flex items-center gap-2">
            {job.pii_detected && (
              <Badge variant="outline" className="border-yellow-500 text-yellow-700">
                PII Detected
              </Badge>
            )}
            <Badge className={statusColors[job.status]}>
              {job.status.replace('_', ' ').toUpperCase()}
            </Badge>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
```

**src/components/monitoring/QueueMonitor.tsx:**
```typescript
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useQuery } from '@tanstack/react-query';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

// Note: This requires dev-only monitoring endpoints from backend
export function QueueMonitor() {
  const { data } = useQuery({
    queryKey: ['queues'],
    queryFn: async () => {
      const response = await fetch('http://localhost:8080/api/dev/monitoring/queues');
      return response.json();
    },
    refetchInterval: 2000,
  });

  if (!data) return null;

  const chartData = [
    { name: 'PII Scan', depth: data.queues.pii_scan },
    { name: 'Processing', depth: data.queues.processing },
    { name: 'Approval', depth: data.queues.approval_pending },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-uic-navy">Queue Depths</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Bar dataKey="depth" fill="#d50032" />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
```

### Docker Integration

**frontend/demo-ui/Dockerfile.dev:**
```dockerfile
FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy source (will be overridden by volume mount in dev)
COPY . .

# Expose Vite dev server
EXPOSE 5173

# Start dev server with host binding for Docker
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

**Add to docker-compose.dev.yml:**
```yaml
services:
  # Demo Frontend - React UI for API testing
  demo-ui:
    build:
      context: ./frontend/demo-ui
      dockerfile: Dockerfile.dev
    container_name: equalify-pdf-demo-ui
    restart: unless-stopped
    ports:
      - "5173:5173"  # Vite dev server
    environment:
      - VITE_API_URL=http://localhost:8080  # Browser makes requests to localhost
      - VITE_GRAFANA_URL=http://localhost:3000
    volumes:
      # Hot reload - mount source code
      - ./frontend/demo-ui/src:/app/src:ro
      - ./frontend/demo-ui/public:/app/public:ro
      - ./frontend/demo-ui/index.html:/app/index.html:ro
    networks:
      - equalify-network
    depends_on:
      api-gateway:
        condition: service_healthy
```

**Important:** The frontend CONTAINER can reach `api-gateway:8000`, but the USER'S BROWSER reaches `localhost:8080`. That's why `VITE_API_URL=http://localhost:8080`.

### Dev-Only Monitoring Endpoints (Backend Addition)

**src/api/dev_monitoring.py (NEW FILE - OPTIONAL):**
```python
"""
Development-only monitoring endpoints.

SECURITY: Only available when ENVIRONMENT=dev
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from ..config import settings
from ..dependencies import get_queue_service, get_job_service

router = APIRouter(prefix="/api/dev", tags=["Development"])


def require_dev_mode() -> None:
    """Ensure endpoint only accessible in development."""
    if settings.environment != "dev":
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/monitoring/queues")
async def get_queue_metrics(
    queue: QueueService = Depends(get_queue_service)
) -> Dict[str, Any]:
    """Get queue depths for development dashboard."""
    require_dev_mode()

    return {
        "queues": {
            "pii_scan": await queue.queue_depth("pii_scan_queue"),
            "processing": await queue.queue_depth("processing_queue"),
            "approval_pending": await queue.queue_depth("approval_pending_queue"),
        },
        "timestamp": datetime.now().isoformat()
    }
```

**Register in src/main.py:**
```python
# Conditionally import dev monitoring
if settings.environment == "dev":
    from .api import dev_monitoring
    app.include_router(dev_monitoring.router)
    logger.info("✅ Dev monitoring endpoints enabled")
```

## Deliverables

### Files to Create

```
/frontend/demo-ui/
├── Dockerfile.dev
├── package.json
├── package-lock.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.js
├── postcss.config.js
├── components.json
├── .env.example
├── .gitignore
├── README.md
│
├── public/
│   ├── uic-logo.svg
│   └── favicon.ico
│
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── index.css
│   │
│   ├── components/
│   │   ├── ui/ (20+ ShadCN components)
│   │   ├── layout/ (Header, Sidebar, DashboardLayout)
│   │   ├── document/ (Upload, JobList, JobCard, PIIReview, etc.)
│   │   └── monitoring/ (SystemHealth, QueueMonitor, WorkerStatus)
│   │
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── JobDetailPage.tsx
│   │   ├── ApprovalReviewPage.tsx
│   │   ├── MonitoringPage.tsx
│   │   └── ResultsPage.tsx
│   │
│   ├── hooks/
│   │   ├── useJobs.ts
│   │   ├── useJob.ts
│   │   ├── useSystemHealth.ts
│   │   └── useQueueMetrics.ts
│   │
│   ├── lib/
│   │   ├── api.ts
│   │   ├── utils.ts
│   │   └── queryClient.ts
│   │
│   └── types/
│       ├── api.ts
│       ├── job.ts
│       └── monitoring.ts

/src/api/
  dev_monitoring.py (OPTIONAL - for queue monitoring UI)

docker-compose.dev.yml (UPDATE - add demo-ui service)
Makefile (UPDATE - add `make demo-ui` target)
```

### Package.json
```json
{
  "name": "equalify-demo-ui",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.21.0",
    "@tanstack/react-query": "^5.17.0",
    "zustand": "^4.4.7",
    "recharts": "^2.10.3",
    "lucide-react": "^0.303.0",
    "clsx": "^2.1.0",
    "tailwind-merge": "^2.2.0",
    "class-variance-authority": "^0.7.0",
    "@radix-ui/react-alert-dialog": "^1.0.5",
    "@radix-ui/react-avatar": "^1.0.4",
    "@radix-ui/react-badge": "^1.0.4",
    "@radix-ui/react-card": "^1.0.4",
    "@radix-ui/react-dropdown-menu": "^2.0.6",
    "@radix-ui/react-label": "^2.0.2",
    "@radix-ui/react-radio-group": "^1.1.3",
    "@radix-ui/react-separator": "^1.0.3",
    "@radix-ui/react-slot": "^1.0.2",
    "@radix-ui/react-table": "^1.0.4",
    "@radix-ui/react-tabs": "^1.0.4",
    "@radix-ui/react-textarea": "^1.0.4",
    "@radix-ui/react-toast": "^1.1.5"
  },
  "devDependencies": {
    "@types/react": "^18.2.46",
    "@types/react-dom": "^18.2.18",
    "@typescript-eslint/eslint-plugin": "^6.17.0",
    "@typescript-eslint/parser": "^6.17.0",
    "@vitejs/plugin-react": "^4.2.1",
    "autoprefixer": "^10.4.16",
    "eslint": "^8.56.0",
    "postcss": "^8.4.33",
    "tailwindcss": "^3.4.1",
    "tailwindcss-animate": "^1.0.7",
    "typescript": "^5.3.3",
    "vite": "^5.0.10"
  }
}
```

## Acceptance Criteria

### 1. Infrastructure
- [ ] Frontend runs in Docker container
- [ ] Hot reload working (code changes update browser)
- [ ] Accessible at http://localhost:5173
- [ ] Can communicate with API at http://localhost:8080
- [ ] Starts with `make dev` or `docker-compose up`

### 2. Document Workflow
- [ ] Upload PDF via drag-drop or file picker
- [ ] Show upload progress indicator
- [ ] Display job ID and status after upload
- [ ] Real-time status updates (polling every 2s)
- [ ] Navigate to job detail view
- [ ] View processing results when complete

### 3. PII Review
- [ ] Access review page via token URL
- [ ] Display all PII findings with scores
- [ ] Highlight high-confidence PII (>0.8) in red
- [ ] Radio selection for approve/deny
- [ ] Justification textarea (required)
- [ ] Submit button triggers approval
- [ ] Show success/error feedback

### 4. System Monitoring
- [ ] System health dashboard (Redis, S3, API)
- [ ] Queue depth monitoring (3 queues)
- [ ] Worker status indicators (3 workers)
- [ ] Job analytics (total, by status, rate)
- [ ] All metrics update in real-time

### 5. UIC Branding
- [ ] Navy (#001e62) header background
- [ ] Red (#d50032) accent colors
- [ ] UIC logo in header
- [ ] "DEMO ONLY" badge visible
- [ ] OKLCH color tokens in Tailwind config
- [ ] Inter font for body text

### 6. Accessibility
- [ ] Keyboard navigation works
- [ ] Screen reader compatible
- [ ] ARIA labels on interactive elements
- [ ] Color contrast meets WCAG 2.1 AA
- [ ] Focus indicators visible

### 7. Mobile Responsive
- [ ] Works on tablets (768px+)
- [ ] Works on phones (375px+)
- [ ] Touch-friendly buttons
- [ ] Readable text without zoom
- [ ] Cards stack properly on small screens

### 8. Documentation
- [ ] README with setup instructions
- [ ] .env.example with API URL
- [ ] Demo script for stakeholders
- [ ] Architecture diagram
- [ ] "DEMO ONLY" disclaimer prominent

## Testing Strategy

### Immediate Verification
```bash
# Start backend
make dev

# Start frontend (in separate terminal)
cd frontend/demo-ui
npm install
npm run dev

# Verify
open http://localhost:5173
# Expected: Dashboard loads with "DEMO ONLY" badge
```

### Functional Testing
```bash
# Test upload flow
1. Click "Upload PDF" on dashboard
2. Select test.pdf
3. Click upload button
4. Verify job card appears
5. Watch status change: pending → processing → complete

# Test PII review
1. Upload document with PII
2. Copy approval URL from API response
3. Open URL in new tab
4. Verify PII findings displayed
5. Select "approve" and add justification
6. Submit and verify success message

# Test monitoring
1. Navigate to "Monitoring" page
2. Verify health indicators show green
3. Upload multiple documents
4. Watch queue depths increase in chart
5. Verify worker status shows all active
```

### Mobile Testing
```bash
# Use browser dev tools
1. Open http://localhost:5173
2. Toggle device toolbar (Cmd+Shift+M)
3. Test iPhone 12 Pro (390x844)
4. Test iPad (768x1024)
5. Verify all UI elements accessible
```

## Definition of Done

- [ ] Frontend runs in Docker with hot reload
- [ ] All 4 pages functional (Dashboard, Job Detail, Approval, Monitoring)
- [ ] Document upload workflow complete
- [ ] PII review workflow functional
- [ ] System monitoring dashboard working
- [ ] UIC branding applied throughout
- [ ] Mobile responsive on tablets/phones
- [ ] Accessibility validated (keyboard nav, ARIA)
- [ ] README with setup + demo script
- [ ] .env.example provided
- [ ] Docker Compose integration complete
- [ ] Zero backend code changes (except optional dev endpoints)
- [ ] "DEMO ONLY" badge prominent
- [ ] No errors in browser console
- [ ] TypeScript builds without errors
- [ ] Stakeholder demo script documented

## Implementation Notes

### Zero Backend Changes
- Frontend is pure addition (no backend modifications)
- Uses existing API endpoints only
- Optional dev monitoring endpoints are OPTIONAL (frontend works without them)

### Development Workflow
```bash
# Terminal 1: Backend
make dev

# Terminal 2: Frontend
cd frontend/demo-ui
npm run dev

# Browser: http://localhost:5173
```

### Performance
- Initial bundle size: ~200KB gzipped
- Lazy load pages with React.lazy()
- React Query caching reduces API calls
- Polling stops when jobs reach terminal state

### Security
- Dev-only tool (never deployed to production)
- Runs in Docker internal network
- No authentication needed (dev environment)
- CORS already configured in backend

### Maintenance
- ShadCN components copy-pasted (no npm updates needed)
- React Query handles all API state
- Tailwind purges unused CSS automatically

## Unblocks

- **PRD-010**: End-to-end integration testing (needs UI for validation)
- **Stakeholder demos**: Visual demonstration of PDF processing
- **QA testing**: Interactive testing of API workflows
- **Developer debugging**: Visual inspection of system state

## References

- [Vite Documentation](https://vitejs.dev/)
- [ShadCN UI](https://ui.shadcn.com/)
- [React Query](https://tanstack.com/query/latest)
- [Tailwind CSS](https://tailwindcss.com/)
- [Radix UI](https://www.radix-ui.com/)
