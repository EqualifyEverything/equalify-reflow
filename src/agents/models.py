"""Pipeline data models.

Minimal stub — most models were removed with the agentic pipeline.
Only StoredFigure remains (used by StorageService for figure upload tracking).
"""

from pydantic import BaseModel, Field


class StoredFigure(BaseModel):
    """Metadata for a figure image stored to S3."""

    figure_id: str = Field(..., description="Sequential identifier (e.g., 'figure-1')")
    s3_key: str = Field(..., description="Full S3 key in results bucket")
    page_num: int = Field(..., ge=1, description="Source page number (1-indexed)")
    alt_text: str = Field(default="", description="Generated alt text")
    caption: str = Field(default="", description="Original caption from PDF")
    ref_id: str = Field(..., description="Original Docling reference (e.g., '#/pictures/0')")
