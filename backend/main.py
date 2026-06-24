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
    from config import (
        PANDORA_DB_HOST, PANDORA_DB_PORT, PANDORA_DB_USER,
        PANDORA_DB_PASS, PANDORA_DB_NAME,
    )
    from db_client import PandoraDB

    db = None
    if PANDORA_DB_USER and PANDORA_DB_PASS:
        try:
            db = PandoraDB(
                host=PANDORA_DB_HOST, port=PANDORA_DB_PORT,
                user=PANDORA_DB_USER, password=PANDORA_DB_PASS,
                database=PANDORA_DB_NAME,
            )
            logger.info("PandoraDB connected")
        except Exception as e:
            logger.warning("PandoraDB unavailable: %s", e)

    return PandoraClient(
        base_url=PANDORA_BASE_URL,
        api_user=PANDORA_API_USER,
        api_pass=PANDORA_API_USER_PASS,
        api_password=PANDORA_API_PASSWORD,
        db=db,
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
    """Generate a Resources Usage Metric Report.

    1. Fetch agents for the group.
    2. Discover metric modules per agent.
    3. Build the .docx report with per-VM charts.
    """
    client = _get_client()

    # Build date range — monthly or custom
    if req.month > 0 and req.year > 0:
        # Monthly report
        last_day = calendar.monthrange(req.year, req.month)[1]
        date_start = f"{req.year:04d}-{req.month:02d}-01"
        date_end = f"{req.year:04d}-{req.month:02d}-{last_day:02d}"
        month_name = calendar.month_name[req.month]
        period = f"{month_name} {req.year}"
    elif req.date_start and req.date_end:
        # Custom date range (weekly, daily, etc.)
        date_start = req.date_start
        date_end = req.date_end
        period = f"{date_start} to {date_end}"
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either (year+month) or (date_start+date_end)."
        )
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

        # 2. Discover metric modules for each agent (with per-agent error handling)
        agent_modules_map: dict[int, list[dict]] = {}
        for agent in agents:
            aid = agent.get("id_agente")
            if aid is None:
                continue
            aid_int = int(aid)
            try:
                mods = await client.discover_agent_modules(aid_int)
            except Exception as e:
                logger.error("Agent %d discovery failed: %s", aid_int, e)
                mods = []
            if mods:
                agent_modules_map[aid_int] = mods
                logger.info(
                    "Agent %s (%s): %d modules discovered",
                    aid, agent.get("alias", "?"), len(mods),
                )
            else:
                agent_modules_map[aid_int] = []

        total_modules = sum(len(m) for m in agent_modules_map.values())

        logger.info(
            "Data: %d agents, %d modules for '%s' / %s",
            len(agents), total_modules, group_name, period,
        )

        # 3. Build the .docx report
        from report_builder import build_report as build_usage_report
        output_path = build_usage_report(
            tenant_name=group_name,
            period=period,
            date_start=date_start,
            date_end=date_end,
            agents=agents,
            agent_modules_map=agent_modules_map,
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
            total_events=total_modules,
            message=f"Report generated with {total_modules} metric charts for {len(agents)} agents.",
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
