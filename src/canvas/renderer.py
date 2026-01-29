"""Markdown-to-HTML renderer for Canvas Pages.

Converts semantic markdown from the processing pipeline into
Canvas-compatible HTML using mistune with a custom renderer.
"""

import logging
from html import escape as html_escape

import mistune

logger = logging.getLogger(__name__)


class _CanvasRenderer(mistune.HTMLRenderer):
    """Custom mistune renderer producing semantic, Canvas-compatible HTML."""

    def __init__(self, image_url_map: dict[str, str] | None = None) -> None:
        super().__init__()
        self._image_url_map = image_url_map or {}

    def heading(self, text: str, level: int, **attrs: object) -> str:
        return f"<h{level}>{text}</h{level}>\n"

    def paragraph(self, text: str) -> str:
        return f"<p>{text}</p>\n"

    def image(self, alt: str, url: str, title: str | None = None) -> str:
        resolved_url = self._resolve_image_url(url)
        safe_url = html_escape(resolved_url)
        # alt is already HTML-escaped by mistune's tokenizer
        safe_alt = alt or ""

        if alt:
            return f'<figure><img src="{safe_url}" alt="{safe_alt}"><figcaption>{safe_alt}</figcaption></figure>\n'
        return f'<img src="{safe_url}" alt="">\n'

    def _resolve_image_url(self, url: str) -> str:
        """Resolve an image URL using the image_url_map."""
        if url in self._image_url_map:
            return self._image_url_map[url]
        if self._image_url_map and url:
            logger.warning("Image path not found in image_url_map: %s", url)
        return url

    def block_code(self, code: str, info: str | None = None) -> str:
        lang = info.split()[0] if info else ""
        safe_code = html_escape(code)
        if lang:
            return f'<pre><code class="language-{html_escape(lang)}">{safe_code}</code></pre>\n'
        return f"<pre><code>{safe_code}</code></pre>\n"

    def block_quote(self, text: str) -> str:
        return f"<blockquote>\n{text}</blockquote>\n"

    def thematic_break(self) -> str:
        return "<hr>\n"

    def list(self, text: str, ordered: bool, **attrs: object) -> str:
        tag = "ol" if ordered else "ul"
        return f"<{tag}>\n{text}</{tag}>\n"

    def list_item(self, text: str, **attrs: object) -> str:
        return f"<li>{text}</li>\n"

    # Table methods (provided by the table plugin)
    def table(self, text: str) -> str:
        return f"<table>\n{text}</table>\n"

    def table_head(self, text: str) -> str:
        return f"<thead>\n<tr>\n{text}</tr>\n</thead>\n"

    def table_body(self, text: str) -> str:
        return f"<tbody>\n{text}</tbody>\n"

    def table_row(self, text: str) -> str:
        return f"<tr>\n{text}</tr>\n"

    def table_cell(self, text: str, align: str | None = None, head: bool = False) -> str:
        if head:
            return f'<th scope="col">{text}</th>\n'
        return f"<td>{text}</td>\n"

    def emphasis(self, text: str) -> str:
        return f"<em>{text}</em>"

    def strong(self, text: str) -> str:
        return f"<strong>{text}</strong>"

    def codespan(self, text: str) -> str:
        return f"<code>{html_escape(text)}</code>"

    def link(self, text: str, url: str, title: str | None = None) -> str:
        safe_url = html_escape(url)
        if title:
            return f'<a href="{safe_url}" title="{html_escape(title)}">{text}</a>'
        return f'<a href="{safe_url}">{text}</a>'

    def linebreak(self) -> str:
        return "<br>\n"


def _create_parser(image_url_map: dict[str, str] | None = None) -> mistune.Markdown:
    """Create a mistune parser with the Canvas renderer."""
    renderer = _CanvasRenderer(image_url_map=image_url_map)
    return mistune.create_markdown(renderer=renderer, plugins=["table"])


class CanvasHTMLRenderer:
    """Convert semantic markdown to Canvas-compatible HTML."""

    def render(
        self,
        markdown_content: str,
        image_url_map: dict[str, str] | None = None,
        download_url: str | None = None,
    ) -> str:
        """Render markdown to Canvas Page HTML.

        Args:
            markdown_content: Semantic markdown from the pipeline
            image_url_map: Map of original image paths to Canvas-hosted URLs
                e.g., {"images/figure_1.png": "https://canvas.uic.edu/courses/1/files/42/preview"}
            download_url: Optional URL for markdown+assets zip download

        Returns:
            HTML string suitable for Canvas Pages API wiki_page[body] field
        """
        parser = _create_parser(image_url_map=image_url_map)
        body_html = str(parser(markdown_content))

        parts = ["<article>\n", body_html]

        if download_url:
            safe_url = html_escape(download_url)
            parts.append("<hr>\n<footer>\n")
            parts.append(f'<p><a href="{safe_url}" download>Download markdown and images</a></p>\n')
            parts.append("</footer>\n")

        parts.append("</article>\n")
        return "".join(parts)
