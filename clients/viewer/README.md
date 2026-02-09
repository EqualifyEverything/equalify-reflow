# Equalify PDF Converter - Demo UI

**⚠️ IMPORTANT: This is a DEMO/DEVELOPER TOOL only, NOT the production interface.**

## Overview

Browser-based demo interface for testing the Equalify PDF Converter REST API and demonstrating document processing workflows to stakeholders. The production interface will use Canvas LMS integration.

**Purpose:**
- Developer testing during implementation
- Stakeholder presentations and demos
- QA validation of API functionality
- Visual debugging of document processing pipeline

## Tech Stack

- **Framework**: Vite 6.3 + React 18.3 + TypeScript 5.9
- **Package Manager**: pnpm 9.0+ (latest versions installed)
- **UI Components**: ShadCN UI (Radix + Tailwind CSS)
- **State Management**: React Query 5.90 + Zustand 5.0
- **Charts**: Recharts 2.15
- **Icons**: Lucide React 0.462
- **Styling**: Tailwind CSS 3.4 with UIC branding

## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- Backend API running (`make dev` in project root)

### Option 1: Docker (Recommended)

```bash
# From project root, start all services including demo UI
make dev

# The demo UI will be available at:
# http://localhost:5173

# API runs at:
# http://localhost:8080
```

The frontend runs in a Docker container with hot-reload enabled. Edit files in `frontend/demo-ui/src/` and changes will appear automatically in the browser.

### Option 2: Local Development (Without Docker)

```bash
cd frontend/demo-ui

# Install pnpm if not installed
npm install -g pnpm

# Install dependencies (latest versions)
pnpm install

# Create .env file
cp .env.example .env

# Start dev server
pnpm run dev

# Open browser
open http://localhost:5173
```

## Features

### 1. Document Upload & Tracking
- Drag-and-drop PDF upload interface
- Real-time job status updates (polling every 2 seconds)
- Job list with status badges
- Job detail view with comprehensive information

### 2. PII Review Workflow
- Token-based approval URLs
- Visual display of PII findings with confidence scores
- Approve/deny decision form with justification
- High-confidence PII highlighted in red

### 3. System Monitoring Dashboard
- System health indicators (API, Redis, S3)
- Worker status display (PII, Processing, Timeout workers)
- Queue depth visualization (if dev endpoints enabled)
- Real-time metrics updates

