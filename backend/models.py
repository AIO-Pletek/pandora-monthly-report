"""
Pydantic models for request/response schemas.

Community Edition note:
  - Agent→group mapping is discovered from events, not stored in agents.
  - Module IDs come from events' ``id_agentmodule`` field.
  - Metric data (CPU/RAM/Disk) may not be available if no corresponding
    module fired alerts in the selected period.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Request ─────────────────────────────────────────────────────────────────

class ReportRequest(BaseModel):
    """Request to generate a monthly report."""

    id_group: int = Field(
        ...,
        description="Pandora group ID (tenant) — obtained from get_groups()",
        ge=0,
    )
    group_name: str = Field(
        default="",
        description="Display name of the group (for report cover)",
    )
    year: int = Field(
        ...,
        description="Report year, e.g. 2026",
        ge=2020,
        le=2100,
    )
    month: int = Field(
        ...,
        description="Report month, 1–12",
        ge=1,
        le=12,
    )


# ── Response data ───────────────────────────────────────────────────────────

class GroupInfo(BaseModel):
    """A tenant/group as shown in the UI dropdown."""

    id: int | str
    name: str
    agent_count: int = 0


class AgentSummary(BaseModel):
    """Agent info shown in the availability table."""

    id_agente: int
    alias: str
    name: str = ""  # OS name
    direccion: str = ""
    comentarios: str = ""


class EventSummary(BaseModel):
    """Aggregated event counts for the executive summary."""

    total: int = 0
    critical: int = 0
    warning: int = 0
    info: int = 0
    unknown: int = 0


class MetricPoint(BaseModel):
    """A single data point for a metric module."""

    timestamp: str = ""
    value: float = 0.0


class AgentMetric(BaseModel):
    """Metric data for one agent (CPU, RAM, Disk)."""

    agent_id: int
    agent_alias: str
    cpu_avg: Optional[float] = None
    cpu_max: Optional[float] = None
    ram_avg: Optional[float] = None
    ram_max: Optional[float] = None
    disk_avg: Optional[float] = None
    disk_max: Optional[float] = None


class ReportStatus(str, Enum):
    """Generation status."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportResponse(BaseModel):
    """Response after a successful report generation."""

    status: ReportStatus = ReportStatus.COMPLETED
    filename: str = ""
    download_url: str = ""
    tenant_name: str = ""
    period: str = ""
    total_agents: int = 0
    total_events: int = 0
    message: str = ""


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str
    status: str = "error"
