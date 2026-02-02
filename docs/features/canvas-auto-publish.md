# Proposal: Automatic PDF-to-Canvas Page Publishing

## Problem

Instructors upload PDF course materials to Canvas. Students receive these as static, fixed-layout files -- content trapped in a container that doesn't reflow on mobile, can't be searched within Canvas, doesn't integrate with Canvas's native tools, and resists assistive technology.

The Equalify PDF Converter already solves the conversion problem: it transforms PDFs into semantic markdown via Docling extraction and AI-agent text correction. But today, the conversion requires a manual trigger -- an instructor clicks "Open in Equalify Reflow" from the Canvas file menu, waits for processing, and gets a result in a separate viewer outside Canvas.

This proposal describes a system where **instructors upload PDFs normally, and students automatically get Canvas-native pages** -- no manual trigger, no leaving Canvas, no waiting.

## Vision

```
Instructor uploads PDF to Canvas
         |
         v
System detects new PDF (background)
         |
         v
Existing pipeline: Docling + AI agents → semantic markdown
         |
         v
Markdown → semantic HTML renderer
         |
         v
Canvas Pages API: publish as Canvas Page
Canvas Files API: upload extracted figures
Canvas Modules API: place in course modules
         |
         v
Student opens course → content is a native Canvas Page
```

The result is a first-class Canvas Page. It shows up in search, works in the Canvas mobile app, reflows to any screen size, and works with assistive technology. Students access it the same way they access any other course content -- it's just a page in the module. The PDF content has been freed from its fixed-layout container and turned into something Canvas-native.

## User Experience

### Instructor Flow

1. Instructor uploads `lecture_notes.pdf` to Course Files (or a Module), as they normally do.
2. Within minutes, a new Canvas Page appears: **"lecture_notes - Reflow"**. It's created as a **draft** by default.
3. Instructor receives a notification (or sees a status indicator in the Equalify dashboard) that processing is complete.
4. Instructor reviews the page. If it looks good, they publish it. (Option: auto-publish if confidence score is above a configurable threshold.)
5. Students see the content as a native Canvas Page in the module.

The key UX principle: **instructors don't change their workflow**. They upload PDFs the same way they always have. The Canvas-native version appears automatically.

### Student Flow

1. Student navigates to a course module.
2. They see the course content as Canvas Pages:
   ```
   Week 3: Cell Biology
     📝 Lecture Notes - Reflow
     📝 Chapter 4 Reading - Reflow
   ```
3. Clicking a page opens reflowable, semantic HTML -- proper headings, embedded images, structured tables. It works on any screen size, with any assistive technology, and with Canvas's built-in tools (search, bookmarks, mobile app).
4. At the bottom of each page, students can download the raw markdown and image assets for use in markdown-native tools (Obsidian, Notion, AI assistants, etc.).
5. The original PDF can optionally remain available as a download, but the Canvas Page is the primary format.

### Edge Cases

- **PDF uploaded during live lecture**: Students who access it before processing completes see only the PDF. The Canvas Page appears a few minutes later. No broken state -- just a brief window where only the original exists.
- **Instructor re-uploads a PDF with the same name**: System detects the update, re-processes, and updates the existing Canvas Page.
- **Processing fails**: Page is not created. Instructor sees an error in the dashboard with the option to retry. Original PDF remains available.
- **Non-PDF files**: Ignored. Only `application/pdf` files trigger processing.

## Technical Architecture

### Component Overview

