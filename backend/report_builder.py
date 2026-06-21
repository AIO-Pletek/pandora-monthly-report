"""
Monthly report generator — builds .docx with embedded charts.

Uses ``python-docx`` for document generation and ``matplotlib`` (Agg backend)
for charts rendered to PNG, then embedded into the document.

**Community Edition note:**
  Metric data (CPU/RAM/Disk) may not be available because Pandora Community
  Edition has no ``get_agent_modules`` operation.  The report gracefully
  shows "N/A" for unavailable metrics and focuses on event/alert data which
  IS available.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── matplotlib MUST use Agg backend (server-side, no display) ──────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

from docx import Document  # noqa: E402
from docx.enum.section import WD_ORIENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Inches, Pt, RGBColor  # noqa: E402

logger = logging.getLogger(__name__)

# ── Chart style constants ──────────────────────────────────────────────────
COLORS = {
    "critical": "#DC3545",
    "warning": "#FFC107",
    "info": "#17A2B8",
    "unknown": "#6C757D",
    "primary": "#0D6EFD",
    "accent": "#198754",
}
CHART_DPI = 150
CHART_WIDTH = 8
CHART_HEIGHT = 5


def _setup_plot_style() -> None:
    """Apply consistent style to all matplotlib charts."""
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Liberation Sans", "Arial"],
        "font.size": 10,
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "figure.facecolor": "white",
        "axes.facecolor": "#F8F9FA",
        "axes.edgecolor": "#DEE2E6",
        "axes.grid": True,
        "grid.alpha": 0.4,
        "grid.color": "#CED4DA",
    })


_setup_plot_style()


# ── Chart generators ───────────────────────────────────────────────────────

def generate_severity_pie(
    severity_counts: dict[str, int],
    output_path: str | Path,
) -> str:
    """Generate a donut chart for event severity breakdown.

    Args:
        severity_counts: e.g. ``{"Critical": 3, "Warning": 5, "Info": 1}``
        output_path: Where to save the PNG.

    Returns:
        Absolute path to the saved PNG.
    """
    labels = list(severity_counts.keys())
    values = list(severity_counts.values())
    pie_colors = [
        COLORS.get(lbl.lower(), COLORS["unknown"]) for lbl in labels
    ]

    fig, ax = plt.subplots(figsize=(6, 5))
    wedges, texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct="%1.1f%%" if sum(values) > 0 else None,
        startangle=90,
        colors=pie_colors,
        wedgeprops={"width": 0.4, "edgecolor": "white", "linewidth": 1},
        pctdistance=0.78,
    )
    for at in autotexts:
        at.set_fontsize(9)
        at.set_fontweight("bold")

    # Legend
    legend_labels = [
        f"{lbl}  ({val})" for lbl, val in zip(labels, values)
    ]
    ax.legend(wedges, legend_labels, title="Severity", loc="center left",
              bbox_to_anchor=(1, 0.5), fontsize=9)

    ax.set_title("Event Severity Distribution", fontweight="bold", pad=15)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    return str(Path(output_path).resolve())


def generate_events_timeline(
    events: list[dict],
    output_path: str | Path,
) -> str:
    """Generate a bar chart of events per day.

    Args:
        events: List of event dicts, each must have a ``utimestamp`` field.
        output_path: Where to save the PNG.

    Returns:
        Absolute path to the saved PNG.
    """
    from collections import Counter

    # Count events per day
    day_counts: Counter[str] = Counter()
    for evt in events:
        uts = evt.get("utimestamp")
        if uts:
            try:
                dt = datetime.fromtimestamp(int(uts))
                day_counts[dt.strftime("%d %b")] += 1
            except (ValueError, TypeError, OSError):
                continue

    if not day_counts:
        # Empty chart with message
        fig, ax = plt.subplots(figsize=(CHART_WIDTH, CHART_HEIGHT))
        ax.text(0.5, 0.5, "No event data available",
                ha="center", va="center", fontsize=14, color=COLORS["unknown"])
        ax.set_title("Event Timeline", fontweight="bold")
        fig.tight_layout()
        fig.savefig(str(output_path), dpi=CHART_DPI)
        plt.close(fig)
        return str(Path(output_path).resolve())

    days = sorted(day_counts.keys())
    counts = [day_counts[d] for d in days]

    fig, ax = plt.subplots(figsize=(CHART_WIDTH, CHART_HEIGHT))
    bars = ax.bar(days, counts, color=COLORS["primary"], edgecolor="white", linewidth=0.5)
    ax.set_title("Events per Day", fontweight="bold")
    ax.set_ylabel("Event Count")
    ax.set_xlabel("Day")
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    # Value labels on bars
    for bar, val in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                str(val), ha="center", va="bottom", fontsize=8, fontweight="bold")

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    return str(Path(output_path).resolve())


# ── Document builder ───────────────────────────────────────────────────────

class ReportBuilder:
    """Builds a monthly report .docx file.

    Usage::

        builder = ReportBuilder(
            tenant_name="ACA_Insurance",
            period="June 2026",
            date_start="2026-06-01",
            date_end="2026-06-30",
            output_dir=Path("/opt/pandora-monthly-report/backend/output"),
        )
        builder.add_executive_summary(agents, events)
        builder.add_availability(agents)
        builder.add_alerts_section(events)
        builder.save("ACA_Insurance_2026-06")
    """

    def __init__(
        self,
        tenant_name: str,
        period: str,
        date_start: str,
        date_end: str,
        output_dir: Path,
    ) -> None:
        self.tenant_name = tenant_name
        self.period = period
        self.date_start = date_start
        self.date_end = date_end
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.doc = Document()
        self._chart_paths: list[str] = []  # Track temp chart files for cleanup

        # Narrow margins
        for section in self.doc.sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)

        # Style defaults
        style = self.doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(10)

        self._add_cover()

    # ── Cover ──────────────────────────────────────────────────────────

    def _add_cover(self) -> None:
        """Add cover page with tenant name and period."""
        # Spacer
        for _ in range(4):
            self.doc.add_paragraph("")

        title = self.doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run("Monthly Infrastructure Report")
        run.font.size = Pt(28)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x0D, 0x6E, 0xFD)

        self.doc.add_paragraph("")

        tenant_para = self.doc.add_paragraph()
        tenant_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = tenant_para.add_run(self.tenant_name)
        run.font.size = Pt(20)
        run.font.bold = True

        period_para = self.doc.add_paragraph()
        period_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = period_para.add_run(f"Period: {self.period}")
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0x6C, 0x75, 0x7D)

        self.doc.add_paragraph("")
        gen_para = self.doc.add_paragraph()
        gen_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = gen_para.add_run(
            f"Generated: {datetime.now().strftime('%d %B %Y, %H:%M')}"
        )
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xAD, 0xB5, 0xBD)

        self.doc.add_page_break()

    # ── Executive Summary ──────────────────────────────────────────────

    def add_executive_summary(
        self, agents: list[dict], events: list[dict]
    ) -> None:
        """Add section 2: Executive Summary."""
        self.doc.add_heading("Executive Summary", level=1)

        # Count severities
        sev: dict[str, int] = {"Critical": 0, "Warning": 0, "Info": 0, "Unknown": 0}
        sev_map = {"4": "Critical", "3": "Warning", "2": "Info", "1": "Info", "0": "Info"}
        for evt in events:
            c = str(evt.get("criticity", ""))
            key = sev_map.get(c, "Unknown")
            sev[key] += 1

        total_events = sum(sev.values())
        total_agents = len(agents)

        # Summary paragraph
        summary_text = (
            f"This report covers {total_agents} agent(s) in tenant "
            f"**{self.tenant_name}** for the period **{self.period}**. "
            f"A total of {total_events} event(s) were recorded during this period."
        )
        self.doc.add_paragraph(summary_text)

        # Key metrics box
        self.doc.add_heading("Key Metrics", level=2)
        table = self.doc.add_table(rows=1, cols=5)
        table.style = "Light Shading Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Total Agents"
        hdr[1].text = "Total Events"
        hdr[2].text = "Critical"
        hdr[3].text = "Warning"
        hdr[4].text = "Info"
        row = table.add_row().cells
        row[0].text = str(total_agents)
        row[1].text = str(total_events)
        row[2].text = str(sev["Critical"])
        row[3].text = str(sev["Warning"])
        row[4].text = str(sev["Info"])

        # Severity pie chart
        sev_display = {k: v for k, v in sev.items() if v > 0}
        if sev_display:
            chart_path = self.output_dir / f"_chart_severity_{_safe_filename(self.tenant_name)}.png"
            generate_severity_pie(sev_display, chart_path)
            self._chart_paths.append(str(chart_path))
            self.doc.add_paragraph("")
            self.doc.add_picture(str(chart_path), width=Inches(4.5))
            last_paragraph = self.doc.paragraphs[-1]
            last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        self.doc.add_page_break()

    # ── Availability ───────────────────────────────────────────────────

    def add_availability(self, agents: list[dict]) -> None:
        """Add section 3: Availability per agent table."""
        self.doc.add_heading("Agent Availability", level=1)

        if not agents:
            self.doc.add_paragraph(
                "No agent data available for this period."
            )
            return

        note = self.doc.add_paragraph()
        run = note.add_run(
            "Note: Uptime data is not directly available via Pandora "
            "Community API. The table below lists all agents in this "
            "tenant. For detailed uptime metrics, consider using the "
            "Pandora Console directly."
        )
        run.font.size = Pt(9)
        run.font.italic = True
        run.font.color.rgb = RGBColor(0x6C, 0x75, 0x7D)

        self.doc.add_paragraph("")

        # Agent table
        table = self.doc.add_table(rows=1, cols=5)
        table.style = "Light Shading Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Agent Alias"
        hdr[1].text = "OS"
        hdr[2].text = "IP Address"
        hdr[3].text = "Description"
        hdr[4].text = "Agent ID"

        for agent in agents:
            row = table.add_row().cells
            row[0].text = agent.get("alias", "N/A")
            row[1].text = agent.get("name", "N/A")
            row[2].text = agent.get("direccion", "N/A")
            row[3].text = (agent.get("comentarios") or "")[:60]
            row[4].text = str(agent.get("id_agente", "N/A"))

        # Auto-fit — set column widths
        widths = [Inches(2.0), Inches(0.8), Inches(1.3), Inches(2.5), Inches(0.7)]
        for row in table.rows:
            for idx, width in enumerate(widths):
                row.cells[idx].width = width

        self.doc.add_page_break()

    # ── Alerts & Events ────────────────────────────────────────────────

    def add_alerts_section(self, events: list[dict]) -> None:
        """Add section 5: Alerts & Events breakdown."""
        self.doc.add_heading("Alerts & Events", level=1)

        if not events:
            self.doc.add_paragraph("No events recorded in this period.")
            return

        # Severity breakdown
        sev_map = {"4": "Critical", "3": "Warning", "2": "Info", "1": "Info", "0": "Info"}
        sev_counts: dict[str, int] = {"Critical": 0, "Warning": 0, "Info": 0}
        for evt in events:
            c = str(evt.get("criticity", ""))
            key = sev_map.get(c, "Info")
            sev_counts[key] += 1

        self.doc.add_heading("Severity Breakdown", level=2)
        table = self.doc.add_table(rows=1, cols=3)
        table.style = "Light Shading Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Severity"
        hdr[1].text = "Count"
        hdr[2].text = "Percentage"
        total = max(sum(sev_counts.values()), 1)
        for sev_name, count in sev_counts.items():
            row = table.add_row().cells
            row[0].text = sev_name
            row[1].text = str(count)
            row[2].text = f"{count / total * 100:.1f}%"

        self.doc.add_paragraph("")

        # Event timeline chart
        chart_path = self.output_dir / f"_chart_timeline_{_safe_filename(self.tenant_name)}.png"
        generate_events_timeline(events, chart_path)
        self._chart_paths.append(str(chart_path))
        self.doc.add_picture(str(chart_path), width=Inches(6.5))
        last_paragraph = self.doc.paragraphs[-1]
        last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        self.doc.add_paragraph("")

        # Latest events table (max 20)
        self.doc.add_heading("Recent Events", level=2)
        events_sorted = sorted(
            events,
            key=lambda e: int(e.get("utimestamp", "0")),
            reverse=True,
        )
        table = self.doc.add_table(rows=1, cols=5)
        table.style = "Light Shading Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Timestamp"
        hdr[1].text = "Agent"
        hdr[2].text = "Severity"
        hdr[3].text = "Type"
        hdr[4].text = "Description"

        for evt in events_sorted[:20]:
            row = table.add_row().cells
            row[0].text = evt.get("timestamp", "N/A")
            row[1].text = evt.get("agent_name", "N/A")
            row[2].text = evt.get("criticity_name", str(evt.get("criticity", "?")))
            row[3].text = evt.get("event_type", "N/A")
            desc = (evt.get("evento") or "")[:100]
            row[4].text = desc

        self.doc.add_page_break()

    # ── Footer ─────────────────────────────────────────────────────────

    def _add_footer(self) -> None:
        """Add footer with generation info."""
        self.doc.add_heading("Notes", level=1)
        self.doc.add_paragraph(
            f"This report was automatically generated by Pandora Monthly Report "
            f"on {datetime.now().strftime('%d %B %Y at %H:%M')}."
        )
        self.doc.add_paragraph(
            "Data source: Pandora FMS Community Edition v7.0 NG — External API."
        )
        self.doc.add_paragraph(
            "Note: Community Edition has limited API capabilities. "
            "Some metrics (CPU, RAM, Disk) may not be available."
        )

        # Disclaimer
        disclaimer = self.doc.add_paragraph()
        run = disclaimer.add_run(
            "This is an automatically generated document. "
            "Please verify critical data against the Pandora Console."
        )
        run.font.size = Pt(8)
        run.font.italic = True
        run.font.color.rgb = RGBColor(0xAD, 0xB5, 0xBD)

    # ── Save & cleanup ─────────────────────────────────────────────────

    def save(self, filename_stem: str) -> str:
        """Finalise the document, save to output_dir, and return the path.

        Args:
            filename_stem: Base filename without extension.

        Returns:
            Absolute path to the generated .docx file.
        """
        self._add_footer()

        # Ensure unique filename
        safe_name = _safe_filename(filename_stem)
        output_path = self.output_dir / f"{safe_name}.docx"
        counter = 1
        while output_path.exists():
            output_path = self.output_dir / f"{safe_name}_{counter}.docx"
            counter += 1

        self.doc.save(str(output_path))
        logger.info("Report saved to %s", output_path)

        # Clean up temp chart files
        for cp in self._chart_paths:
            try:
                os.unlink(cp)
            except OSError:
                pass

        return str(output_path.resolve())


# ── Top-level convenience ──────────────────────────────────────────────────

def build_report(
    tenant_name: str,
    period: str,
    date_start: str,
    date_end: str,
    agents: list[dict],
    events: list[dict],
    output_dir: str | Path,
) -> str:
    """Generate a complete monthly report .docx in one call.

    Args:
        tenant_name: Display name of the tenant/group.
        period: Human-readable period, e.g. "June 2026".
        date_start: Start date "YYYY-MM-DD".
        date_end: End date "YYYY-MM-DD".
        agents: List of agent dicts from PandoraClient.
        events: List of event dicts from PandoraClient.
        output_dir: Directory to save the .docx file.

    Returns:
        Absolute path to the generated .docx file.
    """
    builder = ReportBuilder(
        tenant_name=tenant_name,
        period=period,
        date_start=date_start,
        date_end=date_end,
        output_dir=Path(output_dir),
    )
    builder.add_executive_summary(agents, events)
    builder.add_availability(agents)
    builder.add_alerts_section(events)

    safe_tenant = _safe_filename(tenant_name)
    safe_period = period.replace(" ", "_")
    filename = f"{safe_tenant}_{safe_period}"

    return builder.save(filename)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    """Sanitize a string for use in a filename."""
    import re
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^\w\-_.]", "", name)
    return name or "report"
