# PRD-02: Markdown-to-HTML Renderer

## Problem Statement

The processing pipeline outputs semantic markdown. Canvas Pages accept HTML via their API. There is no component that converts the pipeline's markdown output into Canvas-compatible, semantic HTML.

Canvas Pages sanitize HTML (strip `<script>`, event handlers, some CSS properties) but preserve semantic elements (`<article>`, `<section>`, `<figure>`, `<figcaption>`, `<table>` with `<thead>`/`<tbody>`, ARIA attributes, CSS classes, inline styles).

## Goal

A markdown-to-HTML renderer that produces semantic, Canvas-compatible HTML from the pipeline's markdown output.

## Dependencies

None. Uses existing markdown output format from the pipeline.

## Requirements

### R1: Renderer class

Create `src/canvas/renderer.py` with a renderer that converts markdown to HTML.

```python
# src/canvas/renderer.py

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
        ...
```

### R2: Semantic HTML output

The renderer must produce:
- `<article>` wrapper around the full content
- Heading hierarchy preserved (`<h1>` through `<h6>`)
- Paragraphs as `<p>` tags
- Lists as `<ul>`/`<ol>` with `<li>`
- Images wrapped in `<figure>` with `<figcaption>` when alt text is present:
  ```html
  <figure>
    <img src="canvas-url" alt="Description of figure">
    <figcaption>Description of figure</figcaption>
  </figure>
  ```
- Tables with `<table>`, `<thead>`, `<tbody>`, `<th scope="col">` for header cells
- Code blocks with `<pre><code class="language-{lang}">`
- Blockquotes as `<blockquote>`
- Horizontal rules as `<hr>`
- Bold/italic/inline code preserved

### R3: Image URL rewriting

When `image_url_map` is provided, replace markdown image references with Canvas-hosted URLs. The pipeline outputs images as relative paths like `images/figure_1.png`. The map translates these to Canvas file URLs.

If an image path is not in the map, keep the original path and log a warning.

### R4: Download footer

When `download_url` is provided, append a footer section to the HTML:

```html
<hr>
<footer>
  <p><a href="{download_url}" download>Download markdown and images</a></p>
</footer>
```

### R5: Use mistune

Use `mistune` (already a transitive dependency via Docling) with a custom renderer. Do not add new dependencies.

```python
import mistune

class _CanvasRenderer(mistune.BaseRenderer):
    # Override methods: heading, paragraph, image, table, etc.
    ...

def _create_parser() -> mistune.Markdown:
    renderer = _CanvasRenderer()
    return mistune.create_markdown(renderer=renderer)
```

## Implementation Notes

### Files to create:
1. `src/canvas/renderer.py` -- the renderer class

### Files to modify:
None.

### Design decisions:
- Use mistune (already available) rather than adding markdown-it-py
- Custom renderer class for full control over HTML output
- No inline CSS in v1 -- rely on Canvas's built-in stylesheet. Add inline styles later if testing shows Canvas strips needed elements
- Image rewriting is a simple string replacement, not AST manipulation

## Success Criteria

- [x] `src/canvas/renderer.py` exists with `CanvasHTMLRenderer` class
- [x] `CanvasHTMLRenderer.render()` accepts markdown string and returns HTML string
- [x] Output HTML is wrapped in `<article>` tags
- [x] Headings render as `<h1>` through `<h6>` preserving the original level
- [x] Images render as `<figure><img src="..." alt="..."><figcaption>...</figcaption></figure>` when alt text is present
- [x] Images without alt text render as `<img src="..." alt="">` without figure wrapper
- [x] Tables render with `<table>`, `<thead>`, `<tbody>`, and `<th scope="col">` for header cells
- [x] When `image_url_map` is provided, image `src` attributes are replaced with Canvas URLs
- [x] When an image path is not in `image_url_map`, the original path is kept and a warning is logged
- [x] When `download_url` is provided, a footer with download link is appended after an `<hr>`
- [x] The renderer uses `mistune` and does not add new dependencies to `pyproject.toml`
- [x] Code blocks render as `<pre><code class="language-{lang}">` with the language class
