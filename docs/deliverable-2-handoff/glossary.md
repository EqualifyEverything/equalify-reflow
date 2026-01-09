# Glossary

Terms and definitions used throughout this documentation.

## Core Concepts

### Agent
An AI-powered component that performs document processing tasks. Agents can invoke tools and make decisions based on document content. In this system, agents orchestrate processing rather than performing specialized tasks directly.

### Tool
A specialized function that an agent can invoke to perform a specific task. Tools encapsulate expert knowledge for tasks like alt-text generation or table transcription. Tools are implemented as subagents—separate LLM calls with specialized prompts.

### Subagent
A tool implemented as a separate LLM call with expert prompting. Subagents return results with confidence scores, and the parent agent decides whether to apply them. Examples: Typography Subagent, Citation Subagent, Footnote Subagent.

### Pipeline
The complete document processing workflow from PDF input to accessible markdown output. Consists of phases: Planning → Execution → Verification → Recovery.

### Job
A discrete processing task created during planning. Jobs are routed to appropriate agents and executed in parallel. Types include: ALT_TEXT, TABLE_TRANSCRIPTION, HEADING_FIX, PARAGRAPH.

## Processing Phases

### Planning Phase
Initial document analysis that identifies structure, creates jobs, and builds a processing plan. Includes quick scan (regex-based) and page chain analysis (LLM-based).

### Execution Phase
Parallel processing of jobs by worker and paragraph agents. The main phase where document remediation occurs.

### Verification Phase
Quality checks after execution to ensure completeness. Validates that all figures have alt-text, tables are transcribed, and structure is correct.

### Recovery Phase
Conditional phase that attempts to fix issues found during verification. Only runs if verification fails but pass rate is above threshold.

## Document Processing

### Docling
IBM's open-source PDF processing library used for initial extraction. Converts PDF to markdown with image placeholders.

### Confidence Score
A value between 0.0 and 1.0 indicating how certain the AI is about an edit. High confidence (≥0.8) means auto-apply; medium (0.5-0.8) means apply with review flag; low (<0.5) means skip.

### Ledger
A complete audit trail of all edits made during processing. Each entry includes before/after content, reasoning, confidence, and whether review is needed.

### Alt-text (Alternative Text)
Descriptive text for images that conveys the same information to users who cannot see the image. Critical for accessibility.

### Table Transcription
Converting a visual table (image or PDF table) into properly structured markdown with headers and data cells.

### Heading Hierarchy
The structure of headings in a document (H1 → H2 → H3). Proper hierarchy is essential for navigation with assistive technology.

## Infrastructure

### PydanticAI
Python framework for building AI agents with type-safe outputs. Used to coordinate model interactions and tool definitions.

### AWS Bedrock
Amazon's managed service for foundation models. Provides Claude models for document processing.

### Redis
In-memory data store used for job state management, queuing, and event bus functionality.

### LocalStack
Local AWS emulator used in development. Provides S3 and other AWS services without cloud deployment.

### S3 (Simple Storage Service)
AWS object storage used for document storage. Temp bucket for uploads, results bucket for outputs.

### SSE (Server-Sent Events)
Protocol for real-time server-to-client communication. Used to stream processing events to the viewer UI.

## API Terms

### Job ID
Unique identifier assigned to each document submission. Used to track status and retrieve results.

### Stream Token
Single-use authentication token for SSE connections. Generated via API because browser EventSource cannot send headers.

### Review Mode
Processing mode selection: `auto` completes immediately, `human` pauses for review before completion.

### Debug Bundle
ZIP file containing all processing artifacts (prompts, responses, images, outputs) for troubleshooting.

## Accessibility Terms

### WCAG (Web Content Accessibility Guidelines)
W3C standard for web accessibility. This system targets WCAG 2.1 AA compliance.

### Screen Reader
Assistive technology that reads content aloud for users who cannot see the screen. Examples: NVDA, VoiceOver, JAWS.

### PII (Personally Identifiable Information)
Data that can identify an individual (SSN, email, phone). Documents are scanned for PII before processing to protect privacy.

### Microsoft Presidio
Open-source PII detection library used to scan documents before AI processing.

## Document Elements

### Figure
Any visual element in a document: images, charts, graphs, diagrams. Requires alt-text for accessibility.

### Placeholder
Marker in extracted markdown indicating content that needs processing. Format: `<!-- image placeholder -->` or `<!-- table placeholder -->`.

### Artifact
Unwanted content from PDF conversion: page numbers in wrong places, broken words at page breaks, orphaned headers.

### Cross-reference
Link from one part of a document to another. Examples: "See Figure 3", "As described in Section 2.1".

### Footnote
Reference marker (usually superscript number) linking to explanatory text, typically at page bottom or document end.

### Citation
Reference to external source, usually in academic format: `[1]`, `(Smith, 2023)`, linking to bibliography.

## Quality Metrics

### Pass Rate
Percentage of pages that pass verification checks. Used to determine if recovery phase should run.

### Edit Count
Total number of changes made to a document during processing.

### Processing Duration
Wall-clock time from submission to completion. Target: 2-8 minutes.

### Cost per Document
Total LLM API cost for processing one document. Target: ~$0.20.

## Project Terms

### Deliverable
A milestone in the project with specific acceptance criteria and payment.

### DASE (Digital Accessibility Solutions & Engineering)
UIC team responsible for accessibility compliance and tooling.

### Pilot
Initial testing phase with 30 documents to validate system quality before broader deployment.

### Review Mode
Option to either auto-complete processing (`auto`) or pause for human review (`human`).
