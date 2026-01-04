#!/usr/bin/env python3
"""V5 Pipeline Viewer - Real-time TUI for watching document processing.

Shows the live markdown output as it's being built, with clickable
decisions in the sidebar to see reasoning.

Usage:
    # Start a new job and watch it:
    python cli/viewer.py --file path/to/document.pdf

    # Watch an existing job:
    python cli/viewer.py --job-id <uuid>

Controls:
    j/k or arrows  - Scroll markdown
    Tab            - Switch focus between panels
    Enter          - View decision details (when focused)
    Escape         - Close detail view
    q              - Quit
    r              - Reconnect to stream
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from httpx_sse import aconnect_sse
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Markdown,
    RichLog,
    Static,
)


# =============================================================================
# Event Icons
# =============================================================================

ICONS = {
    "check": "\u2714",
    "x": "\u2718",
    "dot": "\u2022",
    "hourglass": "\u23f3",
    "play": "\u25b6",
    "pencil": "\u270e",
    "wrench": "\U0001f527",
    "party": "\U0001f389",
    "brain": "\U0001f9e0",
    "page": "\U0001f4c4",
    "structure": "\U0001f3d7",
    "job": "\U0001f4cb",
    "tool": "\U0001f6e0",
}


# =============================================================================
# Agent Detail Modal
# =============================================================================


class AgentDetailModal(ModalScreen):
    """Modal showing agent tool calls and thinking for a job."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "dismiss", "Close"),
    ]

    CSS = """
    AgentDetailModal {
        align: center middle;
    }

    #agent-container {
        width: 85%;
        height: 85%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #agent-title {
        text-style: bold;
        background: $primary;
        padding: 0 1;
        margin-bottom: 1;
    }

    #agent-log {
        height: 100%;
    }
    """

    def __init__(self, job_id: str, page: int, events: list[dict[str, Any]]) -> None:
        super().__init__()
        self.job_id = job_id
        self.page = page
        self.events = events

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-container"):
            yield Static(f"Agent Activity - Page {self.page}", id="agent-title")
            yield RichLog(id="agent-log", wrap=True)

    def on_mount(self) -> None:
        log = self.query_one("#agent-log", RichLog)
        for event in self.events:
            text = Text()
            msg_type = event.get("message_type", "?")
            tool_name = event.get("tool_name")

            if tool_name:
                text.append(f"{ICONS['tool']} ", style="magenta")
                text.append(f"{tool_name}()", style="bold magenta")
                text.append("\n")
                # Show args
                args = event.get("tool_args", {})
                if args:
                    for k, v in list(args.items())[:5]:
                        text.append(f"  {k}: ", style="dim")
                        text.append(f"{str(v)[:80]}\n", style="")
                # Show result
                result = event.get("tool_result_preview")
                if result:
                    text.append("  → ", style="green")
                    text.append(f"{result[:100]}\n", style="green dim")
            else:
                text.append(f"{ICONS['brain']} ", style="blue")
                text.append(f"[{msg_type}]", style="bold blue")
                content = event.get("content_preview", "")
                if content:
                    text.append(f"\n  {content[:200]}", style="dim")
                text.append("\n")

            log.write(text)
            log.write("")  # Blank line between events


# =============================================================================
# Enhanced Event Log (simpler, more reliable)
# =============================================================================


