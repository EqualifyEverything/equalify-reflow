"""Shared helper functions for agents.

Functions used across multiple specialized agents for
page feature lookup and markdown extraction.
"""

from src.shared.models.remediation import DocumentManifest, PageFeatures


def get_page_features(manifest: DocumentManifest, page_num: int) -> PageFeatures | None:
    """Get PageFeatures for a specific page number.

    Args:
        manifest: Document manifest containing page features
        page_num: Page number to look up

    Returns:
        PageFeatures for the page, or None if not found
    """
    for pf in manifest.page_features:
        if pf.page_num == page_num:
            return pf
    return None


def extract_page_markdown(markdown: str, page_num: int) -> str:
    """Extract approximate markdown section for a page.

    Uses a chunking approach to estimate the markdown content
    for a specific page based on total line count.

    Args:
        markdown: Full document markdown
        page_num: Page number (1-indexed)

    Returns:
        Estimated markdown content for the page
    """
    lines = markdown.split("\n")
    if not lines:
        return ""

    chunk_size = max(len(lines) // 10, 30)
    start = (page_num - 1) * chunk_size
    end = min(page_num * chunk_size, len(lines))

    return "\n".join(lines[start:end])
