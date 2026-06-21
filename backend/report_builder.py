"""
Monthly report builder — generates .docx matching reference format.

Reference format (extracted from user-provided .docx):
  - Title: "Resources Usage Metric Report" (Arial 24pt bold)
  - Subtitle: "Report Period: <Month Year>" (Arial 14pt)
  - Table (2 cols): "Item" | "Usage Metric"
    - Per VM: "Virtual Machine" | agent alias (bold)
    - Per metric: metric display name | line chart PNG showing values

Uses ``python-docx`` for document and ``matplotlib`` (Agg backend)
for line charts rendered to PNG, embedded into table cells.
"""

from __future__ import annotations

import calendar
import io
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

# ── matplotlib MUST use Agg backend first ──────────────────────────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt            # noqa: E402
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.ticker as mticker        # noqa: E402

from docx import Document                  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Inches, Pt, RGBColor, Emu  # noqa: E402

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
CHART_DPI = 130
CHART_W_INCHES = 5.5
CHART_H_INCHES = 2.2

COLOR_CPU = "#0D6EFD"
COLOR_MEM = "#198754"
COLOR_DISK = "#DC3545"
COLOR_DEFAULT = "#6C757D"

FONT_FAMILY = "DejaVu Sans"

# Metric name pattern → display name + chart color
_METRIC_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, display_name, color)
    (r"(?i)cpu|processor|utilization|load|usage.*cpu", "CPU Utilization", COLOR_CPU),
    (r"(?i)mem|ram|memory", "Memory Usage", COLOR_MEM),
    (r"(?i)disk.*[c][:/]|storage.*[c][:/]|diskused.*[c]", "Disk C:/", COLOR_DISK),
    (r"(?i)disk.*[d][:/]|storage.*[d][:/]|diskused.*[d]", "Disk D:/", COLOR_DISK),
    (r"(?i)disk.*[e][:/]|diskused.*[e]", "Disk E:/", COLOR_DISK),
    (r"(?i)disk|storage|diskused_", "Disk", COLOR_DISK),
]


def _classify_metric(data_parts: list[str]) -> tuple[str, str]:
    """Guess metric type from value patterns.

    Pandora Community Ed gives us no module name — only values.
    We classify by value characteristics:
      - CPU: typically 0–100 (percentage)
      - Memory/Disk: large numbers (KB/bytes/counts)

    Returns (display_name, color).
    """
    if not data_parts:
        return ("Metric", COLOR_DEFAULT)

    # Extract values (every other item: ts, value, ts, value, ...)
    vals = []
    for i, part in enumerate(data_parts):
        if i % 2 == 1:  # odd positions are values
            try:
                vals.append(float(part))
            except ValueError:
                pass

    if not vals:
        return ("Metric", COLOR_DEFAULT)

    avg = sum(vals) / len(vals)
    max_val = max(vals)

    # All values 0–100 → likely CPU %
    if max_val <= 100:
        return ("CPU Utilization", COLOR_CPU)

    # Values in typical memory range (GB → large numbers)
    if avg > 1000:
        return ("Memory Usage", COLOR_MEM)

    # Mid-range → likely disk
    return ("Disk", COLOR_DISK)


# ── Chart generation ──────────────────────────────────────────────────────

def _setup_style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [FONT_FAMILY, "Liberation Sans", "Arial"],
        "font.size": 8,
        "axes.titlesize": 10,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "figure.facecolor": "white",
        "axes.facecolor": "#FAFBFC",
        "axes.edgecolor": "#DEE2E6",
        "axes.grid": True,
        "grid.alpha": 0.35,
        "grid.color": "#CED4DA",
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


_setup_style()


