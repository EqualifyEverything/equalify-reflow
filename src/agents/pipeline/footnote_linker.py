"""Footnote linker for connecting markers to definitions.

Links footnote markers found in text elements to their definitions
found in FOOTNOTE elements from Docling extraction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.api.schemas import FootnoteLink

from .models import ElementType, ProcessedElement

logger = logging.getLogger(__name__)


@dataclass
class FootnoteDefinition:
    """Internal representation of a footnote definition."""

    text: str
    page: int
    marker: str


class FootnoteLinker:
    """Links footnote markers in text to their definitions.

    Supports multiple footnote styles:
    - Numbered superscripts: 1, 2, 3
    - Bracketed numbers: [1], [2], [3]
    - Symbols: *, †, ‡, §, ¶
    - Parenthetical: (1), (2), (3)
    """

    # Common footnote marker patterns
    MARKER_PATTERNS = [
        # [1], [2], etc. - bracketed numbers (most explicit)
        (r"\[(\d+)\]", "bracketed"),
        # Superscript indicators often rendered as ^1 or similar
        (r"\^(\d+)", "superscript"),
        # Symbol markers: *, †, ‡, §, ¶
        (r"([*†‡§¶])", "symbol"),
        # Note: Plain numbers like "1" are too ambiguous for text matching
    ]

    # Pattern to extract marker from definition start
    DEFINITION_MARKER_PATTERNS = [
        r"^\s*\[?(\d+)\]?[\.\:\s]",  # "1." or "[1]" or "1:"
        r"^\s*([*†‡§¶])\s",  # Symbol at start
        r"^\s*\((\d+)\)\s",  # "(1) "
    ]

    def link_footnotes(
        self,
        elements: list[ProcessedElement],
        footnote_style: str = "none",
    ) -> list[FootnoteLink]:
        """Find and link footnote references to definitions.

        Args:
            elements: List of processed document elements
            footnote_style: Style hint from analysis (numbered_superscript, symbols, etc.)

        Returns:
            List of FootnoteLink objects connecting markers to definitions
        """
        # Collect all footnote definitions
        definitions = self._collect_definitions(elements)

        if not definitions:
            logger.debug("No footnote definitions found")
            return []

        logger.info(f"Found {len(definitions)} footnote definitions")

        # Find markers in text and match to definitions
        links: list[FootnoteLink] = []
        seen_markers: set[str] = set()  # Avoid duplicate links

        for elem in elements:
            if elem.element_type not in (
                ElementType.TEXT,
                ElementType.SECTION_HEADER,
            ):
                continue

            text = elem.processed_text or elem.original_text
            if not text:
                continue

            page = elem.provenance.docling_page if elem.provenance else 1

            # Find markers in this text
            markers = self._find_markers(text, footnote_style)

            for marker in markers:
                # Skip if we've already linked this marker
                if marker in seen_markers:
                    continue

                # Look up definition
                if marker in definitions:
                    defn = definitions[marker]
                    links.append(
                        FootnoteLink(
                            marker=marker,
                            reference_text=text[:100] + ("..." if len(text) > 100 else ""),
                            reference_page=page,
                            definition_page=defn.page,
                            definition_text=defn.text,
                        )
                    )
                    seen_markers.add(marker)
                    logger.debug(
                        f"Linked footnote '{marker}' from page {page} to page {defn.page}"
                    )

        logger.info(f"Created {len(links)} footnote links")
        return links

    def _collect_definitions(
        self, elements: list[ProcessedElement]
    ) -> dict[str, FootnoteDefinition]:
        """Collect footnote definitions from FOOTNOTE elements.

        Args:
            elements: List of processed document elements

        Returns:
            Dict mapping marker to FootnoteDefinition
        """
        definitions: dict[str, FootnoteDefinition] = {}

        for elem in elements:
            if elem.element_type != ElementType.FOOTNOTE:
                continue

            text = elem.processed_text or elem.original_text
            if not text:
                continue

            page = elem.provenance.docling_page if elem.provenance else 1

            # Extract marker from definition
            marker = self._extract_marker_from_definition(text)
            if marker:
                definitions[marker] = FootnoteDefinition(
                    text=text,
                    page=page,
                    marker=marker,
                )
                logger.debug(f"Found footnote definition '{marker}' on page {page}")

        return definitions

    def _extract_marker_from_definition(self, text: str) -> str | None:
        """Extract the marker from a footnote definition text.

        Footnotes typically start with their marker:
        - "1. This is the footnote text..."
        - "[1] This is the footnote text..."
        - "* This is the footnote text..."

        Args:
            text: Footnote definition text

        Returns:
            Marker string or None if not found
        """
        for pattern in self.DEFINITION_MARKER_PATTERNS:
            match = re.match(pattern, text)
            if match:
                return match.group(1)

        # Fallback: if text starts with a number followed by punctuation
        match = re.match(r"^\s*(\d+)[.\)\]:\s]", text)
        if match:
            return match.group(1)

        # Fallback: try to find symbol at start
        match = re.match(r"^\s*([*†‡§¶])[\s\.]", text)
        if match:
            return match.group(1)

        logger.debug(f"Could not extract marker from: {text[:50]}...")
        return None

    def _find_markers(self, text: str, footnote_style: str) -> list[str]:
        """Find footnote markers in text.

        Args:
            text: Text to search
            footnote_style: Style hint from analysis

        Returns:
            List of marker strings found
        """
        markers: list[str] = []

        # Choose patterns based on style hint
        patterns_to_use = self.MARKER_PATTERNS

        # If we have style hints, prioritize certain patterns
        if footnote_style == "numbered_superscript":
            # Prioritize numeric patterns
            patterns_to_use = [
                (r"\[(\d+)\]", "bracketed"),
                (r"\^(\d+)", "superscript"),
            ]
        elif footnote_style == "symbols":
            patterns_to_use = [
                (r"([*†‡§¶])", "symbol"),
            ]

        for pattern, _ in patterns_to_use:
            for match in re.finditer(pattern, text):
                marker = match.group(1)
                if marker not in markers:
                    markers.append(marker)

        return markers


def link_footnotes(
    elements: list[ProcessedElement],
    footnote_style: str = "none",
) -> list[FootnoteLink]:
    """Convenience function to link footnotes.

    Args:
        elements: List of processed document elements
        footnote_style: Style hint from analysis

    Returns:
        List of FootnoteLink objects
    """
    linker = FootnoteLinker()
    return linker.link_footnotes(elements, footnote_style)
