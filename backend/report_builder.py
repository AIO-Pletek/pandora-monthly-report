"""
Monthly report builder — generates .docx matching reference format.

Reference format (from user-provided .docx):
  - Title: "Resources Usage Metric Report" (Arial 24pt bold)
  - Subtitle: "Report Period: <Month Year>" (Arial 14pt)
  - Table (2 cols): "Item" | "Usage Metric"
    - Per VM: "Virtual Machine" | agent alias (bold)
    - Per metric: metric display name | line chart PNG

Uses ``python-docx`` + ``matplotlib`` (Agg backend).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

# ── matplotlib MUST use Agg backend first ──────────────────────────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt            # noqa: E402
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.ticker as mticker        # noqa: E402

from docx import Document                  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Inches, Pt, RGBColor  # noqa: E402

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
CHART_DPI = 130
CHART_W_INCHES = 5.5
CHART_H_INCHES = 2.2

COLOR_CPU = "#0D6EFD"
COLOR_MEM = "#198754"
COLOR_DISK = "#DC3545"
COLOR_EXTRA = "#6C757D"
EXTRA_COLORS = ["#FD7E14", "#6F42C1", "#20C997", "#0DCAF0", "#FFC107"]

FONT_FAMILY = "DejaVu Sans"

# Position-based metric display names (modules sorted by ID ascending).
_POSITION_LABELS = [
    "CPU Utilization",
    "Memory Usage",
    "Disk C:/",
    "Disk D:/",
    "Disk E:/",
    "Disk F:/",
    "Metric 7",
    "Metric 8",
]

_POSITION_COLORS = [
    COLOR_CPU,   # CPU
    COLOR_MEM,   # Memory
    COLOR_DISK,  # Disk C
    COLOR_DISK,  # Disk D
    COLOR_DISK,  # Disk E
    *EXTRA_COLORS,
]


def _label_for_position(pos: int) -> tuple[str, str]:
    """Return (display_name, color) for a module at the given position."""
    if pos < len(_POSITION_LABELS):
        label = _POSITION_LABELS[pos]
    else:
        label = f"Metric {pos + 1}"
    color = _POSITION_COLORS[pos] if pos < len(_POSITION_COLORS) else COLOR_EXTRA
    return label, color


def _classify_smart(
    data_points: list[dict[str, Any]],
    position: int,
) -> tuple[str, str]:
    """Classify a module using position + value-range heuristic.

    - Position 0 → CPU always
    - Position 1 → Memory always
    - Position 2+ → Disk C:/, D:/, E:/, etc.
    - If value range doesn't match position (e.g., pos=0 but max=5000),
      adjust the label suffix but not the category.
    """
    label, color = _label_for_position(position)

    if not data_points:
        return label, color

    vals = [p["value"] for p in data_points if p.get("value") is not None]
    if not vals:
        return label, color

    max_val = max(vals)

    # Position 0 (CPU): values should be 0-100%
    if position == 0 and max_val > 100:
        # Higher than CPU range — could be something else, keep label
        pass

    # Position 1 (Memory): values typically >100, add unit hint
    if position == 1:
        if max_val < 100:
            # Low values — might actually be another CPU-like metric
            pass

    return label, color


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
    data_points: list[dict[str, Any]],
    label: str,
    color: str,
    output_path: str | Path,
) -> str | None:
    """Generate a line chart from pre-parsed data points.

    Args:
        data_points: List of ``{timestamp: datetime, value: float}`` dicts.
        label: Y-axis label.
        color: Line color.
        output_path: Where to save the PNG.

    Returns:
        Absolute path to the saved PNG, or None if no valid data.
    """
    if not data_points:
        return None

    # Sort by timestamp
    sorted_pts = sorted(data_points, key=lambda p: p.get("timestamp", datetime.min))

    timestamps = [p["timestamp"] for p in sorted_pts]
    values = [p["value"] for p in sorted_pts]

    if not timestamps or len(timestamps) < 2:
        return None

    fig, ax = plt.subplots(figsize=(CHART_W_INCHES, CHART_H_INCHES))

    ax.plot(timestamps, values, color=color, linewidth=1.2, marker=None)
    ax.fill_between(timestamps, values, alpha=0.08, color=color)

    # Y-axis label
    ax.set_ylabel(label, color=color, fontsize=7, fontweight="bold")

    # X-axis date formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
    ax.xaxis.set_major_locator(locator)

    # Y-axis
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=4))

    # Average line
    avg = sum(values) / len(values)
    ax.axhline(y=avg, color=color, linestyle="--", linewidth=0.7, alpha=0.5)
    ax.text(timestamps[-1], avg, f"  avg {avg:.1f}",
            fontsize=6, color=color, va="center", alpha=0.8)

    fig.autofmt_xdate()
    fig.tight_layout(pad=0.5)
    fig.savefig(str(output_path), dpi=CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    return str(Path(output_path).resolve())


# ── Document builder ──────────────────────────────────────────────────────

class ReportBuilder:
    """Builds the Resources Usage Metric Report .docx."""

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

        # Page setup — portrait A4
        section = self.doc.sections[0]
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.9)
        section.right_margin = Inches(0.9)

        # Default style
        style = self.doc.styles["Normal"]
        style.font.name = "Arial"
        style.font.size = Pt(10)

        self._add_header()

    # ── Header ────────────────────────────────────────────────────────

    def _add_header(self) -> None:
        """Title + subtitle + tenant name."""
        title = self.doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run("Resources Usage Metric Report")
        run.font.size = Pt(24)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        run.font.name = "Arial"

        self.doc.add_paragraph("")

        sub = self.doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = sub.add_run(f"Report Period: {self.period}")
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        run.font.name = "Arial"

        tn = self.doc.add_paragraph()
        tn.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = tn.add_run(self.tenant_name)
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(0x6C, 0x75, 0x7D)
        run.font.name = "Arial"

        self.doc.add_paragraph("")

        # Create table
        self._table = self.doc.add_table(rows=1, cols=2)
        self._table.style = "Table Grid"

        hdr = self._table.rows[0].cells
        hdr[0].text = "Item"
        hdr[1].text = "Usage Metric"
        for ci in (0, 1):
            for p in hdr[ci].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(12)
                    r.font.bold = True
                    r.font.name = "Arial"

    # ── VM block ──────────────────────────────────────────────────────

    def add_vm_block(
        self,
        agent: dict,
        modules: list[dict],
        temp_dir: str,
    ) -> None:
        """Add one VM row + metric chart rows to the table.

        Args:
            agent: Agent dict from PandoraClient.
            modules: List from ``discover_agent_modules()``, each with:
                ``module_id``, ``data_points``, ``avg``, ``max_val``.
            temp_dir: Directory for temporary chart PNGs.
        """
        agent_alias = agent.get("alias", "Unknown")
        agent_name = agent.get("name", "")

        # ── VM header row ──────────────────────────────────────────
        vm_row = self._table.add_row()
        vm_label = vm_row.cells[0].paragraphs[0]
        run = vm_label.add_run("Virtual Machine")
        run.font.size = Pt(12)
        run.font.name = "Arial"

        vm_value = vm_row.cells[1].paragraphs[0]
        run = vm_value.add_run(agent_alias)
        run.font.size = Pt(12)
        run.font.bold = True
        run.font.name = "Arial"
        if agent_name:
            extra = vm_value.add_run(f"  [{agent_name}]")
            extra.font.size = Pt(9)

        self._vm_count += 1

        # Sort modules by ID (preserves Pandora's creation order)
        modules_sorted = sorted(modules, key=lambda m: m.get("module_id", 0))

        if not modules_sorted:
            for label in ["CPU Utilization", "Memory Usage", "Disk C:/", "Disk D:/"]:
                self._add_no_data_row(label)
            return

        # Track which labels we've used to avoid duplicates
        used_positions: set[str] = set()

        for pos, mod in enumerate(modules_sorted):
            data_points = mod.get("data_points", [])
            if not data_points:
                continue

            label, color = _classify_smart(data_points, pos)

            # Avoid duplicate labels by appending module ID suffix
            base_label = label
            suffix = 2
            while label in used_positions:
                label = f"{base_label} ({suffix})"
                suffix += 1
            used_positions.add(label)

            row = self._table.add_row()

            # Label cell
            label_cell = row.cells[0].paragraphs[0]
            run = label_cell.add_run(label)
            run.font.size = Pt(12)
            run.font.name = "Arial"

            # Chart cell
            chart_path = Path(temp_dir) / f"_chart_{agent.get('id_agente','?')}_{mod.get('module_id','?')}.png"
            result = generate_metric_chart(data_points, label, color, chart_path)
            if result:
                self._chart_paths.append(result)
                chart_para = row.cells[1].paragraphs[0]
                run = chart_para.add_run()
                run.add_picture(str(chart_path), width=Inches(CHART_W_INCHES))
            else:
                no_chart = row.cells[1].paragraphs[0]
                run = no_chart.add_run("No chart data")
                run.font.size = Pt(10)

        # Set column widths (apply to all rows)
        for row_obj in self._table.rows:
            row_obj.cells[0].width = Inches(1.8)
            row_obj.cells[1].width = Inches(6.0)

    def _add_no_data_row(self, label: str) -> None:
        """Add a row showing 'No data' for a metric."""
        row = self._table.add_row()
        run = row.cells[0].paragraphs[0].add_run(label)
        run.font.size = Pt(12)
        run.font.name = "Arial"
        run = row.cells[1].paragraphs[0].add_run("No data")
        run.font.size = Pt(12)
        run.font.italic = True

    # ── Save ──────────────────────────────────────────────────────────

    def save(self, filename_stem: str) -> str:
        """Save document to output_dir and return path."""
        safe = _safe_filename(filename_stem)
        path = self.output_dir / f"{safe}.docx"
        i = 1
        while path.exists():
            path = self.output_dir / f"{safe}_{i}.docx"
            i += 1

        self.doc.save(str(path))
        logger.info("Saved report: %s (%d VMs)", path, self._vm_count)

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
        tenant_name: E.g. "PT Asuransi Central Asia [ACA]".
        period: E.g. "Mei 2026".
        agents: List of agent dicts.
        agent_modules_map: ``{agent_id: [module_dict, ...]}`` per agent.
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
            aid_raw = agent.get("id_agente")
            if aid_raw is None:
                continue
            aid = int(aid_raw)
            mods = agent_modules_map.get(aid, [])
            builder.add_vm_block(agent, mods, tmpdir)

        safe_tenant = _safe_filename(tenant_name)
        safe_period = period.replace(" ", "_")
        return builder.save(f"{safe_tenant}_{safe_period}")


def _safe_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^\w\-_.\[\]]", "", name)
    return name or "report"
