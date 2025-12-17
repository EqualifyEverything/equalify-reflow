"""OCR error detection using spell checking.

This module provides zero-cost OCR error detection by:
1. Generating OCR-confused variants of key terms (l/1/I, O/0, rn/m, etc.)
2. Using spell checking to flag potential errors
3. NEVER auto-correcting - only flagging for LLM review with context

The OCR checker is used in the Structure Verification Loop (Phase 3a)
to detect potential OCR errors that the LLM should review in context.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from spellchecker import SpellChecker

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class OCRSuggestion(BaseModel):
    """Potential OCR error flagged for LLM review.

    This model represents a word that may be an OCR error, along with
    suggestions for the correct spelling. The LLM will decide whether
    to fix it based on document context.

    IMPORTANT: OCR suggestions are NEVER auto-corrected. The LLM must
    make the decision in context because OCR errors are semantic.

    Attributes:
        word: The potentially misspelled word
        suggestions: Possible correct spellings (prioritizes key terms)
        confidence: How confident we are this is an OCR error (0.0-1.0)
        reason: Why this was flagged (key_term_variant, spell_check_suggests_key_term)
        context: Surrounding text (~100 chars) for LLM context

    Example:
        >>> suggestion = OCRSuggestion(
        ...     word="Exxon",
        ...     suggestions=["Enzo"],
        ...     confidence=0.95,
        ...     reason="key_term_variant",
        ...     context="...Both Exxon and yt are developed using..."
        ... )
    """

    word: str = Field(..., description="The potentially misspelled word")
    suggestions: list[str] = Field(
        default_factory=list,
        description="Possible correct spellings"
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence this is an OCR error"
    )
    reason: str = Field(
        ...,
        description="Why flagged: key_term_variant, spell_check_suggests_key_term, common_ocr_pattern"
    )
    context: str = Field(
        default="",
        description="Surrounding text for LLM context"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "word": "Exxon",
                "suggestions": ["Enzo"],
                "confidence": 0.95,
                "reason": "key_term_variant",
                "context": "...Both Exxon and yt are developed using..."
            }
        }
    )


class OCRChecker:
    """Zero-cost OCR error detection using spell checking.

    This class detects potential OCR errors by:
    1. Checking if words match OCR-confused variants of key terms
    2. Using spell checking to find misspellings that suggest key terms
    3. Detecting common OCR confusion patterns

    IMPORTANT: This class NEVER auto-corrects. It only flags potential
    errors for LLM review. The LLM decides whether to fix based on
    document context.

    Example:
        >>> checker = OCRChecker()
        >>> suggestions = checker.check_text(
        ...     text="Both Exxon and yt are developed using DVCS.",
        ...     key_terms=["Enzo", "yt", "DVCS"]
        ... )
        >>> for s in suggestions:
        ...     print(f"{s.word} -> {s.suggestions} ({s.reason})")
        Exxon -> ['Enzo'] (key_term_variant)
    """

    # Common OCR character confusions
    # Each tuple contains characters that are often confused by OCR
    COMMON_CONFUSIONS: list[tuple[str, ...]] = [
        ("l", "1", "I", "|"),      # lowercase L, one, uppercase I, pipe
        ("O", "0"),                 # uppercase O, zero
        ("rn", "m"),                # rn looks like m
        ("cl", "d"),                # cl looks like d
        ("vv", "w"),                # vv looks like w
        ("ii", "u"),                # ii looks like u
        ("fi", "fi"),               # fi ligature issues
        ("fl", "fl"),               # fl ligature issues
        ("S", "5"),                 # S and 5
        ("B", "8"),                 # B and 8
        ("Z", "2"),                 # Z and 2
        ("G", "6"),                 # G and 6
    ]

    def __init__(self) -> None:
        """Initialize OCR checker with spell checker."""
        self._spell = SpellChecker()
        self._key_term_variants: dict[str, str] = {}
        self._key_terms_lower: set[str] = set()

    def load_key_terms(self, key_terms: list[str]) -> None:
        """Add document-specific terms as known words.

        This method:
        1. Adds key terms to the spell checker's known words
        2. Generates OCR-confused variants for detection

        Args:
            key_terms: List of document-specific terms (names, projects, etc.)
        """
        # Add terms to spell checker as known words
        self._spell.word_frequency.load_words(key_terms)

        # Store lowercase versions for quick lookup
        self._key_terms_lower = {term.lower() for term in key_terms}

        # Generate OCR-confused variants for detection
        self._key_term_variants = self._generate_variants(key_terms)

        logger.debug(
            f"Loaded {len(key_terms)} key terms, "
            f"generated {len(self._key_term_variants)} variants"
        )

    def _generate_variants(self, terms: list[str]) -> dict[str, str]:
        """Generate OCR-confused variants of key terms.

        For each key term, generates variants by applying common OCR
        confusion patterns. These variants are used to detect potential
        OCR errors in the document.

        Args:
            terms: List of key terms to generate variants for

        Returns:
            Dict mapping variant -> original term
        """
        variants: dict[str, str] = {}

        for term in terms:
            term_lower = term.lower()

            # Apply each confusion pattern
            for confusion in self.COMMON_CONFUSIONS:
                for i, char in enumerate(confusion):
                    for replacement in confusion:
                        if char == replacement:
                            continue

                        # Try replacing in the term
                        if char in term_lower:
                            variant = term_lower.replace(char, replacement)
                            if variant != term_lower and variant not in self._key_terms_lower:
                                variants[variant] = term

        return variants

    def check_text(
        self,
        text: str,
        key_terms: list[str],
    ) -> list[OCRSuggestion]:
        """Find potential OCR errors - NEVER auto-corrects, only flags.

        This method scans the text for potential OCR errors by:
        1. Checking if words match OCR-confused variants of key terms
        2. Using spell checking to find words that might be key terms
        3. Detecting common OCR confusion patterns

        Args:
            text: The text to check for OCR errors
            key_terms: Document-specific terms for context

        Returns:
            List of OCRSuggestion objects for LLM review
        """
        # Load key terms (idempotent, but ensures fresh state)
        self.load_key_terms(key_terms)

        suggestions: list[OCRSuggestion] = []
        seen_words: set[str] = set()

        for word in self._extract_words(text):
            # Skip very short words, numbers, and duplicates
            if len(word) < 3 or word.isdigit() or word.lower() in seen_words:
                continue

            seen_words.add(word.lower())

            # Skip if word is a known key term
            if word.lower() in self._key_terms_lower:
                continue

            # Check 1: Is this a confused variant of a key term?
            if word.lower() in self._key_term_variants:
                original = self._key_term_variants[word.lower()]
                suggestions.append(OCRSuggestion(
                    word=word,
                    suggestions=[original],
                    confidence=0.95,
                    reason="key_term_variant",
                    context=self._get_context(text, word),
                ))
                continue

            # Check 2: Spell check suggests this might be wrong
            if self._spell.unknown([word]):
                # Get spell checker suggestions
                spell_suggestions = list(self._spell.candidates(word) or [])

                # Check if any suggestion is a key term
                key_term_matches = [
                    s for s in spell_suggestions
                    if s.lower() in self._key_terms_lower
                ]

                if key_term_matches:
                    suggestions.append(OCRSuggestion(
                        word=word,
                        suggestions=key_term_matches[:3],  # Limit to 3
                        confidence=0.85,
                        reason="spell_check_suggests_key_term",
                        context=self._get_context(text, word),
                    ))
                    continue

                # Check 3: Common OCR pattern detected
                if self._has_ocr_pattern(word):
                    # Get top spell suggestions
                    top_suggestions = spell_suggestions[:3] if spell_suggestions else []
                    if top_suggestions:
                        suggestions.append(OCRSuggestion(
                            word=word,
                            suggestions=top_suggestions,
                            confidence=0.70,
                            reason="common_ocr_pattern",
                            context=self._get_context(text, word),
                        ))

        logger.debug(f"Found {len(suggestions)} potential OCR errors")
        return suggestions

    def _extract_words(self, text: str) -> list[str]:
        """Extract words from text.

        Args:
            text: Text to extract words from

        Returns:
            List of words (alphabetic characters only)
        """
        return re.findall(r"\b[a-zA-Z]+\b", text)

    def _get_context(self, text: str, word: str, window: int = 50) -> str:
        """Get surrounding context for a word.

        Args:
            text: Full text
            word: Word to get context for
            window: Characters before/after to include

        Returns:
            Context string with ellipsis markers
        """
        # Find word position (case-insensitive)
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        match = pattern.search(text)

        if not match:
            return ""

        idx = match.start()
        start = max(0, idx - window)
        end = min(len(text), idx + len(word) + window)

        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""

        return f"{prefix}{text[start:end]}{suffix}"

    def _has_ocr_pattern(self, word: str) -> bool:
        """Check if word contains common OCR confusion patterns.

        Args:
            word: Word to check

        Returns:
            True if word contains patterns commonly produced by OCR errors
        """
        word_lower = word.lower()

        # Check for patterns that suggest OCR errors
        ocr_patterns = [
            r"[0-9][a-z]",      # Digit followed by letter (l1ke, 0kay)
            r"[a-z][0-9]",      # Letter followed by digit (lik3, okay0)
            r"rn",              # rn often confused with m
            r"[Il1|]{2,}",      # Multiple l/I/1/| in sequence
            r"vv",              # vv often confused with w
        ]

        for pattern in ocr_patterns:
            if re.search(pattern, word_lower):
                return True

        return False


__all__ = ["OCRChecker", "OCRSuggestion"]
