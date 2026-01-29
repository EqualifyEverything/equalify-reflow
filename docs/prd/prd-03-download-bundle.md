# PRD-03: Download Bundle Generator

## Problem Statement

Students should be able to download the raw markdown and image assets from a Canvas Page for use in markdown-native tools (Obsidian, Notion, AI assistants). The pipeline stores markdown and figures in S3, but there is no component that packages them into a downloadable zip.

## Goal

A service that packages a job's markdown output and extracted images into a `.zip` file, uploads it to S3, and returns a presigned download URL.

## Dependencies

None. Uses existing S3 storage service and pipeline output format.

## Requirements

### R1: Bundle generator class

Create `src/canvas/bundle.py`:

```python
# src/canvas/bundle.py

class DownloadBundleService:
    """Package markdown + image assets into a downloadable zip."""

    def __init__(self, storage_service: "StorageService"):
        self.storage = storage_service

    async def create_bundle(
        self,
        job_id: str,
        original_filename: str,
    ) -> str:
        """Create a zip bundle of markdown + images and return a presigned download URL.

        Reads the result markdown and figure images from S3 results bucket,
        packages them into a zip, uploads the zip to S3, and returns a
        presigned URL for download.

        Args:
            job_id: The processing job ID
            original_filename: Original PDF filename (used for zip naming)

        Returns:
            Presigned S3 URL for the zip download (valid for 7 days)
        """
        ...
```

### R2: Zip structure

The zip file should be named `{original_filename_without_extension}-reflow.zip` and contain:

```
lecture_notes-reflow/
  lecture_notes.md          # The result markdown
  images/
    figure_1.png            # Extracted figures
    figure_2.png
    ...
```

### R3: S3 operations

1. Download `results/{job_id}/result.md` from S3 results bucket
2. List and download all files matching `results/{job_id}/figures/*` from S3 results bucket
3. Package into zip using Python's `zipfile` module (in memory, no temp files)
4. Upload zip to `results/{job_id}/bundle.zip` in S3 results bucket
5. Generate presigned URL with 7-day expiry using `StorageService`

### R4: Error handling

- If `result.md` doesn't exist in S3, raise `BundleError("No result markdown found for job {job_id}")`
- If no figures exist, create the zip with just the markdown (no images/ folder)
- Log the bundle size and number of files at INFO level

## Implementation Notes

### Files to create:
1. `src/canvas/bundle.py` -- the bundle service

### Files to modify:
None.

### Design decisions:
- Build zip in memory using `io.BytesIO` + `zipfile.ZipFile` to avoid temp file management
- Use existing `StorageService` for all S3 operations (inherits circuit breakers and retry logic)
- 7-day URL expiry matches reasonable assignment lifecycle
- Zip stored in S3 alongside other results for the job -- cleaned up when job expires

## Success Criteria

- [ ] `src/canvas/bundle.py` exists with `DownloadBundleService` class
- [ ] `create_bundle()` downloads `results/{job_id}/result.md` from S3 results bucket
- [ ] `create_bundle()` downloads all files matching `results/{job_id}/figures/*` from S3
- [ ] `create_bundle()` creates a zip containing `{name}.md` at root and `images/` folder with figures
- [ ] The zip is named `{original_filename_without_extension}-reflow.zip`
- [ ] The zip is uploaded to `results/{job_id}/bundle.zip` in S3 results bucket
- [ ] `create_bundle()` returns a presigned S3 URL valid for 7 days
- [ ] If `result.md` doesn't exist, a `BundleError` is raised with descriptive message
- [ ] If no figures exist, the zip contains only the markdown file (no empty images/ folder)
- [ ] Zip is built in memory using `io.BytesIO` (no temp files on disk)
