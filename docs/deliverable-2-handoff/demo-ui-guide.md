# Demo UI Guide (Pipeline Viewer)

The Pipeline Viewer is an internal development interface for team validation. It provides real-time visibility into document processing.

## Accessing the Viewer

**URL:** `http://localhost:8080/viewer`

**Prerequisites:**
- Services running (`make dev`)
- Valid API key configured

## Interface Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Pipeline Viewer                                    [API Key: •••]│
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────┐  ┌─────────────────────────────────────┐  │
│  │  Upload Panel    │  │  Processing Status                  │  │
│  │                  │  │                                     │  │
│  │  [Drop PDF Here] │  │  Phase: Execution                   │  │
│  │  or click        │  │  Jobs: 8/15 complete                │  │
│  │                  │  │  ████████░░░░░░░ 53%                │  │
│  └──────────────────┘  └─────────────────────────────────────┘  │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Event Stream                                                ││
│  │                                                              ││
│  │  10:03:15  planning:complete    12 jobs created             ││
│  │  10:03:16  job:started          ALT_TEXT page 1             ││
│  │  10:03:18  agent:thinking       view_page_tool              ││
│  │  10:03:20  edit:committed       Added alt-text (0.92)       ││
│  │  10:03:21  job:completed        ALT_TEXT page 1             ││
│  │                                                              ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Ledger Preview                                              ││
│  │                                                              ││
│  │  Page 1 (5 edits)                                           ││
│  │  ├─ ALT_TEXT: figure_001  [0.92] ✓                          ││
│  │  ├─ HEADING_FIX: H1→H2   [0.88] ✓                           ││
│  │  └─ TYPOGRAPHY: emphasis  [0.75] ⚠ needs review             ││
│  │                                                              ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  Cost: $0.18  |  Tokens: 133,500  |  Duration: 4m 32s           │
└─────────────────────────────────────────────────────────────────┘
```

**Diagram description:** The Pipeline Viewer interface has four main sections. At the top is a header bar with the title and API key indicator. Below that, the left side contains an Upload Panel with a drag-and-drop zone, and the right side shows Processing Status with the current phase, job progress (8/15 complete), and a progress bar at 53%. The middle section displays an Event Stream showing timestamped events like "planning:complete", "job:started", "agent:thinking", "edit:committed", and "job:completed". Below that is a Ledger Preview showing edits grouped by page with confidence scores and review status (checkmarks for auto-applied, warning icons for needs review). The footer displays cost ($0.18), token count (133,500), and duration (4m 32s).

## Upload Panel

### Drag and Drop

1. Drag a PDF file onto the upload area
2. Processing starts immediately with `skip_pii_scan=true`
3. SSE stream connects automatically

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| Review Mode | `auto` | `auto`: immediate completion, `human`: ledger review |
| Debug Bundle | `false` | Generate debug artifacts for troubleshooting |

## Processing Status

### Phase Indicators

| Phase | Icon | Description |
|-------|------|-------------|
| Docling | 📄 | PDF extraction in progress |
| Planning | 🗺️ | Document analysis and job creation |
| Execution | ⚙️ | Jobs running in parallel |
| Verification | ✅ | Quality checks |
| Recovery | 🔧 | Fixing failed pages (if needed) |
| Complete | 🎉 | Processing finished |

### Progress Bar

Shows `jobs_complete / jobs_total` during execution phase.

## Event Stream

Real-time events displayed chronologically:

### Event Categories

| Category | Events | Color |
|----------|--------|-------|
| Planning | `planning:*` | Blue |
| Jobs | `job:*` | Gray |
| Agent | `agent:thinking` | Purple |
| Edits | `edit:committed`, `edit:validated` | Green |
| Verification | `verification:*` | Orange |
| Recovery | `recovery:*` | Yellow |
| Final | `processing:complete`, `processing:error` | Bold |

### Filtering

Click category pills to filter visible events.

## Ledger Preview

Shows edits grouped by page as they're committed:

### Edit Entry

```
├─ ACTION: target  [confidence] status
│    Before: original text...
│    After:  modified text...
│    Reason: AI explanation
```

### Status Icons

| Icon | Meaning |
|------|---------|
| ✓ | Auto-applied (confidence ≥ 0.8) |
| ⚠ | Needs review (confidence 0.5-0.8) |
| ✗ | Skipped (confidence < 0.5) |

## Results Panel

Appears after processing completes:

### Available Actions

| Action | Description |
|--------|-------------|
| **Download Markdown** | Download final accessible markdown |
| **View Ledger** | Open full ledger with all changes |
| **Download Debug Bundle** | Download ZIP with all artifacts (if enabled) |

### Metrics Display

- **Confidence Score:** Overall document confidence (0-1)
- **Total Edits:** Number of changes made
- **Processing Time:** Total duration
- **Cost:** Estimated LLM cost in dollars

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+U` | Open upload dialog |
| `Ctrl+L` | Toggle ledger panel |
| `Ctrl+E` | Toggle event stream |
| `Esc` | Close modals |

## Troubleshooting

### Connection Lost

If SSE stream disconnects:
1. Check if services are running (`make logs`)
2. Refresh the page
3. Re-upload the document

### Slow Processing

- Large documents (20+ pages) take longer
- Complex tables and figures require more compute
- Check `make logs-api` for detailed timing

### No Events Appearing

1. Verify API key is correct
2. Check browser console for errors
3. Ensure job was created (`GET /api/v1/documents/{job_id}`)

## Technical Details

**Source:** `frontend/demo-ui/src/` (React 18, Tailwind CSS, Vite)

**Key files:** `ViewerPage.tsx` (main component), `useStream.ts` (SSE handling)