```
┌──────────────────────────────────────────────────────┐
│                    Canvas LMS                         │
│                                                       │
│  Files API ──── Modules API ──── Pages API            │
│      │               │              ▲                 │
└──────┼───────────────┼──────────────┼─────────────────┘
       │               │              │
       ▼               │              │
┌──────────────────────┼──────────────┼─────────────────┐
│  Equalify PDF Converter             │                 │
│                                     │                 │
│  ┌─────────────────┐  ┌────────────┴──────────────┐  │
│  │ File Discovery   │  │ Canvas Publisher           │  │
│  │ Worker           │  │                            │  │
│  │                  │  │ - Markdown→HTML renderer   │  │
│  │ - Poll Canvas API│  │ - Image uploader (Files)   │  │
│  │ - Filter PDFs    │  │ - Page creator (Pages)     │  │
│  │ - Track processed│  │ - Module linker (Modules)  │  │
│  └────────┬─────────┘  └────────────▲──────────────┘  │
│           │                         │                  │
│           ▼                         │                  │
│  ┌─────────────────────────────────────────────────┐  │
│  │ Existing Processing Pipeline                     │  │
│  │                                                   │  │
│  │ S3 Upload → Docling → AI Text Correction          │  │
│  │ → Confidence Scoring → Semantic Markdown           │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐                    │
│  │ Redis         │  │ S3 (results) │                    │
│  │ - Job state   │  │ - Markdown   │                    │
│  │ - Processed   │  │ - Figures    │                    │
│  │   file set    │  │              │                    │
│  └──────────────┘  └──────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

### New Components

#### 1. File Discovery Worker

Detects new PDF uploads in Canvas courses where the tool is enabled.

**MVP: Canvas API Polling**

A background worker runs on a schedule (e.g., every 2 minutes):

```
For each configured course:
  GET /api/v1/courses/{id}/files?content_types[]=application/pdf&sort=created_at&order=desc
  For each PDF not in the processed-files set:
    Queue for processing
    Add to processed-files set (Redis)
```

- Only monitors courses where the LTI tool is installed
- Uses a Redis set (`eq-pdf:processed:{course_id}`) to track processed file IDs
- Respects Canvas API rate limits (request throttling)
- Canvas API token with file read permissions required

**Production: Canvas Live Events (future)**

Canvas emits `attachment_created` events to an SQS queue. A consumer filters for PDFs and queues processing. This is real-time (seconds vs minutes) but requires Canvas admin to configure the event subscription.

#### 2. Markdown-to-HTML Renderer

Converts the pipeline's semantic markdown output into HTML suitable for Canvas Pages.

Responsibilities:
- Parse markdown (headings, paragraphs, lists, tables, code blocks, images, math)
- Generate semantic HTML (proper heading hierarchy, ARIA landmarks, structured tables)
- Wrap images in `<figure>` with `<figcaption>`, preserve alt text
- Structure tables with `<thead>`, `<th scope="col|row">`, `<caption>`
- Apply inline CSS for layout (Canvas may strip `<style>` tags)
- Output self-contained HTML suitable for the Canvas Pages `body` field

Library candidates: `mistune` (already a Python dependency via Docling) with a custom renderer, or `markdown-it-py` with accessibility plugins.

#### 3. Canvas Publisher Service

Orchestrates publishing the Canvas-native version.

**Step 1: Upload extracted images to Canvas**
```
For each figure extracted by the pipeline:
  POST /api/v1/courses/{course_id}/files
  (Canvas 3-step upload: request → upload → confirm)
  → Get Canvas-hosted image URL
```

Uploading images to Canvas Files means they're served by Canvas's CDN, work in the Canvas mobile app, and don't depend on external S3 URLs.

**Step 2: Create Canvas Page**
```
POST /api/v1/courses/{course_id}/pages
{
  "wiki_page": {
    "title": "{original_filename} - Reflow",
    "body": "<article>...rendered HTML with Canvas image URLs...</article>
             <footer>
               <a href='{s3_download_url}'>Download markdown + assets</a>
             </footer>",
    "published": false,  // Draft by default, configurable
    "editing_roles": "teachers"
  }
}
```

The page body includes a footer with a download link to the raw markdown bundle hosted on S3. This gives students the option to work with the content in markdown-native tools.

**Step 3: Add to Module (if original PDF is in a module)**
```
# Find which module contains the original PDF
GET /api/v1/courses/{course_id}/modules?include[]=items