def generate_metric_chart(
    data_parts: list[str],
    label: str,
    color: str,
    output_path: str | Path,
) -> str | None:
    """Generate a line chart for one metric module.

    ``data_parts`` is a list of alternating ``utimestamp`` and ``value``
    strings from Pandora's raw module_data response.

    Returns the absolute path to the saved PNG, or None if no valid data.
    """
    if not data_parts or len(data_parts) < 2:
        return None

    # Parse: even indices = timestamps, odd indices = values
    timestamps: list[datetime] = []
    values: list[float] = []

    for i in range(0, len(data_parts) - 1, 2):
        try:
            ts = datetime.fromtimestamp(int(data_parts[i]))
            val = float(data_parts[i + 1])
            timestamps.append(ts)
            values.append(val)
        except (ValueError, OSError):
            continue

    if not timestamps:
        return None

    fig, ax = plt.subplots(figsize=(CHART_W_INCHES, CHART_H_INCHES))

    ax.plot(timestamps, values, color=color, linewidth=1.2, marker=None)
    ax.fill_between(timestamps, values, alpha=0.08, color=color)

    # Formatting
    ax.set_ylabel(label, color=color, fontweight="bold")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=4))

    # Add avg line
    if values:
        avg = sum(values) / len(values)
        unit = "%" if max(values) <= 100 else ""
        ax.axhline(y=avg, color=color, linestyle="--", linewidth=0.7, alpha=0.5)
        ax.text(timestamps[-1], avg, f"  avg {avg:.1f}{unit}",
                fontsize=6, color=color, va="center", alpha=0.8)

    fig.autofmt_xdate()
    fig.tight_layout(pad=0.5)
    fig.savefig(str(output_path), dpi=CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    return str(Path(output_path).resolve())


# ── Document builder ──────────────────────────────────────────────────────

class ReportBuilder:
    """Builds the Resources Usage Metric Report .docx matching reference format.

    Usage::

        builder = ReportBuilder(
            tenant_name="PT Asuransi Central Asia [ACA]",
            period="Mei 2026",
            output_dir=Path(".../output"),
        )
        for agent in agents:
            modules = await client.discover_agent_modules(agent["id_agente"])
            builder.add_vm_block(agent, modules)
        path = builder.save("ACA_Mei_2026")
    """

    def __init__(
        self,
        tenant_name: str,
        period: str,
        output_dir: Path,
    ) -> None:
        self.tenant_name = tenant_name
        self.period = period
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.doc = Document()
        self._chart_paths: list[str] = []
        self._vm_count = 0

        # Page setup — portrait A4, moderate margins
        section = self.doc.sections[0]
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.9)
        section.right_margin = Inches(0.9)

        # Style
        style = self.doc.styles["Normal"]
        style.font.name = "Arial"
        style.font.size = Pt(10)

        self._add_header()

    def _add_header(self) -> None:
        """Title + subtitle matching reference format."""
        # Title
        title = self.doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run("Resources Usage Metric Report")
        run.font.size = Pt(24)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        run.font.name = "Arial"

        # Spacer
        self.doc.add_paragraph("")

        # Subtitle
        sub = self.doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = sub.add_run(f"Report Period: {self.period}")
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        run.font.name = "Arial"

        # Tenant
        tn = self.doc.add_paragraph()
        tn.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = tn.add_run(self.tenant_name)
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(0x6C, 0x75, 0x7D)
        run.font.name = "Arial"

        self.doc.add_paragraph("")

        # Create table: 2 columns
        self._table = self.doc.add_table(rows=1, cols=2)
        self._table.style = "Table Grid"
        # Header row
        hdr = self._table.rows[0].cells
        hdr[0].text = "Item"
        hdr[1].text = "Usage Metric"
        for p in hdr[0].paragraphs:
            for r in p.runs:
                r.font.size = Pt(12)
                r.font.bold = True
                r.font.name = "Arial"
        for p in hdr[1].paragraphs:
            for r in p.runs:
                r.font.size = Pt(12)
                r.font.bold = True
                r.font.name = "Arial"

    def add_vm_block(
        self,
        agent: dict,
        modules: list[dict],
        temp_dir: str,
    ) -> None:
        """Add one VM row + metric chart rows to the table."""
        agent_alias = agent.get("alias", "Unknown")
        agent_name = agent.get("name", "")
        agent_id = agent.get("id_agente", "?")

        # ── VM header row ──────────────────────────────────────────
        vm_row = self._table.add_row()
        vm_label = vm_row.cells[0].paragraphs[0]
        vm_label_run = vm_label.add_run("Virtual Machine")
        vm_label_run.font.size = Pt(12)
        vm_label_run.font.name = "Arial"

        vm_value = vm_row.cells[1].paragraphs[0]
        vm_value_run = vm_value.add_run(agent_alias)
        vm_value_run.font.size = Pt(12)
        vm_value_run.font.bold = True
        vm_value_run.font.name = "Arial"
        if agent_name:
            vm_value.add_run(f"  [{agent_name}]").font.size = Pt(9)

        self._vm_count += 1

        # ── Module metric rows ─────────────────────────────────────
        if not modules:
            # No module data — just add a note
            for label in ["CPU Utilization", "Memory Usage", "Disk C:/", "Disk D:/"]:
                row = self._table.add_row()
                row.cells[0].paragraphs[0].add_run(label).font.size = Pt(12)
                no_data = row.cells[1].paragraphs[0]
                no_data_run = no_data.add_run("No data")
                no_data_run.font.size = Pt(12)
                no_data_run.font.italic = True
            return

        # Classify each module and generate chart
        for mod in modules:
            parts = mod.get("values", [])
            module_id = mod.get("module_id", "?")
            label, color = _classify_metric(parts)

            row = self._table.add_row()

            # Label cell
            label_cell = row.cells[0].paragraphs[0]
            label_run = label_cell.add_run(label)
            label_run.font.size = Pt(12)
            label_run.font.name = "Arial"

            # Chart cell
            chart_path = Path(temp_dir) / f"_chart_{agent_id}_{module_id}.png"
            result = generate_metric_chart(parts, label, color, chart_path)
            if result:
                self._chart_paths.append(result)
                # Embed in cell
                chart_para = row.cells[1].paragraphs[0]
                chart_run = chart_para.add_run()
                chart_run.add_picture(str(chart_path), width=Inches(CHART_W_INCHES))
            else:
                row.cells[1].paragraphs[0].add_run(
                    "No chart data"
                ).font.size = Pt(10)

        # Set column widths
        for row_obj in self._table.rows:
            row_obj.cells[0].width = Inches(1.8)
            row_obj.cells[1].width = Inches(6.0)

    def save(self, filename_stem: str) -> str:
        """Save document and return path."""
        safe = _safe_filename(filename_stem)
        path = self.output_dir / f"{safe}.docx"
        i = 1
        while path.exists():
            path = self.output_dir / f"{safe}_{i}.docx"
            i += 1

        self.doc.save(str(path))
        logger.info("Saved report: %s (%d VMs)", path, self._vm_count)

        # Cleanup chart files
        for cp in self._chart_paths:
            try:
                os.unlink(cp)
            except OSError:
                pass

        return str(path.resolve())


