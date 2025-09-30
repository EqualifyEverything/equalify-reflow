# PRD-010: Demo Frontend Application

## Overview
**Epic**: Stakeholder Demo Interface
**Phase**: 3 - Integration & Demo
**Estimated Effort**: 3 days
**Dependencies**: PRD-004 (API Endpoints), PRD-006 (Approval API)

## Problem Statement
We need a demo frontend application for testing and demonstrating the PDF converter API to stakeholders. This is NOT the production interface for UIC - think of it as a better alternative to Postman/Insomnia for demos and stakeholder presentations.

**Important**: This is a demo/testing UI only. The production UIC interface will be integrated differently (Canvas LMS integration). This frontend exists to showcase the API capabilities during development and stakeholder reviews.

## Success Criteria
- [ ] Can demonstrate full document processing workflow to stakeholders
- [ ] Mobile responsive design for presentations on any device
- [ ] Clear, intuitive interface for non-technical stakeholders
- [ ] Real-time job status updates for demo flow
- [ ] PII review workflow demonstration
- [ ] Processing results viewer with accessible output
- [ ] Can run against local API or deployed API

## Technical Requirements

### Tech Stack
- **Framework**: Vite + React + TypeScript
- **UI Library**: ShadCN UI (accessible components built on Radix)
- **Styling**: Tailwind CSS
- **API Communication**: Fetch API for REST endpoints
- **State Management**: React hooks (useState, useEffect)

### Frontend Features

#### 1. Document Upload Interface
```typescript
// src/components/DocumentUpload.tsx
interface DocumentUploadProps {
  onUploadComplete: (jobId: string) => void;
}

export function DocumentUpload({ onUploadComplete }: DocumentUploadProps) {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleUpload = async () => {
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch('http://localhost:8000/api/documents', {
      method: 'POST',
      body: formData
    });

    const data = await response.json();
    onUploadComplete(data.job_id);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload Document</CardTitle>
      </CardHeader>
      <CardContent>
        <Input type="file" accept=".pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <Button onClick={handleUpload} disabled={!file || isUploading}>
          {isUploading ? 'Uploading...' : 'Upload PDF'}
        </Button>
      </CardContent>
    </Card>
  );
}
```

#### 2. Job Status Tracking Display
```typescript
// src/components/JobList.tsx
interface Job {
  job_id: string;
  status: string;
  created_at: string;
  pii_detected: boolean;
}

export function JobList() {
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    // Poll for job updates
    const interval = setInterval(async () => {
      const response = await fetch('http://localhost:8000/api/jobs');
      const data = await response.json();
      setJobs(data.jobs);
    }, 2000);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-4">
      {jobs.map(job => (
        <JobCard key={job.job_id} job={job} />
      ))}
    </div>
  );
}
```