class EnhancedEventLog(VerticalScroll):
    """Enhanced event log with detailed, formatted output using Static widgets for proper wrapping."""

    DEFAULT_CSS = """
    EnhancedEventLog {
        scrollbar-size: 1 1;
    }

    EnhancedEventLog > Static {
        width: 100%;
        margin-bottom: 1;
    }

    EnhancedEventLog > .job-event {
        background: $surface-darken-1;
        padding: 0 1;
    }

    EnhancedEventLog > .job-event:hover {
        background: $primary 20%;
    }
    """

    def __init__(self, **kwargs) -> None:
        # Remove RichLog-specific kwargs
        kwargs.pop("auto_scroll", None)
        kwargs.pop("max_lines", None)
        super().__init__(**kwargs)
        # Track agent events per job for modal
        self._agent_events: dict[str, list[dict[str, Any]]] = {}
        self._job_pages: dict[str, int] = {}  # job_id -> page
        self._current_job: str | None = None
        self._current_page: int | None = None

    def add_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Add a formatted event to the log."""
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Track agent:thinking events separately (don't display inline)
        if event_type == "agent:thinking":
            job_id = data.get("job_id", self._current_job or "unknown")
            if job_id not in self._agent_events:
                self._agent_events[job_id] = []
            self._agent_events[job_id].append(data)
            return  # Don't show inline

        text = Text()
        text.append(f"{timestamp} ", style="dim")
        css_class = ""
        job_id_for_click: str | None = None

        # Event-specific formatting
        if event_type == "docling:started":
            text.append(f"{ICONS['hourglass']} Converting PDF...", style="yellow")
        elif event_type == "docling:complete":
            pages = data.get("pages_extracted", 0)
            text.append(f"{ICONS['check']} {pages} pages extracted", style="green")
        elif event_type == "planning:started":
            text.append(f"{ICONS['hourglass']} Planning document...", style="yellow")
        elif event_type == "planning:structure":
            title = data.get("document_title", "?")[:40]
            doc_type = data.get("document_type", "?")
            text.append(f"{ICONS['structure']} ", style="cyan")
            text.append(f"{title}", style="bold")
            text.append(f" ({doc_type})", style="dim")
        elif event_type == "planning:page_summarized":
            page = data.get("page_num", "?")
            summary = data.get("summary", "")
            text.append(f"{ICONS['page']} Page {page}: ", style="cyan")
            text.append(summary, style="")
        elif event_type == "planning:complete":
            jobs = data.get("total_jobs", 0)
            text.append(f"{ICONS['check']} Planning complete: {jobs} jobs", style="green")
        elif event_type == "job:created":
            return  # Skip, we show job:started instead
        elif event_type == "job:started":
            job_id = data.get("job_id", "")
            page = data.get("page", "?")
            self._current_job = job_id
            self._current_page = page
            self._job_pages[job_id] = page
            self._agent_events[job_id] = []  # Reset for new job
            text.append(f"{ICONS['play']} ", style="yellow")
            text.append(f"Processing page {page}", style="yellow bold")
            text.append(" [click for details]", style="dim italic")
            css_class = "job-event"
            job_id_for_click = job_id
        elif event_type == "job:completed":
            job_id = data.get("job_id", "")
            page = data.get("page", "?")
            edits = data.get("edits_made", 0)
            tool_calls = len(self._agent_events.get(job_id, []))
            text.append(f"{ICONS['check']} Page {page} done ", style="green")
            text.append(f"({edits} edits, {tool_calls} calls)", style="dim")
            css_class = "job-event"
            job_id_for_click = job_id
        elif event_type == "edit:committed":
            entry = data.get("ledger_entry", {})
            target = entry.get("target", "?")
            action = entry.get("action", "?")
            text.append(f"{ICONS['pencil']} ", style="cyan")
            text.append(f"{target}", style="bold")
            text.append(f" [{action}]", style="dim")
        elif event_type == "edit:validated":
            approved = data.get("approved", False)
            target = data.get("target", "?")
            if approved:
                text.append(f"{ICONS['check']} Approved: ", style="green")
                text.append(target, style="green")
            else:
                text.append(f"{ICONS['x']} Rejected: ", style="red")
                text.append(target, style="red")
        elif event_type == "verification:started":
            text.append(f"{ICONS['hourglass']} Verifying output...", style="yellow")
        elif event_type == "verification:page_verified":
            page = data.get("page_num", "?")
            passed = data.get("passed", False)
            conf = data.get("confidence", 0)
            icon = ICONS["check"] if passed else ICONS["x"]
            style = "green" if passed else "red"
            text.append(f"{icon} Page {page}: ", style=style)
            text.append(f"{conf:.0%} confidence", style="dim")
        elif event_type == "verification:complete":
            passed = data.get("passed", False)
            pages_passed = data.get("pages_passed", 0)
            icon = ICONS["check"] if passed else ICONS["x"]
            style = "green bold" if passed else "red bold"
            text.append(f"{icon} Verification: ", style=style)
            text.append(f"{pages_passed} passed", style="green" if passed else "yellow")
        elif event_type == "recovery:started":
            pages = len(data.get("pages_to_recover", []))
            text.append(f"{ICONS['wrench']} Recovering {pages} pages...", style="yellow")
        elif event_type == "recovery:complete":
            recovered = len(data.get("pages_recovered", []))
            text.append(f"{ICONS['check']} Recovered {recovered} pages", style="green")
        elif event_type == "processing:complete":
            success = data.get("success", False)
            edits = data.get("total_edits", 0)
            cost = data.get("total_cost", 0)
            duration = data.get("total_duration_ms", 0) / 1000
            if success:
                text.append(f"{ICONS['party']} SUCCESS ", style="green bold")
                text.append(f"{edits} edits | ${cost:.3f} | {duration:.1f}s", style="green")
            else:
                text.append(f"{ICONS['x']} FAILED", style="red bold")
        elif event_type == "processing:error":
            error = data.get("error", "Unknown error")
            text.append(f"{ICONS['x']} Error: {error}", style="red")
        else:
            # Generic fallback
            text.append(f"{ICONS['dot']} {event_type}", style="dim")

        # Create a clickable Static widget
        widget = Static(text, classes=css_class)
        if job_id_for_click:
            widget.data_job_id = job_id_for_click  # Store job_id for click handling
        self.mount(widget)
        self.scroll_end(animate=False)

    def get_agent_events(self, job_id: str) -> list[dict[str, Any]]:
        """Get agent events for a specific job."""
        return self._agent_events.get(job_id, [])

    def get_job_page(self, job_id: str) -> int:
        """Get page number for a job."""
        return self._job_pages.get(job_id, 0)


# =============================================================================
# Decision Detail Modal
# =============================================================================


class DecisionDetailScreen(ModalScreen):
    """Modal screen showing decision details."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "dismiss", "Close"),
    ]

    CSS = """
    DecisionDetailScreen {
        align: center middle;
    }

    #detail-container {
        width: 80%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #detail-title {
        text-style: bold;
        background: $primary;
        padding: 0 1;
        margin-bottom: 1;
    }

    #detail-content {
        height: 100%;
        overflow-y: auto;
    }

    .detail-section {
        margin-bottom: 1;
    }

    .detail-label {
        text-style: bold;
        color: $text-muted;
    }
    """

    def __init__(self, decision: dict[str, Any]) -> None:
        super().__init__()
        self.decision = decision

    def compose(self) -> ComposeResult:
        d = self.decision
        with Vertical(id="detail-container"):
            yield Static(f"Decision: {d.get('target', '?')}", id="detail-title")

            with VerticalScroll(id="detail-content"):
                # Status
                approved = d.get("approved", False)
                status = f"{ICONS['check']} Approved" if approved else f"{ICONS['x']} Rejected"
                status_style = "green" if approved else "red"
                yield Static(Text(status, style=status_style), classes="detail-section")

                # Target info
                yield Static(Text("Target:", style="bold"), classes="detail-label")
                yield Static(f"  Page {d.get('page', '?')} | {d.get('target', '?')} | {d.get('action', '?')}")

                # Before
                yield Static(Text("\nBefore:", style="bold"), classes="detail-label")
                before = d.get("before", "")
                yield Static(Text(before or "(empty)", style="red dim"))

                # After
                yield Static(Text("\nAfter:", style="bold"), classes="detail-label")
                after = d.get("after", "")
                yield Static(Text(after or "(empty)", style="green"))

                # Reasoning
                yield Static(Text("\nReasoning:", style="bold"), classes="detail-label")
                reasoning = d.get("reasoning", "No reasoning provided")
                yield Static(Text(reasoning, style="italic"))

                # Feedback (if rejected)
                if not approved and d.get("feedback"):
                    yield Static(Text("\nRejection Reason:", style="bold red"), classes="detail-label")
                    yield Static(Text(d.get("feedback", ""), style="red"))

                yield Static("\n[Press Enter or Escape to close]", classes="detail-section")


