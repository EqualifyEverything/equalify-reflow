"""Unit tests for Canvas HTML renderer."""

import logging

import pytest
from src.canvas.renderer import CanvasHTMLRenderer

pytestmark = pytest.mark.unit


@pytest.fixture
def renderer():
    """Create a CanvasHTMLRenderer for testing."""
    return CanvasHTMLRenderer()


class TestArticleWrapper:
    """R2: Output HTML is wrapped in <article> tags."""

    def test_empty_input_wraps_in_article(self, renderer):
        result = renderer.render("")
        assert result.startswith("<article>\n")
        assert result.endswith("</article>\n")

    def test_simple_paragraph_wraps_in_article(self, renderer):
        result = renderer.render("Hello world")
        assert result.startswith("<article>\n")
        assert result.endswith("</article>\n")
        assert "<p>Hello world</p>" in result


class TestHeadings:
    """R2: Heading hierarchy preserved (h1-h6)."""

    @pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
    def test_heading_levels(self, renderer, level):
        md = "#" * level + " Test Heading"
        result = renderer.render(md)
        assert f"<h{level}>Test Heading</h{level}>" in result

    def test_multiple_headings(self, renderer):
        md = "# Title\n\n## Subtitle\n\n### Section"
        result = renderer.render(md)
        assert "<h1>Title</h1>" in result
        assert "<h2>Subtitle</h2>" in result
        assert "<h3>Section</h3>" in result


class TestParagraphs:
    """R2: Paragraphs as <p> tags."""

    def test_simple_paragraph(self, renderer):
        result = renderer.render("Some text here.")
        assert "<p>Some text here.</p>" in result

    def test_multiple_paragraphs(self, renderer):
        md = "First paragraph.\n\nSecond paragraph."
        result = renderer.render(md)
        assert "<p>First paragraph.</p>" in result
        assert "<p>Second paragraph.</p>" in result


class TestLists:
    """R2: Lists as <ul>/<ol> with <li>."""

    def test_unordered_list(self, renderer):
        md = "- Item one\n- Item two\n- Item three"
        result = renderer.render(md)
        assert "<ul>" in result
        assert "</ul>" in result
        assert "<li>" in result
        assert "Item one" in result
        assert "Item two" in result
        assert "Item three" in result

    def test_ordered_list(self, renderer):
        md = "1. First\n2. Second\n3. Third"
        result = renderer.render(md)
        assert "<ol>" in result
        assert "</ol>" in result
        assert "<li>" in result
        assert "First" in result
        assert "Second" in result


class TestImages:
    """R2/R3: Image rendering and URL rewriting."""

    def test_image_with_alt_text_renders_figure(self, renderer):
        md = '![Description of figure](images/figure_1.png)'
        result = renderer.render(md)
        assert "<figure>" in result
        assert '<img src="images/figure_1.png" alt="Description of figure">' in result
        assert "<figcaption>Description of figure</figcaption>" in result
        assert "</figure>" in result

    def test_image_without_alt_text_no_figure(self, renderer):
        md = '![](images/figure_1.png)'
        result = renderer.render(md)
        assert "<figure>" not in result
        assert '<img src="images/figure_1.png" alt="">' in result

    def test_image_url_rewriting(self, renderer):
        image_map = {"images/figure_1.png": "https://canvas.uic.edu/courses/1/files/42/preview"}
        md = '![A chart](images/figure_1.png)'
        result = renderer.render(md, image_url_map=image_map)
        assert 'src="https://canvas.uic.edu/courses/1/files/42/preview"' in result
        assert "images/figure_1.png" not in result

    def test_image_url_not_in_map_keeps_original(self, renderer, caplog):
        image_map = {"images/other.png": "https://canvas.uic.edu/courses/1/files/99/preview"}
        md = '![Missing image](images/figure_1.png)'
        with caplog.at_level(logging.WARNING, logger="src.canvas.renderer"):
            result = renderer.render(md, image_url_map=image_map)
        assert 'src="images/figure_1.png"' in result
        assert "Image path not found in image_url_map: images/figure_1.png" in caplog.text

    def test_image_url_rewriting_multiple_images(self, renderer):
        image_map = {
            "images/fig1.png": "https://canvas.uic.edu/files/1/preview",
            "images/fig2.png": "https://canvas.uic.edu/files/2/preview",
        }
        md = '![Fig 1](images/fig1.png)\n\n![Fig 2](images/fig2.png)'
        result = renderer.render(md, image_url_map=image_map)
        assert 'src="https://canvas.uic.edu/files/1/preview"' in result
        assert 'src="https://canvas.uic.edu/files/2/preview"' in result

    def test_image_no_map_no_warning(self, renderer, caplog):
        md = '![Alt](images/figure_1.png)'
        with caplog.at_level(logging.WARNING, logger="src.canvas.renderer"):
            renderer.render(md)
        assert "Image path not found" not in caplog.text

    def test_image_alt_text_html_escaped(self, renderer):
        md = '![Description with <tags> & "quotes"](images/fig.png)'
        result = renderer.render(md)
        # mistune pre-escapes alt text; verify it appears escaped in the output
        assert "&lt;tags&gt;" in result
        assert "&amp;" in result


