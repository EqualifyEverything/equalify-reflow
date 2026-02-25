"""Tesseract OCR language validation utilities.

Provides helpers to discover installed Tesseract language packs and
validate caller-supplied language codes against what is available on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.config import settings

logger = logging.getLogger(__name__)

# Module-level cache for installed languages
_installed_languages: frozenset[str] | None = None


def get_installed_languages() -> frozenset[str]:
    """Return the set of Tesseract language codes installed on this system.

    Scans ``settings.tessdata_prefix`` for ``*.traineddata`` files and caches
    the result in a module-level variable so the filesystem is only read once.

    Returns:
        Frozenset of language codes (e.g. ``{"eng", "fra", "deu"}``).
        Returns an empty frozenset if the tessdata directory does not exist.
    """
    global _installed_languages  # noqa: PLW0603

    if _installed_languages is not None:
        return _installed_languages

    tessdata_dir = Path(settings.tessdata_prefix)

    if not tessdata_dir.is_dir():
        logger.warning("Tessdata directory not found: %s", tessdata_dir)
        _installed_languages = frozenset()
        return _installed_languages

    languages = frozenset(p.stem for p in tessdata_dir.glob("*.traineddata"))
    logger.info("Found %d installed Tesseract languages", len(languages))
    _installed_languages = languages
    return _installed_languages


def validate_ocr_languages(languages: list[str]) -> list[str]:
    """Validate that the given Tesseract language codes are installed.

    Args:
        languages: List of Tesseract language codes to validate.

    Returns:
        List of invalid language codes (empty if all are valid).
        If the tessdata directory is not found (e.g. running outside Docker),
        validation is skipped and an empty list is returned.
    """
    tessdata_dir = Path(settings.tessdata_prefix)

    if not tessdata_dir.is_dir():
        logger.debug("Tessdata directory not found, skipping validation")
        return []

    installed = get_installed_languages()
    return [lang for lang in languages if lang not in installed]