# Add the page as a module item after the PDF
POST /api/v1/courses/{course_id}/modules/{module_id}/items
{
  "module_item": {
    "title": "{original_filename} - Reflow",
    "type": "Page",
    "page_url": "{page_slug}",
    "position": {pdf_position + 1}
  }
}
```

### Canvas API Permissions Required

The system needs a Canvas API token (or OAuth client credentials) with:

| Permission | API Endpoints | Why |
|---|---|---|
| Read files | `GET /courses/{id}/files` | Discover new PDFs, download content |
| Create pages | `POST /courses/{id}/pages` | Publish Canvas-native versions |
| Update pages | `PUT /courses/{id}/pages/{url}` | Update when PDF is re-uploaded |
| Upload files | `POST /courses/{id}/files` | Upload extracted images |
| Read modules | `GET /courses/{id}/modules` | Find where PDF lives in course structure |
| Create module items | `POST /courses/{id}/modules/{id}/items` | Place page in course modules |

For a university deployment, these permissions would come from either:
- A **service account** API token created by the Canvas admin
- **LTI Advantage** OAuth 2.0 client credentials (scoped per-course, no static token)

### Image Handling Strategy

Extracted figures are stored in two places (dual storage):

```
Pipeline extracts figure → S3 results bucket (source of truth, existing)
                               │
                               ├──→ Canvas Files API upload
                               │    (POST /courses/{id}/files)
                               │    → Canvas-hosted URL for Page HTML
                               │    (<img src="canvas-cdn-url" alt="Figure 1: ...">)
                               │
                               └──→ S3 download bundle
                                    (markdown + images as downloadable archive)
```

**Canvas Files**: Images uploaded to a dedicated folder in the course's file storage (e.g., `equalify-reflow/figures/`). These are referenced in the Canvas Page HTML. Served by Canvas's CDN, work in the mobile app.

**S3 (source of truth)**: The complete markdown output and all extracted image assets remain in S3. This serves the downloadable markdown bundle linked from the Canvas Page footer, and acts as the authoritative copy for re-publishing if needed.

### Data Flow (Complete)

```
1. File Discovery Worker detects new PDF
   Input:  Canvas API file listing
   Output: Job queued in Redis with canvas_file_id, course_id

2. Pipeline downloads and processes PDF (existing)
   Input:  Canvas file download URL
   Output: Semantic markdown + extracted figures in S3

3. Markdown-to-HTML Renderer converts output
   Input:  Markdown from S3
   Output: Semantic HTML string

4. Canvas Publisher uploads images
   Input:  Figure files from S3
   Output: Canvas-hosted image URLs

5. Canvas Publisher creates/updates Page
   Input:  HTML with Canvas image URLs
   Output: Canvas Page (draft)

6. Canvas Publisher links to Module
   Input:  Course/module structure
   Output: Module item alongside original PDF

7. (Optional) Notify instructor
   Input:  Processing result
   Output: Canvas notification or dashboard update
