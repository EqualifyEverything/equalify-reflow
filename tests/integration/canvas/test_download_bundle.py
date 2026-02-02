"""Integration tests for DownloadBundleService with real S3.

Verifies S3 round-trip for zip bundles: upload result.md + figures,
create a zip bundle, download and verify internal structure.
"""

import io
import zipfile

import pytest
from src.canvas.bundle import BundleError
from src.config import settings


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_create_bundle_with_markdown_only(bundle_service, seed_s3_result):
    """Upload result.md, create bundle, verify zip contains markdown."""
    job_id = "bundle-test-md-only"
    seed_s3_result(job_id, markdown="# Lecture Notes\n\nImportant content.")

    url = await bundle_service.create_bundle(job_id, "lecture_notes.pdf")

    assert url  # non-empty string
    # Verify zip was uploaded to S3
    response = bundle_service.storage.s3_client.get_object(
        Bucket=settings.s3_results_bucket,
        Key=f"results/{job_id}/bundle.zip",
    )
    zip_bytes = response["Body"].read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "lecture_notes-reflow/lecture_notes.md" in names
        content = zf.read("lecture_notes-reflow/lecture_notes.md")
        assert b"# Lecture Notes" in content


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_create_bundle_with_figures(bundle_service, seed_s3_result):
    """Upload result.md + figures, verify zip has images/ subfolder."""
    job_id = "bundle-test-with-figs"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    seed_s3_result(
        job_id,
        markdown="# Doc with Images\n\n![fig](images/figure_1.png)",
        figures={"figure_1.png": fake_png, "figure_2.png": fake_png},
    )

    url = await bundle_service.create_bundle(job_id, "chapter4.pdf")

    assert url
    response = bundle_service.storage.s3_client.get_object(
        Bucket=settings.s3_results_bucket,
        Key=f"results/{job_id}/bundle.zip",
    )
    zip_bytes = response["Body"].read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "chapter4-reflow/chapter4.md" in names
        assert "chapter4-reflow/images/figure_1.png" in names
        assert "chapter4-reflow/images/figure_2.png" in names


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_create_bundle_returns_presigned_url(bundle_service, seed_s3_result):
    """Presigned URL is a non-empty string."""
    job_id = "bundle-test-url"
    seed_s3_result(job_id)

    url = await bundle_service.create_bundle(job_id, "test.pdf")

    assert isinstance(url, str)
    assert len(url) > 0
    assert "bundle.zip" in url or "test" in url


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_create_bundle_zip_structure(bundle_service, seed_s3_result):
    """Verify internal zip paths follow naming convention."""
    job_id = "bundle-test-structure"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    seed_s3_result(
        job_id,
        markdown="# Structured Doc",
        figures={"diagram.png": fake_png},
    )

    await bundle_service.create_bundle(job_id, "Chapter 4 Reading.pdf")

    response = bundle_service.storage.s3_client.get_object(
        Bucket=settings.s3_results_bucket,
        Key=f"results/{job_id}/bundle.zip",
    )
    zip_bytes = response["Body"].read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        # Folder uses {base_name}-reflow convention
        assert "Chapter 4 Reading-reflow/Chapter 4 Reading.md" in names
        assert "Chapter 4 Reading-reflow/images/diagram.png" in names


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_create_bundle_missing_markdown_raises(bundle_service):
    """No result.md in S3 → BundleError."""
    with pytest.raises(BundleError, match="No result markdown found"):
        await bundle_service.create_bundle("nonexistent-job-id", "missing.pdf")


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_create_bundle_skips_missing_figures(bundle_service, seed_s3_result):
    """Missing figure doesn't fail — bundle still created with markdown only."""
    job_id = "bundle-test-no-figs"
    # Seed markdown only, no figures
    seed_s3_result(job_id, markdown="# No Figures\n\nText only.")

    url = await bundle_service.create_bundle(job_id, "text_only.pdf")

    assert url
    response = bundle_service.storage.s3_client.get_object(
        Bucket=settings.s3_results_bucket,
        Key=f"results/{job_id}/bundle.zip",
    )
    zip_bytes = response["Body"].read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "text_only-reflow/text_only.md" in names
        # No images/ entries
        assert not any("images/" in n for n in names)
