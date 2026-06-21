"""
FastAPI application — Pandora Monthly Report.

Endpoints:
    GET  /                         — Main UI (form).
    GET  /api/health               — Health check.
    GET  /api/groups               — List tenant/groups for dropdown.
    POST /api/report/generate      — Trigger report generation.
    GET  /api/report/download/{f}  — Download a generated .docx file.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000
    uvicorn main:app --host 127.0.0.1 --port 8000  (behind nginx)
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from config import (
    APP_ENV,
    APP_PORT,
    OUTPUT_DIR,
    PANDORA_API_PASSWORD,
    PANDORA_API_USER,
    PANDORA_API_USER_PASS,
    PANDORA_BASE_URL,
)
from models import GroupInfo, ReportRequest, ReportResponse, ReportStatus
from pandora_client import PandoraAPIError, PandoraAuthError, PandoraClient
from report_builder import build_report

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO if APP_ENV != "development" else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

# ── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Pandora Monthly Report",
    version="0.1.0",
    docs_url=None if APP_ENV == "production" else "/docs",
)

# ── Jinja2 setup ───────────────────────────────────────────────────────────
from jinja2 import Environment, FileSystemLoader  # noqa: E402

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _render_template(name: str, **ctx) -> HTMLResponse:
    """Render a Jinja2 template to HTMLResponse (avoids starlette compat issues)."""
    template = _jinja_env.get_template(name)
    return HTMLResponse(template.render(**ctx))


# ── Client factory ─────────────────────────────────────────────────────────

def _get_client() -> PandoraClient:
    """Create a PandoraClient from environment config."""
    return PandoraClient(
        base_url=PANDORA_BASE_URL,
        api_user=PANDORA_API_USER,
        api_pass=PANDORA_API_USER_PASS,
        api_password=PANDORA_API_PASSWORD,
    )


# ── Routes: UI ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Main page — group selection + month picker form."""
    now = datetime.now()
    months = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December"),
    ]
    years = list(range(now.year - 2, now.year + 1))
    return _render_template("index.html",
        current_year=now.year,
        current_month=now.month,
        months=months,
        years=years,
    )


# ── Routes: API ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Health check — also tests Pandora connectivity."""
    result: dict = {"status": "ok", "pandora": "unknown"}
    try:
        client = _get_client()
        info = await client.test()
        result["pandora"] = f"connected (v{info.get('version','?')})"
    except PandoraAuthError:
        result["status"] = "error"
        result["pandora"] = "auth_error"
    except Exception as e:
        result["status"] = "error"
        result["pandora"] = f"error: {e}"
    return result


@app.get("/api/groups")
async def list_groups():
    """Return groups (tenants) for the UI dropdown."""
    try:
        client = _get_client()
        groups = await client.get_groups()
    except PandoraAuthError:
        raise HTTPException(status_code=503, detail="Pandora auth error — check .env credentials")
    except PandoraAPIError as e:
        raise HTTPException(status_code=502, detail=f"Pandora API error: {e}")
    except Exception as e:
        logger.exception("Unexpected error in /api/groups")
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    result: list[GroupInfo] = []
    for g in groups:
        if isinstance(g, dict):
            result.append(GroupInfo(
                id=g.get("id", ""),
                name=g.get("name", "Unknown"),
                agent_count=g.get("agent_count", g.get("agent_count", 0)),
            ))

    if not result:
        # Fallback: return empty list so UI shows "no groups"
        pass

    return {"groups": result, "count": len(result)}


@app.post("/api/report/generate", response_model=ReportResponse)
async def generate_report(req: ReportRequest):
    """Generate a monthly report for a specific tenant + month.

    1. Fetch agents for the group.
    2. Fetch events for the group in the date range.
    3. Build the .docx report.
    """
    client = _get_client()

    # Build date range
    year = req.year
    month = req.month
    last_day = calendar.monthrange(year, month)[1]
    date_start = f"{year:04d}-{month:02d}-01"
    date_end = f"{year:04d}-{month:02d}-{last_day:02d}"
    month_name = calendar.month_name[month]
    period = f"{month_name} {year}"
    group_name = req.group_name or f"Group {req.id_group}"

    logger.info(
        "Generating report: group=%s (%s), period=%s",
        req.id_group, group_name, period,
    )

    try:
        # 1. Fetch agents for this group
        agents = await client.get_agents_for_group(int(req.id_group))
        if not agents:
            return ReportResponse(
                status=ReportStatus.COMPLETED,
                filename="",
                download_url="",
                tenant_name=group_name,
                period=period,
                total_agents=0,
                total_events=0,
                message="No agents found in this group for the selected period.",
            )

        # 2. Fetch events for this group in the date range
        events = await client.get_events(
            id_group=int(req.id_group),
            date_start=date_start,
            date_end=date_end,
        )

        logger.info(
            "Data: %d agents, %d events for '%s' / %s",
            len(agents), len(events), group_name, period,
        )

        # 3. Build the .docx report
        output_path = build_report(
            tenant_name=group_name,
            period=period,
            date_start=date_start,
            date_end=date_end,
            agents=agents,
            events=events,
            output_dir=OUTPUT_DIR,
        )

        filename = Path(output_path).name
        download_url = f"/api/report/download/{filename}"

        return ReportResponse(
            status=ReportStatus.COMPLETED,
            filename=filename,
            download_url=download_url,
            tenant_name=group_name,
            period=period,
            total_agents=len(agents),
            total_events=len(events),
            message="Report generated successfully.",
        )

    except PandoraAuthError:
        raise HTTPException(
            status_code=503,
            detail="Pandora authentication failed. Check .env credentials.",
        )
    except PandoraAPIError as e:
        logger.exception("Pandora API error during report generation")
        raise HTTPException(status_code=502, detail=f"Pandora API error: {e}")
    except Exception as e:
        logger.exception("Unexpected error during report generation")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/report/download/{filename:path}")
async def download_report(filename: str):
    """Download a previously generated .docx report.

    Only files inside ``OUTPUT_DIR`` are served (path-traversal protection).
    """
    file_path = OUTPUT_DIR / filename
    # Security: resolve to prevent path traversal
    resolved = file_path.resolve()
    if not str(resolved).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Report file not found. Please generate it first.")

    if not resolved.suffix.lower() in (".docx", ".doc"):
        raise HTTPException(status_code=403, detail="File type not allowed")

    return FileResponse(
        path=str(resolved),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


# ── Startup ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=APP_PORT,
        reload=(APP_ENV == "development"),
    )
