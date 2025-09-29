# PRD-005: Approval Service & UI

## Overview
**Epic**: MVP PDF Converter Human Approval Workflow
**Phase**: 2 - Core Services
**Estimated Effort**: 3 days
**Dependencies**: PRD-001 (Infrastructure), PRD-002 (Data Models)
**Parallel**: ✅ Independent service

## Problem Statement
The system needs a manual approval workflow for documents containing PII. This includes both a backend service that processes approval decisions and a simple web interface where you can review PII findings and make approve/deny decisions with justification.

## Success Criteria
- [ ] Web interface displays PII findings for review
- [ ] Approval/denial endpoints with justification
- [ ] Secure token-based approval URLs
- [ ] Job routing to processing queue on approval
- [ ] Job cleanup and status updates on denial
- [ ] Simple, functional UI for decision making

## Technical Requirements

### Backend Service Architecture

#### Approval Processing Service
```python
# Main service endpoints
POST /approve/{token}     # Submit approval decision
GET /review/{token}       # Display PII findings for review
GET /health              # Health check endpoint

# Worker processes approval queue decisions
async def approval_worker_main():
    """Processes approval decisions and routes jobs"""
    while True:
        # Listen for manual approval decisions
        decision = await get_pending_decision()
        if decision:
            await process_approval_decision(decision)
```

#### Decision Processing Logic
```python
async def process_approval_decision(decision: ApprovalDecision):
    """Process manual approval/denial decision"""
    job = await get_job_from_token(decision.approval_token)

    if decision.approved:
        # Route to processing queue
        processing_payload = ProcessingQueuePayload(
            job_id=job.job_id,
            s3_key=job.s3_key,
            approved_at=datetime.utcnow()
        )

        await redis.lpush(PROCESSING_QUEUE, processing_payload.json())
        await update_job_status(
            job.job_id,
            "processing",
            approval_decision=decision
        )

    else:
        # Cleanup and mark denied
        await cleanup_job_files(job.s3_key)
        await update_job_status(
            job.job_id,
            "denied",
            approval_decision=decision
        )

    # Remove from timeout tracking
    await redis.zrem(APPROVAL_TIMEOUTS, job.job_id)
```

### Frontend: React + Vite + ShadCN UI

#### Separate Frontend Application
Create a modern React application using Vite and ShadCN UI components for the approval interface:

- **Framework**: Vite + React + TypeScript
- **UI Library**: ShadCN UI (accessible components built on Radix)
- **Styling**: Tailwind CSS
- **API Communication**: Fetch API for REST endpoints

#### Frontend Features
- Document upload interface
- Real-time job status tracking
- PII findings review with highlighting
- Approval/denial workflow with justification
- Processing results display

#### API-First Backend
The approval service becomes a pure REST API (no HTML templates):
- `GET /api/jobs/{job_id}` - Get job status and PII findings
- `POST /api/jobs/{job_id}/approve` - Submit approval decision
- `GET /api/jobs` - List jobs awaiting approval

#### API Endpoints
```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Literal

app = FastAPI()

class ApprovalDecision(BaseModel):
    decision: Literal["approved", "denied"]
    justification: str

@app.get("/api/jobs/{job_id}")
async def get_job_details(job_id: str):
    """Get job details including PII findings"""
    job = await get_job_from_id(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    return {
        "job_id": job.job_id,
        "status": job.status,
        "pii_findings": job.pii_findings,
        "created_at": job.created_at,
        "expires_at": job.expires_at
    }

@app.post("/api/jobs/{job_id}/approve")
async def submit_approval(job_id: str, decision: ApprovalDecision):
    """Submit approval decision for job"""
    # Process the approval decision
    await process_approval_decision(job_id, decision)
    return {"message": "Decision processed successfully"}

@app.get("/api/jobs")
async def list_pending_jobs():
    """List all jobs awaiting approval"""
    return await get_jobs_by_status("awaiting_approval")
```

## Acceptance Criteria

### 1. Review Interface
- [ ] Displays all PII findings with context
- [ ] Shows confidence scores and entity types
- [ ] Highlights high-risk PII (SSN, Credit Cards)
- [ ] Provides approve/deny radio buttons
- [ ] Requires justification text field
- [ ] Confirms decision before submission

### 2. Decision Processing
- [ ] Validates approval tokens and expiration
- [ ] Processes approved jobs to processing queue
- [ ] Cleans up denied jobs and temp files
- [ ] Updates job status with decision details
- [ ] Removes jobs from timeout tracking
- [ ] Handles invalid/expired tokens gracefully

### 3. Security
- [ ] Approval URLs use secure random tokens
- [ ] Tokens expire after configured timeout
- [ ] No sensitive data in URLs or logs
- [ ] CSRF protection on form submissions
- [ ] Input validation and sanitization

### 4. User Experience
- [ ] Clear, simple interface design
- [ ] Mobile-responsive layout
- [ ] Confirmation dialogs for decisions
- [ ] Success/error feedback messages
- [ ] Handles browser back button gracefully

### 5. Integration
- [ ] Connects to Redis for job data
- [ ] Updates job status correctly
- [ ] Communicates with S3 for cleanup
- [ ] Works with Docker networking
- [ ] Health checks for orchestration

## Deliverables