# ── Top-level convenience ──────────────────────────────────────────────────

def build_report(
    tenant_name: str,
    period: str,
    agents: list[dict],
    agent_modules_map: dict[int, list[dict]],
    output_dir: str | Path,
) -> str:
    """Generate the Resources Usage Metric Report.

    Args:
        tenant_name: E.g. "PT Asuransi Central Asia [ACA]"
        period: E.g. "Mei 2026"
        agents: List of agent dicts from PandoraClient.
        agent_modules_map: ``{agent_id: [module_dict, ...]}`` from
            ``PandoraClient.discover_agent_modules()``.
        output_dir: Where to save the .docx.

    Returns:
        Absolute path to the generated file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        builder = ReportBuilder(
            tenant_name=tenant_name,
            period=period,
            output_dir=out,
        )
        for agent in agents:
            agent_id_raw = agent.get("id_agente")
            if agent_id_raw is None:
                continue
            aid = int(agent_id_raw)
            mods = agent_modules_map.get(aid, [])
            builder.add_vm_block(agent, mods, tmpdir)

        safe_tenant = _safe_filename(tenant_name)
        safe_period = period.replace(" ", "_")
        return builder.save(f"{safe_tenant}_{safe_period}")


def _safe_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^\w\-_.\[\]]", "", name)
    return name or "report"