class TestTables:
    """R2: Tables with <table>, <thead>, <tbody>, <th scope="col">."""

    def test_table_structure(self, renderer):
        md = "| Header 1 | Header 2 |\n|----------|----------|\n| Cell 1   | Cell 2   |"
        result = renderer.render(md)
        assert "<table>" in result
        assert "</table>" in result
        assert "<thead>" in result
        assert "</thead>" in result
        assert "<tbody>" in result
        assert "</tbody>" in result

    def test_table_header_cells_have_scope(self, renderer):
        md = "| Name | Value |\n|------|-------|\n| A    | 1     |"
        result = renderer.render(md)
        assert '<th scope="col">' in result
        assert "Name" in result
        assert "Value" in result

    def test_table_body_cells(self, renderer):
        md = "| H1 | H2 |\n|----|----|\n| D1 | D2 |"
        result = renderer.render(md)
        assert "<td>D1</td>" in result
        assert "<td>D2</td>" in result

    def test_table_multiple_rows(self, renderer):
        md = "| H |\n|---|\n| R1 |\n| R2 |\n| R3 |"
        result = renderer.render(md)
        assert result.count("<tr>") == 4  # 1 header row + 3 body rows


class TestCodeBlocks:
    """R2: Code blocks with <pre><code class="language-{lang}">."""

    def test_code_block_with_language(self, renderer):
        md = "```python\nprint('hello')\n```"
        result = renderer.render(md)
        assert '<pre><code class="language-python">' in result
        assert "print(&#x27;hello&#x27;)" in result or "print(&#39;hello&#39;)" in result
        assert "</code></pre>" in result

    def test_code_block_without_language(self, renderer):
        md = "```\nsome code\n```"
        result = renderer.render(md)
        assert "<pre><code>" in result
        assert "some code" in result
        assert "</code></pre>" in result

    def test_code_block_html_escaped(self, renderer):
        md = '```html\n<div class="test">&amp;</div>\n```'
        result = renderer.render(md)
        assert "&lt;div" in result
        assert "&amp;amp;" in result


class TestBlockquotes:
    """R2: Blockquotes as <blockquote>."""

    def test_blockquote(self, renderer):
        md = "> This is a quote"
        result = renderer.render(md)
        assert "<blockquote>" in result
        assert "This is a quote" in result
        assert "</blockquote>" in result


class TestHorizontalRules:
    """R2: Horizontal rules as <hr>."""

    def test_horizontal_rule(self, renderer):
        md = "Before\n\n---\n\nAfter"
        result = renderer.render(md)
        assert "<hr>" in result


class TestInlineFormatting:
    """R2: Bold/italic/inline code preserved."""

    def test_bold(self, renderer):
        md = "This is **bold** text."
        result = renderer.render(md)
        assert "<strong>bold</strong>" in result

    def test_italic(self, renderer):
        md = "This is *italic* text."
        result = renderer.render(md)
        assert "<em>italic</em>" in result

    def test_inline_code(self, renderer):
        md = "Use the `print()` function."
        result = renderer.render(md)
        assert "<code>print()</code>" in result

    def test_link(self, renderer):
        md = "Visit [Canvas](https://canvas.uic.edu) for more."
        result = renderer.render(md)
        assert '<a href="https://canvas.uic.edu">' in result
        assert "Canvas</a>" in result


class TestDownloadFooter:
    """R4: Download footer when download_url is provided."""

    def test_footer_appended_with_download_url(self, renderer):
        result = renderer.render("# Test", download_url="https://example.com/download.zip")
        assert "<hr>" in result
        assert "<footer>" in result
        assert '<a href="https://example.com/download.zip" download>' in result
        assert "Download markdown and images" in result
        assert "</footer>" in result

    def test_no_footer_without_download_url(self, renderer):
        result = renderer.render("# Test")
        assert "<footer>" not in result

    def test_footer_inside_article(self, renderer):
        result = renderer.render("# Test", download_url="https://example.com/dl.zip")
        # Footer should be inside the article wrapper
        article_start = result.index("<article>")
        article_end = result.index("</article>")
        footer_start = result.index("<footer>")
        assert article_start < footer_start < article_end

    def test_footer_url_html_escaped(self, renderer):
        result = renderer.render("Test", download_url="https://example.com/dl?a=1&b=2")
        assert "a=1&amp;b=2" in result


class TestFullDocument:
    """End-to-end rendering of a realistic markdown document."""

    def test_full_document(self, renderer):
        md = """# Course Syllabus

## Overview

This course covers **important topics** in *accessibility*.

### Learning Objectives

1. Understand WCAG guidelines
2. Apply semantic HTML
3. Test with screen readers

## Content

Here is a key figure:

![Bar chart showing enrollment trends](images/enrollment.png)

| Semester | Enrollment |
|----------|------------|
| Fall     | 450        |
| Spring   | 380        |

> Note: All materials must meet accessibility standards.

```python
def check_accessibility(page):
    return page.validate()
```

---

For more info, visit [UIC](https://www.uic.edu).
"""
        image_map = {"images/enrollment.png": "https://canvas.uic.edu/files/100/preview"}
        result = renderer.render(md, image_url_map=image_map, download_url="https://example.com/dl.zip")

        # Article wrapper
        assert result.startswith("<article>\n")
        assert result.endswith("</article>\n")

        # Headings
        assert "<h1>Course Syllabus</h1>" in result
        assert "<h2>Overview</h2>" in result
        assert "<h3>Learning Objectives</h3>" in result

        # Inline formatting
        assert "<strong>important topics</strong>" in result
        assert "<em>accessibility</em>" in result

        # Ordered list
        assert "<ol>" in result
        assert "Understand WCAG guidelines" in result

        # Image with figure
        assert "<figure>" in result
        assert 'src="https://canvas.uic.edu/files/100/preview"' in result
        assert "<figcaption>" in result

        # Table
        assert "<table>" in result
        assert '<th scope="col">' in result
        assert "<td>450</td>" in result

        # Blockquote
        assert "<blockquote>" in result

        # Code block
        assert '<pre><code class="language-python">' in result

        # Horizontal rule
        assert "<hr>" in result

        # Link
        assert '<a href="https://www.uic.edu">' in result

        # Download footer
        assert "<footer>" in result
        assert "https://example.com/dl.zip" in result