# =============================================================================
# Decision List Item
# =============================================================================


class DecisionItem(ListItem):
    """A clickable decision item."""

    def __init__(self, decision: dict[str, Any], index: int) -> None:
        super().__init__()
        self.decision = decision
        self.index = index

    def compose(self) -> ComposeResult:
        d = self.decision
        approved = d.get("approved", False)
        target = d.get("target", "?")
        icon = ICONS["check"] if approved else ICONS["x"]
        style = "green" if approved else "red"

        text = Text()
        text.append(f"{icon} ", style=style)
        text.append(target, style=style if not approved else "")

        yield Label(text)


# =============================================================================
# Main Widgets
# =============================================================================


class MarkdownViewer(VerticalScroll):
    """Live markdown viewer that updates as edits come in."""

    DEFAULT_CSS = """
    MarkdownViewer {
        padding: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._page_markdowns: dict[int, str] = {}
        self._content = ""
        self._show_raw = False

    def on_mount(self) -> None:
        """Show initial placeholder."""
        self._update_display("*Waiting for content...*")

    def set_content(self, content: str) -> None:
        """Set the full markdown content."""
        self._content = content
        self._update_display(content)

    def update_page(self, page: int, content: str) -> None:
        """Update a specific page's markdown."""
        self._page_markdowns[page] = content
        self._rebuild_markdown()

    def _rebuild_markdown(self) -> None:
        """Rebuild the full markdown from all pages."""
        if not self._page_markdowns:
            return

        parts = []
        for page_num in sorted(self._page_markdowns.keys()):
            parts.append(f"<!-- Page {page_num} -->\n")
            parts.append(self._page_markdowns[page_num])
            parts.append("\n\n---\n\n")

        self._content = "".join(parts)
        self._update_display(self._content)

    def toggle_raw(self) -> None:
        """Toggle between rendered and raw markdown."""
        self._show_raw = not self._show_raw
        self._update_display(self._content)

    @property
    def show_raw(self) -> bool:
        return self._show_raw

    def _update_display(self, content: str) -> None:
        """Update the display with content."""
        # Clear existing children
        self.remove_children()

        if self._show_raw:
            # Show plain text
            self.mount(Static(content))
        else:
            # Render as markdown
            self.mount(Markdown(content))