```

## Instructor Control

### Course-Level Configuration

Instructors configure auto-processing via an LTI `course_navigation` placement -- a sidebar item in Canvas that opens the Equalify dashboard:

- **Enable/disable** auto-processing for the course
- **Auto-publish threshold**: Confidence score above which pages are auto-published (default: create as draft)
- **Processing status**: List of all PDFs and their conversion status
- **Retry failed**: Re-process documents that failed
- **Review queue**: Pages awaiting instructor review before publishing

### Per-File Control

- Instructors can exclude specific files from processing (via the dashboard)
- Re-uploading a PDF triggers re-processing and updates the existing page
- Deleting the original PDF optionally deletes the generated page (configurable)

## Alternatives Considered

### A. Keep the current LTI file_menu approach (manual trigger)

**Pros:** Already built. Simple. No Canvas API polling needed.
**Cons:** Requires instructor action for every file. Students must leave Canvas to view results. Doesn't scale to courses with dozens of PDFs.

**Verdict:** Keep as a complement (useful for one-off processing), but doesn't solve the automatic publishing goal.

### B. Host content outside Canvas (current viewer)

**Pros:** Full control over rendering. Rich interactive features possible (diff view, annotations). No Canvas API write permissions needed.
**Cons:** Students leave the LMS. Doesn't work with Canvas mobile app. External dependency. Another URL to manage. Doesn't feel "native."

**Verdict:** Good for the instructor review/diff experience, but the student-facing content should live inside Canvas.

### C. Canvas Rich Content Editor plugin

**Pros:** Deep Canvas integration. Could transform PDFs inline when instructors paste them into pages.
**Cons:** Only works when instructors explicitly use the editor. Doesn't handle files uploaded to the Files area. Requires Canvas plugin architecture (not available on Instructure-hosted Canvas).

**Verdict:** Not viable for Instructure-hosted institutions.

### D. Replace the PDF entirely

Instead of creating a Canvas Page alongside the PDF, delete the PDF and only keep the HTML.

**Pros:** Students only see one version. No confusion. Clean experience.
**Cons:** Destroys the original. Some content (complex diagrams, precise formatting) may lose fidelity. Instructors may object to their files being modified. No fallback if processing produces errors.

**Verdict:** Appealing in principle but too aggressive for v1. The Canvas Page should be the primary format with the original PDF available as a download, not a parallel item in the module. Long-term, if conversion quality is consistently high, the PDF could be moved to an archive folder rather than displayed alongside the page.

### E. Browser extension / client-side conversion

**Pros:** No server-side processing. Works anywhere.
**Cons:** 5-minute processing time in the browser is unacceptable. Can't run AI agents client-side. No persistence -- each student would re-process the same document.

**Verdict:** Not viable for AI-agent-based processing.

## Full Requirements

### What exists (reusable as-is)

| Component | Location | What it does |
|---|---|---|
| PDF processing pipeline | `src/services/document_processing_service.py` | Docling extraction → AI text correction → semantic markdown |
| S3 storage | `src/services/storage_service.py` | Upload/download files, presigned URLs |
| Redis job management | `src/services/job_service.py` | Job state, queuing, status tracking |
| LTI 1.3 authentication | `src/lti/` | OIDC flow, JWT validation, Canvas file download |
| Canvas API client (basic) | `src/lti/service.py` | File listing, file download with Docker networking |
| Confidence scoring | `src/services/assembly_service.py` | Per-page and overall confidence scores |
| SSE event streaming | `src/api/routes/documents.py` | Real-time job status updates |

### What needs to be built

#### Backend (Python/FastAPI)

**1. Canvas API Client** — `src/canvas/client.py`
Authenticated client for Canvas REST API with rate limiting. Used by all Canvas-facing components.
- Course files listing (paginated)
- Pages API (create, update, get)
- Files API (3-step upload workflow)
- Modules API (list modules + items, create items)
- Rate limit handling (Canvas returns `X-Rate-Limit-Remaining`)

**2. Markdown-to-HTML Renderer** — `src/canvas/renderer.py`
Converts pipeline markdown output to Canvas-compatible semantic HTML.
- Parse markdown (mistune or markdown-it-py with custom renderer)
- Semantic HTML output (article, section, figure/figcaption, structured tables)
- Rewrite image references from S3 paths to Canvas-hosted URLs
- Inject download footer with link to markdown zip bundle
- Inline CSS where needed (Canvas may strip `<style>` tags)

**3. Canvas Publisher Service** — `src/canvas/publisher.py`
Orchestrates publishing a processed document to Canvas.
- Upload extracted images to Canvas Files API (course folder: `equalify-reflow/`)
- Create or update Canvas Page with rendered HTML
- Add page to course module (if PDF was in a module)
- Generate and upload markdown zip bundle to S3
- Track published state in Redis (page URL, canvas page ID, canvas file IDs)

**4. File Discovery Worker** — `src/workers/canvas_file_worker.py`
Background task that polls Canvas for new PDFs.
- Scheduled loop (configurable interval, default 2 min)
- Query each enabled course for PDF files
- Compare against Redis processed-file set (`eq-pdf:processed:{course_id}`)
- Check `updated_at` for re-processing triggers
- Queue new/updated PDFs for processing
- Respect Canvas API rate limits

**5. Course Configuration API** — `src/api/routes/canvas_config.py`
REST endpoints for the instructor dashboard to manage course settings.
- `GET /api/v1/canvas/courses/{id}/config` — get course processing settings
- `PUT /api/v1/canvas/courses/{id}/config` — update settings (enabled, auto-publish threshold)
- `GET /api/v1/canvas/courses/{id}/documents` — list PDFs and their processing status
- `POST /api/v1/canvas/courses/{id}/documents/{file_id}/process` — manually trigger processing
- `POST /api/v1/canvas/courses/{id}/documents/{file_id}/retry` — retry failed processing
- `POST /api/v1/canvas/courses/{id}/documents/{file_id}/publish` — publish a draft page

**6. Download Bundle Generator** — `src/canvas/bundle.py`
Creates downloadable zip of markdown + image assets.
- Pull markdown and figures from S3 results bucket
- Package as `.zip` with `{filename}.md` + `images/` folder
- Upload zip to S3, generate presigned download URL
- URL referenced in Canvas Page footer

#### Frontend (Instructor Dashboard)

**7. Dashboard UI** — `src/canvas/templates/`
LTI `course_navigation` placement. Instructor-facing, server-rendered from FastAPI.

**Stack**: Jinja2 templates + Tailwind CSS. No JS framework. FastAPI serves HTML directly. Tailwind compiles to a static CSS file (`src/canvas/static/dashboard.css`) via the Tailwind CLI during build. Interactive elements (publish button, retry, settings toggle) use standard HTML forms or htmx for partial page updates without a full SPA.

Views:
- **Document list**: All PDFs in the course with status (pending, processing, draft, published, failed)
- **Settings**: Enable/disable auto-processing, auto-publish confidence threshold
- **Document detail**: Preview of generated page, confidence score, publish/retry actions

The dashboard needs:
- LTI session context (which course, which user, what role)
- Status polling or SSE for in-progress jobs (SSE already exists in the pipeline)
- Canvas-compatible iframe embedding (respect X-Frame-Options, handle LTI session)

#### Infrastructure / Config

**8. LTI Configuration Updates** — `src/lti/config.py`
Add `course_navigation` placement to the Developer Key config for the dashboard.

**9. Canvas Course Config Storage** — Redis or database
Per-course settings: enabled, auto-publish threshold, processing stats.
- Redis hash: `eq-pdf:course-config:{course_id}` → `{enabled, auto_publish_threshold, ...}`
- Redis hash: `eq-pdf:processed:{course_id}` → `{file_id: updated_at, ...}`
- Redis hash: `eq-pdf:published:{course_id}:{file_id}` → `{page_url, canvas_page_id, ...}`

### Component dependency graph

```
                    Dashboard UI (7)
                         │
                         ▼
                Course Config API (5)
                    │         │
         ┌──────────┘         └──────────┐
         ▼                               ▼
  File Discovery         Canvas Publisher (3)
  Worker (4)               │          │
         │                 ▼          ▼
         │          MD→HTML       Download Bundle
         │         Renderer (2)   Generator (6)
         │                 │
         └────────┬────────┘
                  ▼
           Canvas API Client (1)
                  │
                  ▼
         Existing Pipeline
    (S3, Redis, Docling, AI agents)
