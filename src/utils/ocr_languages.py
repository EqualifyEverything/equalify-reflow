"""OCR language utilities.

Provides Tesseract-to-EasyOCR code mapping (used by docling-serve which
defaults to EasyOCR) and language validation against the known mapping table.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tesseract → EasyOCR language code mapping
# ---------------------------------------------------------------------------

# docling-serve defaults to EasyOCR, which uses ISO 639-1 codes.
# The pipeline stores Tesseract codes (ISO 639-3 / tessdata names).
TESSERACT_TO_EASYOCR: dict[str, str] = {
    "eng": "en",
    "spa": "es",
    "fra": "fr",
    "deu": "de",
    "chi_sim": "ch_sim",
    "chi_tra": "ch_tra",
    "jpn": "ja",
    "kor": "ko",
    "ara": "ar",
    "hin": "hi",
    "por": "pt",
    "ita": "it",
    "rus": "ru",
    "nld": "nl",
    "pol": "pl",
    "tur": "tr",
    "vie": "vi",
    "tha": "th",
    "ukr": "uk",
    "ces": "cs",
    "swe": "sv",
    "nor": "no",
    "dan": "da",
    "fin": "fi",
    "hun": "hu",
    "ron": "ro",
    "bul": "bg",
    "hrv": "hr",
    "slk": "sk",
    "slv": "sl",
    "ell": "el",
    "heb": "he",
    "ind": "id",
    "msa": "ms",
    "ben": "bn",
    "tam": "ta",
    "tel": "te",
    "kan": "kn",
    "mal": "ml",
    "mar": "mr",
    "nep": "ne",
    "urd": "ur",
}


def validate_ocr_languages(languages: list[str]) -> list[str]:
    """Validate that the given Tesseract language codes are recognized.

    Checks against the known mapping table. Unrecognized codes are returned
    as invalid (they may still work if EasyOCR supports them directly).

    Args:
        languages: List of Tesseract language codes to validate.

    Returns:
        List of invalid language codes (empty if all are recognized).
    """
    return [lang for lang in languages if lang not in TESSERACT_TO_EASYOCR]


def tesseract_to_easyocr(languages: list[str]) -> list[str]:
    """Convert Tesseract language codes to EasyOCR codes.

    Unknown codes are passed through unchanged (EasyOCR may still accept them).

    Args:
        languages: List of Tesseract language codes (e.g. ``["eng", "deu"]``).

    Returns:
        List of EasyOCR codes (e.g. ``["en", "de"]``).
    """
    result: list[str] = []
    for lang in languages:
        mapped = TESSERACT_TO_EASYOCR.get(lang, lang)
        if mapped != lang:
            logger.debug("OCR language mapped: %s → %s", lang, mapped)
        result.append(mapped)
    return result