### 4. UIC Branding
- Navy (#001e62) and red (#d50032) color scheme
- "DEMO ONLY" badge prominently displayed
- Accessible UI components (WCAG 2.1 AA)
- Mobile-responsive design

## Project Structure

```
frontend/demo-ui/
├── src/
│   ├── components/
│   │   ├── ui/                # ShadCN primitives (Button, Card, etc.)
│   │   ├── layout/            # Header, Sidebar, DashboardLayout
│   │   ├── document/          # Upload, JobList, JobCard, JobDetail
│   │   └── monitoring/        # SystemHealth, QueueMonitor, WorkerStatus
│   │
│   ├── pages/
│   │   ├── Dashboard.tsx      # Main page (upload + job list)
│   │   ├── JobDetailPage.tsx  # Single job view
│   │   ├── ApprovalReviewPage.tsx  # PII review interface
│   │   └── MonitoringPage.tsx # System monitoring dashboard
│   │
│   ├── hooks/
│   │   ├── useJob.ts          # Job status polling
│   │   ├── useSystemHealth.ts # Health check polling
│   │   └── useQueueMetrics.ts # Queue depth polling
│   │
│   ├── lib/
│   │   ├── api.ts             # Typed API client
│   │   ├── utils.ts           # Helper functions
│   │   └── queryClient.ts     # React Query configuration
│   │
│   └── types/
│       └── api.ts             # TypeScript API types
│
├── Dockerfile.dev             # Docker configuration
├── vite.config.ts             # Vite configuration
├── tailwind.config.js         # UIC branding colors
└── package.json               # Dependencies
```

## API Endpoints Used

**Document Management:**
- `POST /api/documents/submit` - Upload PDF
- `GET /api/documents/{job_id}` - Get job status
- `GET /api/documents/{job_id}/result` - Get processing results

**Approval Workflow:**
- `GET /api/approval/{token}/review` - Get PII review data
- `POST /api/approval/{token}/decision` - Submit approval decision

**System Health:**
- `GET /health` - System health check

**Dev Monitoring (Optional):**
- `GET /api/dev/monitoring/queues` - Queue depth metrics (dev-only)

## Demo Script (For Stakeholders)

### Scenario 1: Clean Document (No PII)

1. **Upload Document**
   - Navigate to Dashboard
   - Click "Upload PDF" and select a clean document
   - Watch status change: `pending` → `pii_scanning` → `processing` → `completed`

2. **View Results**
   - Click on job card to view details
   - Click "View Results" to see processed output

### Scenario 2: Document with PII

1. **Upload Document with PII**
   - Upload a document containing names, emails, or SSNs
   - Status changes to `awaiting_approval`
   - Note the PII detection message

2. **Review PII**
   - Copy approval URL from API response (or check logs)
   - Open URL in new tab
   - Review detected PII entities with confidence scores
   - High-risk PII (>80% confidence) highlighted in red

3. **Approve/Deny**
   - Select "Approve" or "Deny"
   - Enter reviewer name
   - Add justification (10-1000 characters)
   - Submit decision
   - Watch job status update

### Scenario 3: System Monitoring

1. **View Health Dashboard**
   - Navigate to "Monitoring" page
   - Verify all systems show green (API, Redis, S3)
   - Check worker status (all should show "Running")

2. **Monitor Queue Activity**
   - Upload multiple documents
   - Watch queue depths increase in bar chart
   - Observe real-time updates every 2 seconds

## Development

### Hot Reload

The Docker setup includes hot-reload for rapid development:

```bash
# Edit any file in frontend/demo-ui/src/
# Browser automatically refreshes

# If changes don't appear, try:
docker-compose restart demo-ui
```

### Building for Production

```bash
pnpm run build

# Output in dist/ directory
# Can be served with any static host
```

### TypeScript Type Checking

```bash
pnpm run build  # Runs tsc before build
```

## Environment Variables

Create `.env` file (see `.env.example`):

```bash
# API Configuration
VITE_API_URL=http://localhost:8080

# Optional: Grafana URL (if PRD-009A complete)
VITE_GRAFANA_URL=http://localhost:3001
```

**Important:** In Docker, `VITE_API_URL` must be `http://localhost:8080` because the browser (not the container) makes API requests.

## Accessibility

- WCAG 2.1 AA compliant components (Radix UI)
- Keyboard navigation supported
- Screen reader compatible
- Color contrast validated
- Focus indicators visible

## Mobile Responsive

Tested on:
- Desktop (1920x1080+)
- Tablet (768x1024)
- Phone (375x667)

All layouts adapt gracefully.

## Troubleshooting

### Frontend not loading

```bash
# Check if container is running
docker ps | grep demo-ui

# View logs
docker logs equalify-pdf-demo-ui

# Restart container
docker-compose restart demo-ui
```

### API requests failing

```bash
# Verify API is running
curl http://localhost:8080/health

# Check CORS configuration
# Should allow localhost:5173 origin
```

### Hot reload not working

```bash
# Verify volume mounts
docker inspect equalify-pdf-demo-ui

# Should see mounts for src/, public/, etc.
```

## Known Limitations

1. **Queue Monitoring**: Requires optional dev endpoints (PRD-009B)
2. **Grafana Embedding**: Requires PRD-009A implementation
3. **Job Persistence**: Jobs not persisted between API restarts
4. **Authentication**: None (dev-only tool)

## Future Enhancements

- [ ] Grafana dashboard embedding (PRD-009A)
- [ ] Job history persistence
- [ ] Document preview before upload
- [ ] Export job list to CSV
- [ ] Dark mode toggle

## Support

This is a demo tool for internal use. For issues:

1. Check Docker logs: `make logs`
2. Verify backend API is healthy: `curl http://localhost:8080/health`
3. Review browser console for errors
4. Check network tab for failed requests

## Related Documentation

- [PRD-009B](../../ai-docs/PRDs/phase-3-integration/PRD-009B-demo-rest-ui.md) - Demo UI requirements
- [PRD-004](../../ai-docs/PRDs/phase-2-services/PRD-004-api-endpoints.md) - API endpoints
- [PRD-006](../../ai-docs/PRDs/phase-2-services/PRD-006-approval-api.md) - Approval workflow

---

**Remember: This is a DEMO ONLY interface for testing and presentations. Production will use Canvas LMS integration.**