```

**Build order follows the dependency graph bottom-up:**

### Implementation Phases

### Phase 1: Canvas API Client + Renderer + Publisher

The foundation. Everything else depends on being able to talk to Canvas and produce HTML.

| # | Component | What to build |
|---|---|---|
| 1a | Canvas API Client | Authenticated client with rate limiting, Pages/Files/Modules endpoints |
| 1b | Markdown-to-HTML Renderer | Custom renderer producing Canvas-compatible semantic HTML |
| 1c | Canvas Publisher Service | Image upload → Page create/update → Module linking |
| 1d | Download Bundle Generator | Zip packaging, S3 upload, presigned URL generation |

**Test**: Manually trigger via existing LTI launch → pipeline processes PDF → publisher creates Canvas Page with images and download link. Verify in local Canvas.

### Phase 2: File Discovery + Course Config

Automation layer. PDFs get processed without instructor action.

| # | Component | What to build |
|---|---|---|
| 2a | Course Config Storage | Redis schemas for course settings and processed-file tracking |
| 2b | File Discovery Worker | Polling loop, deduplication, re-processing detection |
| 2c | Course Config API | REST endpoints for settings and document status |

**Test**: Enable auto-processing for local Canvas course → upload PDF → verify page appears automatically within polling interval.

### Phase 3: Instructor Dashboard

Control plane. Instructors manage their course's processing.

| # | Component | What to build |
|---|---|---|
| 3a | LTI course_navigation placement | Add placement to Developer Key config |
| 3b | Dashboard UI | Document list, settings, publish/retry actions |
| 3c | Dashboard API auth | LTI session validation for dashboard API calls |

**Test**: Open dashboard from Canvas course sidebar → see document list → change settings → publish a draft page → retry a failed job.

### Phase 4: Production Hardening

Scale and reliability for institutional deployment.

- Canvas Live Events (SQS consumer) as alternative to polling
- Error recovery and dead letter queue
- Processing cost tracking per course
- Canvas API token rotation
- Monitoring dashboards (extend existing Grafana)

**Why last:** Requires institutional cooperation and real-world usage patterns to prioritize.

## Decisions

1. **Page naming**: `{original_filename} - Reflow` (e.g., `lecture_notes - Reflow`). Strips the `.pdf` extension, appends ` - Reflow` to signal the format without implying a separate accessibility accommodation.

2. **Draft vs auto-publish**: Draft by default. Configurable per course -- instructors can set an auto-publish confidence threshold in the dashboard if they want hands-off operation.

3. **Canvas API authentication**: TBD. Need more information about what UIC's Canvas instance supports before choosing between service account tokens and LTI Advantage OAuth.

4. **Image hosting**: Dual storage. Upload extracted images to Canvas Files (so they work natively in Canvas Pages, mobile app, CDN). Also keep the full markdown output and image assets in S3 as the source of truth. The Canvas Page will include a download link for the raw markdown + assets bundle, so students can work with the content in markdown-native tools (Obsidian, Notion, AI assistants, etc.).

5. **Re-processing trigger**: Track Canvas file `updated_at` timestamps in Redis alongside processed file IDs. When the file discovery worker sees a file whose `updated_at` is newer than the stored value, re-process it and update the existing Canvas Page. This is simpler than content hashing (no need to download the file just to check), and Canvas reliably updates this timestamp when file content changes. Store as `eq-pdf:processed:{course_id}` → `{file_id: updated_at}` hash in Redis.

6. **Scope**: Configurable at the course level. Instructors opt in per course via the dashboard. Account-level enable/disable can be added later for institutional rollouts.

7. **Cost model**: ~$0.05-$0.50 per document depending on length and complexity. Course-level configuration keeps costs manageable -- only enabled courses incur processing costs. Cost tracking per course for billing/reporting.

8. **Existing content**: Only new uploads going forward. When auto-processing is first enabled for a course, the system begins monitoring for new PDFs from that point. Instructors can manually trigger processing for existing PDFs via the dashboard if they want to backfill.

9. **Download bundle format**: `.zip` archive containing the markdown file and image assets folder.

10. **Canvas Page styling**: TBD. Need to test against Canvas's HTML sanitizer to find what works. Decide after building the renderer and testing against a real Canvas instance.

11. **Notification mechanism**: Dashboard status indicator (simplest). Instructors check the Equalify dashboard to see processing status. No push notifications in v1.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Canvas API rate limiting | Processing backlog during batch uploads | Throttle requests, prioritize by course activity |
| AI processing costs at scale | High per-document cost at university scale | Tiered processing (simple docs skip AI), confidence-based early termination |
| Canvas HTML sanitization strips needed elements | Broken rendering | Test against Canvas's sanitization rules early, use only elements known to be preserved |
| Processing failure rate | Missing Canvas Page for some PDFs | Retry logic, instructor notification, manual fallback via LTI launch |
| Course storage quota | Images use course file quota | Compress images, track usage, warn instructors |
| Stale content | PDF updated but page not re-processed | Track file content hashes, re-process on change |
| Permission scope creep | System has broad Canvas write access | Minimize permissions, use course-scoped tokens where possible |
| FERPA / student data | Course materials may reference students | The system processes course materials only (same boundary as existing pipeline), not student submissions |