#### 3. PII Review and Approval Interface
```typescript
// src/components/PIIReview.tsx
interface PIIFinding {
  entity_type: string;
  text: string;
  score: number;
  start: number;
  end: number;
}

export function PIIReview({ token }: { token: string }) {
  const [reviewData, setReviewData] = useState<any>(null);
  const [decision, setDecision] = useState<'approved' | 'denied' | null>(null);
  const [justification, setJustification] = useState('');

  const handleSubmit = async () => {
    await fetch(`http://localhost:8000/api/approve/${token}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, justification })
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>PII Review</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {reviewData?.pii_findings.map((finding: PIIFinding, idx: number) => (
            <Alert key={idx} variant={finding.score > 0.8 ? 'destructive' : 'default'}>
              <AlertDescription>
                <strong>{finding.entity_type}</strong>: {finding.text}
                (Confidence: {(finding.score * 100).toFixed(1)}%)
              </AlertDescription>
            </Alert>
          ))}

          <RadioGroup value={decision || ''} onValueChange={(v) => setDecision(v as any)}>
            <div className="flex items-center space-x-2">
              <RadioGroupItem value="approved" id="approved" />
              <Label htmlFor="approved">Approve - Safe to process</Label>
            </div>
            <div className="flex items-center space-x-2">
              <RadioGroupItem value="denied" id="denied" />
              <Label htmlFor="denied">Deny - Contains PII</Label>
            </div>
          </RadioGroup>

          <Textarea
            placeholder="Justification for decision..."
            value={justification}
            onChange={(e) => setJustification(e.target.value)}
          />

          <Button onClick={handleSubmit} disabled={!decision || !justification}>
            Submit Decision
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
```

#### 4. Processing Results Viewer
```typescript
// src/components/ResultsViewer.tsx
export function ResultsViewer({ jobId }: { jobId: string }) {
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    fetch(`http://localhost:8000/api/jobs/${jobId}/result`)
      .then(res => res.json())
      .then(setResult);
  }, [jobId]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Processing Results</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          <div>
            <strong>Status:</strong> {result?.status}
          </div>
          <div>
            <strong>Output URL:</strong>
            <a href={result?.output_url} target="_blank" rel="noopener noreferrer" className="text-blue-600">
              View Accessible Document
            </a>
          </div>
          <div>
            <strong>Processing Time:</strong> {result?.processing_time}s
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
```

### API Client
```typescript
// src/lib/api.ts
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const api = {
  async uploadDocument(file: File) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE_URL}/api/documents`, {
      method: 'POST',
      body: formData
    });

    return response.json();
  },

  async getJob(jobId: string) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`);
    return response.json();
  },

  async listJobs() {
    const response = await fetch(`${API_BASE_URL}/api/jobs`);
    return response.json();
  },

  async getReviewDetails(token: string) {
    const response = await fetch(`${API_BASE_URL}/api/review/${token}`);
    return response.json();
  },

  async submitApproval(token: string, decision: string, justification: string) {
    const response = await fetch(`${API_BASE_URL}/api/approve/${token}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, justification })
    });
    return response.json();
  }
};
```

## Acceptance Criteria

### 1. User Interface
- [ ] Clean, modern design using ShadCN components
- [ ] Mobile responsive layout (works on tablets/phones for demos)
- [ ] Intuitive navigation between upload/status/review/results
- [ ] Clear status indicators (processing, awaiting approval, complete, failed)
- [ ] Accessible design (keyboard navigation, ARIA labels)

### 2. Document Upload Flow
- [ ] File selection with PDF validation
- [ ] Upload progress indicator
- [ ] Success confirmation with job ID
- [ ] Error handling for invalid files
- [ ] Automatic redirect to job status view

### 3. Job Status Tracking
- [ ] Real-time status updates (polling or websockets)
- [ ] List view of all jobs
- [ ] Detailed view of individual job
- [ ] Status badges (pending, processing, complete, failed)
- [ ] PII detection indicator

### 4. PII Review Workflow
- [ ] Display all PII findings with confidence scores
- [ ] Highlight high-risk PII (SSN, Credit Cards)
- [ ] Radio button selection (approve/deny)
- [ ] Required justification text field
- [ ] Confirmation dialog before submission
- [ ] Success/error feedback

### 5. Results Display
- [ ] Link to accessible HTML output
- [ ] Processing time and stats
- [ ] Download options
- [ ] Error details if processing failed

## Deliverables

### Files to Create
```
/frontend/demo-ui/                    # Separate React app
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.js
├── components.json                   # ShadCN config
├── .env.example
├── README.md                         # Setup instructions
├── src/
│   ├── components/
│   │   ├── ui/                       # ShadCN components
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── input.tsx
│   │   │   ├── alert.tsx
│   │   │   └── ...
│   │   ├── DocumentUpload.tsx
│   │   ├── JobList.tsx
│   │   ├── JobCard.tsx
│   │   ├── PIIReview.tsx
│   │   ├── ApprovalForm.tsx
│   │   └── ResultsViewer.tsx
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── JobDetail.tsx
│   │   ├── ApprovalReview.tsx
│   │   └── Results.tsx
│   ├── hooks/
│   │   ├── useJobs.ts
│   │   └── useApi.ts
│   ├── lib/
│   │   ├── api.ts
│   │   └── utils.ts
│   ├── App.tsx
│   ├── main.tsx
│   └── index.css
└── tests/
    └── components/
        ├── DocumentUpload.test.tsx
        └── PIIReview.test.tsx
```

## Local Development

```bash
# Start the backend API first
cd /Users/dylanisaac/Projects/equalify-pdf-converter
docker-compose up -d
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Run the demo frontend separately
cd frontend/demo-ui
npm install
npm run dev
# Frontend will be available at http://localhost:5173
```

## Environment Configuration

```bash
# .env.example
VITE_API_URL=http://localhost:8000
VITE_ENABLE_WEBSOCKETS=false
```

## Package Configuration

```json
// package.json
{
  "name": "equalify-demo-ui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "description": "Demo frontend for Equalify PDF Converter API",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "@radix-ui/react-alert-dialog": "^1.0.5",
    "@radix-ui/react-button": "^1.0.4",
    "@radix-ui/react-card": "^1.0.4",
    "@radix-ui/react-form": "^0.0.3",
    "@radix-ui/react-radio-group": "^1.1.3",
    "@radix-ui/react-textarea": "^1.0.4",
    "@radix-ui/react-label": "^2.0.2",
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

## README Content

The README.md should include:
1. **Purpose**: Clarify this is a demo UI, not production
2. **Setup Instructions**: Node version, npm install, env configuration
3. **Running the App**: How to start dev server
4. **API Requirements**: Backend API must be running first
5. **Demo Workflow**: Step-by-step demo script for stakeholders
6. **Development Notes**: How to add new components, update API client
7. **Production Note**: Explain this won't be deployed to production

## Testing Strategy

### Component Tests (Optional for Demo)
```typescript
// tests/components/DocumentUpload.test.tsx
import { render, screen } from '@testing-library/react';
import { DocumentUpload } from '../src/components/DocumentUpload';

test('renders upload button', () => {
  render(<DocumentUpload onUploadComplete={() => {}} />);
  expect(screen.getByText(/upload pdf/i)).toBeInTheDocument();
});
```

### Manual Testing Checklist
- [ ] Upload document and verify job created
- [ ] Watch status update in real-time
- [ ] Review PII findings and submit approval
- [ ] View processing results
- [ ] Test on mobile device (responsive design)
- [ ] Test error scenarios (invalid file, network error)

## Definition of Done
- [ ] Frontend application runs independently from backend
- [ ] Can demonstrate complete document workflow to stakeholders
- [ ] Mobile responsive design works on tablets/phones
- [ ] All major workflows functional (upload, status, approval, results)
- [ ] README with clear setup instructions
- [ ] .env.example with required configuration
- [ ] ShadCN components properly installed and themed
- [ ] Can run against local or remote API
- [ ] Demo script documented for stakeholder presentations
- [ ] Clear documentation that this is demo-only, not production