### Files to Create
```
/services/approval-service/           # Backend API
├── Dockerfile
├── pyproject.toml
├── app/
│   ├── main.py                      # FastAPI REST API
│   ├── routers/
│   │   ├── approval.py              # Approval endpoints
│   │   └── jobs.py                  # Job management endpoints
│   ├── services/
│   │   ├── job_service.py           # Job management
│   │   ├── decision_service.py      # Decision processing
│   │   └── cleanup_service.py       # File cleanup
├── tests/
│   ├── test_approval_flow.py        # Approval workflow tests
│   ├── test_api.py                  # API endpoint tests
│   └── test_security.py             # Security tests

/frontend/approval-ui/               # Frontend React app
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── components.json                  # ShadCN config
├── src/
│   ├── components/
│   │   ├── ui/                      # ShadCN components
│   │   ├── JobList.tsx
│   │   ├── PIIReview.tsx
│   │   ├── ApprovalForm.tsx
│   │   └── DocumentUpload.tsx
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── JobDetail.tsx
│   │   └── ApprovalReview.tsx
│   ├── hooks/
│   │   ├── useJobs.ts
│   │   └── useApi.ts
│   ├── lib/
│   │   ├── api.ts
│   │   └── utils.ts
│   ├── App.tsx
│   └── main.tsx
├── tests/
│   ├── components/
│   └── integration/
└── docs/
    └── frontend_setup.md
```

### Container Configuration
```dockerfile
FROM python:3.12-slim

# Install uv
RUN pip install uv

WORKDIR /app

# Copy pyproject.toml and install dependencies
COPY pyproject.toml ./
RUN uv pip install --system -e .

COPY app/ ./app/

EXPOSE 3000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
```

## Technical Notes

### Token Security
```python
import secrets
import hashlib
from datetime import datetime, timedelta

def generate_approval_token() -> str:
    """Generate cryptographically secure approval token"""
    return secrets.token_urlsafe(32)

async def validate_approval_token(token: str) -> Optional[JobStatus]:
    """Validate token and return associated job"""
    # Find job by approval token
    job_keys = await redis.keys("eq-pdf:job:*")

    for job_key in job_keys:
        job_data = await redis.hgetall(job_key)
        if job_data.get("approval_token") == token:
            # Check expiration
            expires_at = datetime.fromisoformat(job_data["expires_at"])
            if datetime.utcnow() < expires_at:
                return JobStatus.parse_obj(job_data)
            else:
                # Clean up expired job
                await cleanup_expired_job(job_data["job_id"])
                return None

    return None
```

### Job Cleanup
```python
async def cleanup_job_files(s3_key: str):
    """Clean up S3 files for denied jobs"""
    try:
        await s3_client.delete_object(Bucket=S3_TEMP_BUCKET, Key=s3_key)
        logger.info(f"Cleaned up S3 file: {s3_key}")
    except Exception as e:
        logger.error(f"Failed to cleanup S3 file {s3_key}: {e}")
        # Don't fail the whole operation for cleanup errors
```

### Environment Configuration
```python
# Environment variables required
REDIS_URL=redis://redis:6379
AWS_ENDPOINT_URL=http://localstack:4566
S3_TEMP_BUCKET=equalify-temp

# Queue names
APPROVAL_QUEUE_NAME=eq-pdf:queue:approval
PROCESSING_QUEUE_NAME=eq-pdf:queue:processing

# Service settings
APPROVAL_SERVICE_PORT=3000
APPROVAL_TIMEOUT_HOURS=4
BASE_URL=http://localhost:3000

# Security settings
SESSION_SECRET_KEY=your-secret-key-here
CSRF_PROTECTION=true
```

### Frontend Package Configuration
```json
// package.json
{
  "name": "approval-ui",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "@radix-ui/react-alert-dialog": "^1.0.5",
    "@radix-ui/react-button": "^1.0.4",
    "@radix-ui/react-card": "^1.0.4",
    "@radix-ui/react-form": "^0.0.3",
    "@radix-ui/react-textarea": "^1.0.4",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.0.0",
    "tailwind-merge": "^2.0.0",
    "lucide-react": "^0.294.0"
  },
  "devDependencies": {
    "@types/react": "^18.2.43",
    "@types/react-dom": "^18.2.17",
    "@typescript-eslint/eslint-plugin": "^6.14.0",
    "@typescript-eslint/parser": "^6.14.0",
    "@vitejs/plugin-react": "^4.2.1",
    "autoprefixer": "^10.4.16",
    "eslint": "^8.55.0",
    "eslint-plugin-react-hooks": "^4.6.0",
    "eslint-plugin-react-refresh": "^0.4.5",
    "postcss": "^8.4.32",
    "tailwindcss": "^3.3.6",
    "typescript": "^5.2.2",
    "vite": "^5.0.8"
  }
}
```

### Python Dependencies (pyproject.toml)
```toml
[project]
name = "approval-service"
version = "0.1.0"
description = "Approval service for PDF converter"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "redis",
    "boto3",
    "python-multipart"
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## Definition of Done
- [ ] Web interface renders PII findings correctly
- [ ] Approval/denial decisions process successfully
- [ ] Jobs route to correct queues based on decisions
- [ ] Security tokens work and expire properly
- [ ] Container builds and runs successfully
- [ ] Integration tests with Redis pass
- [ ] UI works on desktop and mobile
- [ ] Service ready for processing worker integration