class EventLog(RichLog):
    """Compact event log."""

    def add_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Add an event to the log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(f"{timestamp} ", style="dim")

        # Compact event display
        if event_type == "docling:started":
            text.append(f"{ICONS['hourglass']} Converting PDF...", style="yellow")
        elif event_type == "docling:complete":
            pages = data.get("pages_extracted", 0)
            text.append(f"{ICONS['check']} {pages} pages extracted", style="green")
        elif event_type == "planning:started":
            text.append(f"{ICONS['hourglass']} Planning...", style="yellow")
        elif event_type == "planning:complete":
            jobs = data.get("total_jobs", 0)
            text.append(f"{ICONS['check']} {jobs} jobs planned", style="green")
        elif event_type == "job:started":
            page = data.get("page", "?")
            text.append(f"{ICONS['play']} Page {page}", style="yellow")
        elif event_type == "job:completed":
            page = data.get("page", "?")
            edits = data.get("edits_made", 0)
            text.append(f"{ICONS['check']} Page {page} ({edits} edits)", style="green")
        elif event_type == "edit:committed":
            target = data.get("ledger_entry", {}).get("target", "?")
            text.append(f"{ICONS['pencil']} {target}", style="cyan")
        elif event_type == "verification:complete":
            passed = data.get("passed", False)
            if passed:
                text.append(f"{ICONS['check']} Verified", style="green")
            else:
                text.append(f"{ICONS['x']} Verification failed", style="red")
        elif event_type == "recovery:started":
            pages = len(data.get("pages_to_recover", []))
            text.append(f"{ICONS['wrench']} Recovering {pages} pages", style="yellow")
        elif event_type == "recovery:complete":
            recovered = len(data.get("pages_recovered", []))
            text.append(f"{ICONS['check']} Recovered {recovered}", style="green")
        elif event_type == "processing:complete":
            success = data.get("success", False)
            edits = data.get("total_edits", 0)
            cost = data.get("total_cost", 0)
            if success:
                text.append(f"{ICONS['party']} Done! {edits} edits, ${cost:.2f}", style="green bold")
            else:
                text.append(f"{ICONS['x']} Failed", style="red bold")
        elif event_type == "processing:error":
            text.append(f"{ICONS['x']} Error: {data.get('error', '')[:30]}", style="red")
        else:
            # Skip verbose events
            return

        self.write(text)


class StatusBar(Static):
    """Status bar showing current progress."""

    status = reactive("Connecting...")
    page_info = reactive("")
    cost = reactive(0.0)

    def render(self) -> Text:
        text = Text()
        text.append(f" {self.status} ", style="bold")
        if self.page_info:
            text.append(" | ", style="dim")
            text.append(self.page_info, style="cyan")
        if self.cost > 0:
            text.append(" | ", style="dim")
            text.append(f"${self.cost:.3f}", style="green")
        return text


# =============================================================================
# Main App
# =============================================================================


class V5Viewer(App):
    """V5 Pipeline Viewer - Live markdown output with clickable decisions."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 3fr 1fr;
        grid-rows: 1fr auto;
    }

    #markdown-container {
        column-span: 1;
        row-span: 1;
        height: 1fr;
        border: solid $primary;
    }

    #markdown-title {
        dock: top;
        text-style: bold;
        background: $primary;
        padding: 0 1;
    }

    #markdown-viewer {
        height: 100%;
    }

    #sidebar {
        column-span: 1;
        row-span: 1;
        height: 1fr;
        layout: vertical;
    }

    #events-container {
        height: 1fr;
        border: solid $secondary;
    }

    #events-title {
        dock: top;
        text-style: bold;
        background: $secondary;
        padding: 0 1;
    }

    #decisions-container {
        height: 1fr;
        border: solid $success;
    }

    #decisions-title {
        dock: top;
        text-style: bold;
        background: $success;
        padding: 0 1;
    }

    #decisions-list {
        height: 100%;
    }

    #status-bar {
        column-span: 2;
        dock: bottom;
        height: 1;
        background: $surface;
    }

    EventLog {
        scrollbar-size: 1 1;
    }

    ListView {
        scrollbar-size: 1 1;
    }

    ListView > ListItem {
        padding: 0 1;
    }

    ListView > ListItem:hover {
        background: $primary 30%;
    }

    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reconnect", "Reconnect"),
        Binding("m", "toggle_markdown", "Raw/Rendered"),
        Binding("tab", "focus_next", "Next Panel"),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
    ]

    def __init__(
        self,
        api_url: str,
        api_key: str,
        job_id: str | None = None,
        file_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.job_id = job_id
        self.file_path = file_path
        self._decisions: list[dict[str, Any]] = []
        self._page_markdowns: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        yield Header()

        # Main markdown view - direct child of Screen for grid layout
        with Vertical(id="markdown-container"):
            yield Static("Markdown Output", id="markdown-title")
            yield MarkdownViewer(id="markdown-viewer")

        # Sidebar - direct child of Screen for grid layout
        with Vertical(id="sidebar"):
            with Vertical(id="events-container"):
                yield Static("Events", id="events-title")
                yield EnhancedEventLog(id="events", auto_scroll=True, max_lines=500)

            with Vertical(id="decisions-container"):
                yield Static("Decisions (click for details)", id="decisions-title")
                yield ListView(id="decisions-list")

        yield StatusBar(id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        """Start processing when app mounts."""
        self.title = "V5 Pipeline Viewer"

        if self.file_path:
            self.start_job()
        elif self.job_id:
            self.watch_job()
        else:
            self.notify("No file or job ID provided", severity="error")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle decision selection."""
        if isinstance(event.item, DecisionItem):
            self.push_screen(DecisionDetailScreen(event.item.decision))

    def on_click(self, event) -> None:
        """Handle clicks on job events to show agent details."""
        # Check if clicked widget has a job_id
        widget = event.widget
        if hasattr(widget, "data_job_id"):
            job_id = widget.data_job_id
            events_log = self.query_one("#events", EnhancedEventLog)
            agent_events = events_log.get_agent_events(job_id)
            page = events_log.get_job_page(job_id)
            if agent_events:
                self.push_screen(AgentDetailModal(job_id, page, agent_events))
            else:
                self.notify("No agent activity recorded for this job", severity="warning")

    @work(exclusive=True)
    async def start_job(self) -> None:
        """Submit a new job and start streaming."""
        status_bar = self.query_one("#status-bar", StatusBar)

        status_bar.status = "Uploading..."

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(self.file_path, "rb") as f:
                    response = await client.post(
                        f"{self.api_url}/api/v5/process",
                        headers={"X-API-Key": self.api_key},
                        files={"file": (self.file_path.name, f, "application/pdf")},
                    )

                if response.status_code != 200:
                    self.notify(f"Upload failed: {response.text}", severity="error")
                    return

                data = response.json()
                self.job_id = data["job_id"]
                self.title = f"V5 Viewer - {self.job_id[:8]}"

                await self._stream_events()

        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            status_bar.status = f"Error: {e}"

    @work(exclusive=True)
    async def watch_job(self) -> None:
        """Watch an existing job."""
        self.title = f"V5 Viewer - {self.job_id[:8]}"
        await self._stream_events()

    async def _stream_events(self) -> None:
        """Stream events from the API."""
        status_bar = self.query_one("#status-bar", StatusBar)
        events_log = self.query_one("#events", EnhancedEventLog)
        decisions_list = self.query_one("#decisions-list", ListView)
        markdown_viewer = self.query_one("#markdown-viewer", MarkdownViewer)

        status_bar.status = "Connecting..."

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                url = f"{self.api_url}/api/v5/jobs/{self.job_id}/stream"
                headers = {"X-API-Key": self.api_key}

                async with aconnect_sse(client, "GET", url, headers=headers) as source:
                    status_bar.status = "Streaming..."

                    async for event in source.aiter_sse():
                        if event.event == "done":
                            status_bar.status = "Complete"
                            break

                        if event.event == "error":
                            data = json.loads(event.data)
                            self.notify(f"Stream error: {data.get('message')}", severity="error")
                            break

                        try:
                            data = json.loads(event.data)
                        except json.JSONDecodeError:
                            continue

                        # Add to event log
                        events_log.add_event(event.event, data)

                        # Track decisions (both proposed and validated)
                        if event.event == "edit:validated":
                            # Store validation result
                            decision = {
                                "approved": data.get("approved", False),
                                "target": data.get("target", "?"),
                                "feedback": data.get("feedback"),
                                "job_id": data.get("job_id"),
                            }
                            # We'll merge with committed data when it comes

                        if event.event == "edit:committed":
                            entry = data.get("ledger_entry", {})
                            decision = {
                                "approved": True,
                                "target": entry.get("target", "?"),
                                "page": entry.get("page", "?"),
                                "action": entry.get("action", "?"),
                                "before": entry.get("before", ""),
                                "after": entry.get("after", ""),
                                "reasoning": entry.get("reasoning", ""),
                                "feedback": None,
                            }
                            self._decisions.append(decision)
                            await decisions_list.append(DecisionItem(decision, len(self._decisions) - 1))

                            # Update markdown for this page
                            page = entry.get("page", 1)
                            # Apply the edit to our tracked markdown
                            current = self._page_markdowns.get(page, "")
                            before = entry.get("before", "")
                            after = entry.get("after", "")
                            if before and before in current:
                                self._page_markdowns[page] = current.replace(before, after, 1)
                                markdown_viewer.update_page(page, self._page_markdowns[page])

                        # Initialize page markdown from planning
                        if event.event == "planning:page_summarized":
                            page = data.get("page_num", 1)
                            # We don't have the actual markdown here, will get from job results

                        if event.event == "job:started":
                            page = data.get("page", 1)
                            status_bar.page_info = f"Processing page {page}"

                        if event.event == "job:completed":
                            page = data.get("page", "?")
                            # After job completes, we should have updated markdown

                        if event.event == "planning:complete":
                            status_bar.status = "Executing..."
                            # Fetch initial markdown for all pages
                            await self._fetch_initial_markdown()

                        if event.event == "verification:started":
                            status_bar.status = "Verifying..."
                            status_bar.page_info = ""

                        if event.event == "recovery:started":
                            status_bar.status = "Recovering..."

                        if event.event == "processing:complete":
                            success = data.get("success", False)
                            status_bar.cost = data.get("total_cost", 0)
                            status_bar.status = "SUCCESS" if success else "FAILED"
                            status_bar.page_info = f"{data.get('total_edits', 0)} edits"

                            # Fetch final markdown
                            await self._fetch_final_markdown()

        except httpx.ReadTimeout:
            status_bar.status = "Timeout"
        except Exception as e:
            self.notify(f"Stream error: {e}", severity="error")
            status_bar.status = f"Error"

    async def _fetch_initial_markdown(self) -> None:
        """Fetch initial page markdowns from job state."""
        # For now, show placeholder until edits come in
        markdown_viewer = self.query_one("#markdown-viewer", MarkdownViewer)
        markdown_viewer.set_content("*Processing document... edits will appear as they're made.*")

    async def _fetch_final_markdown(self) -> None:
        """Fetch final markdown from API."""
        markdown_viewer = self.query_one("#markdown-viewer", MarkdownViewer)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.api_url}/api/v5/jobs/{self.job_id}/markdown"
                headers = {"X-API-Key": self.api_key}
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    markdown_viewer.set_content(response.text)
                else:
                    markdown_viewer.set_content(f"*Error fetching markdown: {response.status_code}*")
        except Exception as e:
            markdown_viewer.set_content(f"*Error fetching markdown: {e}*")

    def action_reconnect(self) -> None:
        """Reconnect to the stream."""
        if self.job_id:
            self.watch_job()

    def action_scroll_down(self) -> None:
        """Scroll markdown down."""
        viewer = self.query_one("#markdown-viewer", MarkdownViewer)
        viewer.scroll_down()

    def action_scroll_up(self) -> None:
        """Scroll markdown up."""
        viewer = self.query_one("#markdown-viewer", MarkdownViewer)
        viewer.scroll_up()

    def action_toggle_markdown(self) -> None:
        """Toggle between rendered and raw markdown."""
        markdown_viewer = self.query_one("#markdown-viewer", MarkdownViewer)
        markdown_viewer.toggle_raw()

        # Update title to reflect current mode
        title = self.query_one("#markdown-title", Static)
        if markdown_viewer.show_raw:
            title.update("Markdown Output [RAW]")
        else:
            title.update("Markdown Output")


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="V5 Pipeline Viewer - Live markdown output with clickable decisions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Process a new PDF and watch it:
    python cli/viewer.py --file document.pdf --api-key YOUR_KEY

    # Watch an existing job:
    python cli/viewer.py --job-id abc123 --api-key YOUR_KEY
        """,
    )
    parser.add_argument("--file", "-f", type=Path, help="PDF file to process")
    parser.add_argument("--job-id", "-j", type=str, help="Existing job ID to watch")
    parser.add_argument("--api-url", type=str, default="http://localhost:8080", help="API base URL")
    parser.add_argument("--api-key", "-k", type=str, required=True, help="API key")

    args = parser.parse_args()

    if not args.file and not args.job_id:
        parser.error("Either --file or --job-id is required")

    if args.file and not args.file.exists():
        parser.error(f"File not found: {args.file}")

    app = V5Viewer(
        api_url=args.api_url,
        api_key=args.api_key,
        job_id=args.job_id,
        file_path=args.file,
    )
    app.run()


if __name__ == "__main__":
    main()
