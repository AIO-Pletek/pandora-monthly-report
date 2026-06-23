"""
Pandora FMS REST API client — read-only wrapper module.

Target: Pandora FMS v7.0 NG **Community Edition** (Open Source).
Verified against real instance June 2026.

Key differences from Enterprise edition:
  - Agents have NO ``id_grupo`` field (Community has no agent-group mapping).
  - ``get_agent_modules`` / ``agent_modules`` returns "No modules retrieved".
  - ``return_type=json`` is RESPECTED (no forced CSV like some older versions).
  - ``module_groups`` returns CSV, not JSON.
  - Events contain ``id_grupo`` / ``group_name`` — usable for grouping.

Strategy:
  - Groups (tenants) come from ``module_groups`` CSV + events enrichment.
  - Agents are NOT filterable by group server-side → fetch all, map via events.
  - Module IDs come from events (``id_agentmodule``), not from agent_modules.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30.0  # seconds
DATE_FMT_DISPLAY = "%Y-%m-%d"     # human-readable input
DATE_FMT_DATETIME = "%Y-%m-%d %H:%M:%S"  # Pandora timestamp format


# ── Exceptions ───────────────────────────────────────────────────────────────
class PandoraAPIError(Exception):
    """Generic Pandora API error (non-auth)."""


class PandoraAuthError(PandoraAPIError):
    """Raised when Pandora returns 'auth error' — credentials are wrong."""


# ── CSV helpers ──────────────────────────────────────────────────────────────

def _parse_pandora_csv(text: str, delimiter: str = ";") -> list[dict]:
    """Parse Pandora CSV response (header row + data rows) into list[dict].

    Returns empty list if text is empty or only a header row.
    """
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []
    headers = lines[0].split(delimiter)
    rows: list[dict] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        values = line.split(delimiter)
        row: dict[str, str] = {}
        for i, hdr in enumerate(headers):
            row[hdr] = values[i] if i < len(values) else ""
        rows.append(row)
    return rows


def _parse_test_response(text: str) -> dict:
    """Parse ``op2=test`` CSV: ``OK,v7.0NG.720,PC180320``."""
    parts = text.strip().split(",")
    return {
        "status": parts[0] if len(parts) > 0 else "",
        "version": parts[1] if len(parts) > 1 else "",
        "build": parts[2] if len(parts) > 2 else "",
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_unix(date_str: str, end_of_day: bool = False) -> int:
    """Convert human-readable date to Unix timestamp (int seconds)."""
    date_str = date_str.strip()
    try:
        dt = datetime.strptime(date_str, "%Y%m%dT%H:%M")
    except ValueError:
        dt = datetime.strptime(date_str, DATE_FMT_DISPLAY)
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _parse_pandora_timestamp(ts_str: str) -> datetime | None:
    """Parse Pandora's ``YYYY-MM-DD HH:MM:SS`` timestamp format."""
    try:
        return datetime.strptime(ts_str.strip(), DATE_FMT_DATETIME)
    except (ValueError, TypeError):
        return None


def _parse_module_data_tokens(raw_text: str) -> list[dict[str, Any]]:
    """Parse Pandora's raw module_data response into structured points.

    Pandora Community Ed returns whitespace-separated tokens where each
    token is a 10-digit Unix timestamp concatenated with the float value,
    WITHOUT any delimiter between them.

    Example: ``"178201193295.27000 178197564795.26000"``
      → ``[{timestamp: datetime(2026,6,20,...), value: 95.27}, ...]``

    Returns empty list if parsing fails.
    """
    tokens = raw_text.strip().split()
    points: list[dict[str, Any]] = []
    for token in tokens:
        token = token.strip()
        if len(token) < 11:  # minimum: 10-digit ts + 1-digit value
            continue
        try:
            ts_str = token[:10]
            val_str = token[10:]
            ts_int = int(ts_str)
            val = float(val_str)
            # Sanity check: timestamp should be in reasonable range
            if ts_int < 1000000000 or ts_int > 2000000000:
                continue
            dt = datetime.fromtimestamp(ts_int)
            points.append({"timestamp": dt, "value": val, "utimestamp": ts_int})
        except (ValueError, OSError):
            continue
    return points


# ── Client ───────────────────────────────────────────────────────────────────

class PandoraClient:
    """Async read-only client for Pandora FMS Community Edition API.

    Usage::

        client = PandoraClient(
            base_url=config.PANDORA_BASE_URL,
            api_user=config.PANDORA_API_USER,
            api_pass=config.PANDORA_API_USER_PASS,
            api_password=config.PANDORA_API_PASSWORD,
        )
        info = await client.test()
        groups = await client.get_groups()
        agents = await client.get_agents()
        events = await client.get_events(date_start="2025-06-01", date_end="2025-06-30")
    """

    def __init__(
        self,
        base_url: str,
        api_user: str,
        api_pass: str,
        api_password: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        db: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/include/api.php"
        self.api_user = api_user
        self.api_pass = api_pass
        self.api_password = api_password
        self.timeout = timeout
        self.db = db  # PandoraDB instance (optional, for Community Ed)

    # ── Low-level call ──────────────────────────────────────────────────

    async def _call(
        self,
        op2: str,
        extra_params: dict[str, Any] | None = None,
        id_: str | None = None,
        *,
        timeout: float | None = None,
        expect_csv: bool = False,
    ) -> Any:
        """Issue a GET request to the Pandora API.

        All requests include ``op=get``, auth, and ``return_type=json``
        (unless ``expect_csv`` forces CSV mode).

        Args:
            op2: Operation name (e.g. ``"all_agents"``).
            extra_params: Additional query params.
            id_: Value for the ``id`` param.
            timeout: Per-call override.
            expect_csv: If True, use ``return_type=csv`` and parse as CSV.

        Returns:
            Parsed data — dict, list[dict], or parsed CSV list.
        """
        params: dict[str, Any] = {
            "op": "get",
            "op2": op2,
            "user": self.api_user,
            "pass": self.api_pass,
            "apipass": self.api_password,
            "return_type": "csv" if expect_csv else "json",
        }
        if id_ is not None:
            params["id"] = id_
        if extra_params:
            params.update(extra_params)

        t = timeout if timeout is not None else self.timeout
        logger.debug("Pandora API: op2=%s (csv=%s)", op2, expect_csv)

        async with httpx.AsyncClient(timeout=t) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()

        text = resp.text.strip()

        # Auth error check (plain string or wrapped JSON)
        if text.lower() in ('"auth error"', "'auth error'", "auth error"):
            raise PandoraAuthError(
                "Pandora API returned 'auth error' — check credentials in .env"
            )

        if not text:
            return []

        # ── Handle different response formats ──────────────────────────

        # op2=test always returns CSV regardless of return_type
        if op2 == "test":
            return _parse_test_response(text)

        if expect_csv:
            return _parse_pandora_csv(text)

        # JSON path
        try:
            data = resp.json()
        except json.JSONDecodeError:
            # Fallback: try CSV parse (some ops return CSV despite asking JSON)
            logger.debug("op2=%s: JSON decode failed, trying CSV", op2)
            csv_data = _parse_pandora_csv(text)
            if csv_data:
                return csv_data
            raise PandoraAPIError(
                f"Pandora returned unrecognized format for op2={op2}: "
                f"{text[:500]}"
            )

        # Check for error responses disguised as JSON:
        #   {"type":"string","data":"This operation does not exist."}
        #   ["This operation does not exist."]
        if isinstance(data, dict):
            if data.get("type") == "string" and "data" in data:
                msg = str(data["data"])
                if any(kw in msg.lower() for kw in
                       ("does not exist", "error", "invalid", "auth", "acl")):
                    raise PandoraAPIError(
                        f"Pandora rejected op2={op2}: {msg}"
                    )
            # Empty data from wrapped format
            if data.get("data") == "" or data.get("data") == "No modules retrieved.":
                return []
            # Unwrap {"type":"array","data":[...]}
            if data.get("type") == "array" and "data" in data:
                return data["data"]

        if isinstance(data, list) and len(data) > 0 and all(isinstance(x, str) for x in data):
            combined = " ".join(data).lower()
            if any(kw in combined for kw in
                   ("does not exist", "error", "no modules")):
                raise PandoraAPIError(
                    f"Pandora rejected op2={op2}: {data}"
                )

        return data

    # ── Public API ──────────────────────────────────────────────────────

    # -- 3.1  Connection test -------------------------------------------

    async def test(self) -> dict:
        """Test connection and return ``{status, version, build}``."""
        return await self._call("test")

    # -- 3.2  Groups (tenants) ------------------------------------------

    async def get_module_groups(self) -> list[dict]:
        """Return module groups from Pandora Community Edition.

        Pandora returns CSV WITHOUT a header row — every line is ``id;name``.
        e.g.::
            1;General
            2;Networking
            ...

        Returns list of dicts with ``id`` (int) and ``name`` (str).
        """
        raw_text = ""
        params: dict[str, Any] = {
            "op": "get",
            "op2": "module_groups",
            "user": self.api_user,
            "pass": self.api_pass,
            "apipass": self.api_password,
            "return_type": "csv",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()
        raw_text = resp.text.strip()

        if not raw_text:
            return []

        result: list[dict] = []
        for line in raw_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            if len(parts) >= 2 and parts[0].isdigit():
                try:
                    result.append({
                        "id": int(parts[0]),
                        "name": parts[1],
                    })
                except (ValueError, IndexError):
                    pass
        return result

    async def get_groups(self) -> list[dict]:
        """Return all groups (tenants) for UI dropdown.

        DB first (instant, complete), API fallback.
        """
        # DB path (preferred)
        if self.db:
            try:
                rows = self.db.get_groups()
                result = []
                for r in rows:
                    gid = r.get("id_grupo")
                    count = self.db.get_agent_count_by_group(gid)
                    result.append({
                        "id": gid, "name": r.get("nombre", f"Group {gid}"),
                        "agent_count": count,
                    })
                if result:
                    logger.info("get_groups: %d from DB", len(result))
                    return sorted(result, key=lambda g: str(g["name"]))
            except Exception as e:
                logger.warning("DB groups failed: %s, falling back to API", e)

        # API fallback (same as before)
        return await self._get_groups_via_api()

    async def _get_groups_via_api(self) -> list[dict]:
        """Get groups via Pandora API (fallback when no DB)."""
        groups: dict[int, dict] = {}
        try:
            params = {"op":"get","op2":"groups","user":self.api_user,
                      "pass":self.api_pass,"apipass":self.api_password,"return_type":"csv"}
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(self.base_url, params=params)
            for line in r.text.strip().split("\n"):
                if not line.strip(): continue
                parts = line.split(";")
                if len(parts) >= 2 and parts[0].isdigit():
                    groups[int(parts[0])] = {"id": int(parts[0]), "name": parts[1], "agent_count": 0}
        except Exception as e:
            logger.info("op2=groups failed: %s", e)
        return sorted(groups.values(), key=lambda g: str(g["name"]))

    # -- 3.3  Agents ----------------------------------------------------

    async def get_agents(self) -> list[dict]:
        """Return ALL agents."""
        if self.db:
            try:
                rows = self.db.get_agents()
                result = []
                for r in rows:
                    result.append({
                        "id_agente": r.get("id_agente"),
                        "alias": r.get("alias", ""),
                        "name": "",  # not in DB
                        "direccion": r.get("direccion", ""),
                        "comentarios": r.get("comentarios", ""),
                        "url_address": "",
                        "nombre": "",
                        "id_grupo": r.get("id_grupo"),
                        "grupo_nombre": r.get("grupo_nombre", ""),
                    })
                return result
            except Exception as e:
                logger.warning("DB agents failed: %s", e)
        # API fallback
        agents = await self._call("all_agents")
        if isinstance(agents, list): return agents
        if isinstance(agents, dict) and "data" in agents: return agents["data"]
        return []

    async def get_agents_for_group(self, id_group: int) -> list[dict]:
        """Return agents in a group."""
        if self.db:
            try:
                rows = self.db.get_agents_by_group(int(id_group))
                result = []
                for r in rows:
                    result.append({
                        "id_agente": r.get("id_agente"),
                        "alias": r.get("alias", ""),
                        "name": "",
                        "direccion": r.get("direccion", ""),
                        "comentarios": r.get("comentarios", ""),
                        "url_address": "",
                        "nombre": "",
                    })
                logger.info("Group %d: %d agents from DB", id_group, len(result))
                return result
            except Exception as e:
                logger.warning("DB agents_by_group failed: %s", e)
        # API fallback
        return await self._get_agents_for_group_via_api(int(id_group))

    async def _get_agents_for_group_via_api(self, id_group: int) -> list[dict]:
        """Fallback: use CSV all_agents filter."""
        agent_ids: set[int] = set()
        try:
            params = {"op":"get","op2":"all_agents","user":self.api_user,
                      "pass":self.api_pass,"apipass":self.api_password,
                      "other":f"|{id_group}|||||0","other_mode":"url_encode_separator_|",
                      "return_type":"csv"}
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(self.base_url, params=params)
            for ln in r.text.strip().split("\n"):
                d = "".join(ch for ch in ln.strip() if ch.isdigit())
                if d and len(d) <= 10:
                    agent_ids.add(int(d))
        except Exception as e:
            logger.exception("CSV filter failed: %s", e)
        all_agents = await self.get_agents()
        return [a for a in all_agents if int(a.get("id_agente", 0)) in agent_ids]

    # -- 3.4  Agent modules (DB: real names, real IDs) -----------------

    async def discover_agent_modules(self, agent_id: int) -> list[dict]:
        """Discover metric modules + data for an agent.

        DB path: query tagente_modulo directly → get real names + IDs.
        API fallback: events-based sequential scan.
        """
        if self.db:
            return await self._discover_via_db(int(agent_id))
        return await self._discover_via_events(int(agent_id))

    async def _discover_via_db(self, agent_id: int) -> list[dict]:
        """Get modules from DB, fetch data from API."""
        try:
            rows = self.db.get_agent_modules(agent_id)
        except Exception as e:
            logger.warning("DB modules failed: %s", e)
            return await self._discover_via_events(agent_id)

        if not rows:
            return []

        # Filter to metric modules only by name.
        cpu_ok = ["cpu load", "cpu usage", "cpu utilization"]
        mem_ok = ["mem", "memory", "ram"]
        # Disk: only essential mounts (/, /var, /data, /home) + Windows drives
        disk_prefixes = ["disk", "storage"]
        allowed_disks = ["/ ", " /", "disk_/ ", "disk_/\"",  # root disk
                        "/var", "/data", "/home",  # Linux essential
                        "/data-nfs", "/var/www", "/opt",  # Linux extra
                        "c:", "d:", "e:", "f:"]  # Windows
        skip_disk_kw = ["/boot", "/tmp", "/mnt", "/snap", "/owncloud",
                       "/backup", "/pgsql", "/apps",
                       "/var/lib", "/nimble",
                       "temp_mount", ".temp_mount", "temp mount",
                       "freedisk", "spool"]
        skip_kw = ["host alive", "host latency", "latency", "icmp", "ping",
                   "ifadminstatus", "ifoperstatus", "traffic", "ifinoctets",
                   "ifoutoctets", "ifdescr", "service", "status", "process",
                   "tcp", "udp", "snmp", "check port",
                   "load average", "iowait", "processor",
                   "swap", "swap_used", "swap used"]

        relevant = []
        for r in rows:
            name = (r.get("nombre") or "").lower()
            if any(kw in name for kw in skip_kw):
                continue
            match = False
            if any(kw in name for kw in cpu_ok):
                match = True
            elif any(kw in name for kw in mem_ok):
                match = True
            elif any(kw in name for kw in disk_prefixes):
                # Disk: only keep if path matches allowed list
                if any(kw in name for kw in skip_disk_kw):
                    continue
                if any(ok in name for ok in allowed_disks):
                    match = True
            if match:
                relevant.append(r)

        logger.info("Agent %d: %d modules from DB (%d metric)", agent_id, len(rows), len(relevant))

        # Fetch data for each relevant module (max 10)
        found = []
        for mod in relevant[:10]:
            mid = mod.get("id_agente_modulo")
            if not mid:
                continue
            try:
                raw = await self._raw_module_data(int(mid))
            except (PandoraAPIError, Exception):
                continue
            if not raw:
                continue
            points = _parse_module_data_tokens(raw)
            if points:
                found.append({
                    "module_id": int(mid),
                    "module_name": mod.get("nombre", f"Module {mid}"),
                    "data_points": points,
                    "count": len(points),
                    "avg": sum(p["value"] for p in points) / len(points),
                    "max_val": max(p["value"] for p in points),
                })
        return sorted(found, key=lambda m: m["module_id"])

    async def get_module_data(self, module_id: int, date_start: str, date_end: str) -> list[dict]:
        """Return historical data points for a single module."""
        result = await self._call("module_data", id_=str(module_id))
        if isinstance(result, list): data = result
        elif isinstance(result, dict) and "data" in result: data = result["data"]
        else: data = []
        if data and date_start and date_end:
            ts_s = _to_unix(date_start, end_of_day=False)
            ts_e = _to_unix(date_end, end_of_day=True)
            data = [d for d in data if isinstance(d,dict) and ts_s <= int(d.get("utimestamp",0)) <= ts_e]
        return data

    async def get_events(self, id_group=None, date_start=None, date_end=None,
                         criticity=None, event_type=None, status=None, limit=0) -> list[dict]:
        """Return events, filtered client-side."""
        events = await self._call("events")
        if isinstance(events, dict) and "data" in events: events = events["data"]
        if not isinstance(events, list): events = []
        ts_s = _to_unix(date_start) if date_start else None
        ts_e = _to_unix(date_end, end_of_day=True) if date_end else None
        filtered = []
        for evt in events:
            if not isinstance(evt, dict): continue
            if id_group is not None and int(evt.get("id_grupo",0)) != int(id_group): continue
            if ts_s is not None or ts_e is not None:
                uts = int(evt.get("utimestamp",0))
                if ts_s and uts < ts_s: continue
                if ts_e and uts > ts_e: continue
            if criticity is not None and int(evt.get("criticity",0)) != int(criticity): continue
            if event_type and evt.get("event_type") != event_type: continue
            if status is not None and int(evt.get("estado",0)) != int(status): continue
            filtered.append(evt)
        return filtered[:limit] if limit and limit > 0 else filtered

    async def _discover_via_events(self, agent_id: int) -> list[dict]:
        """Fallback: find modules via events + sequential scan."""
        if not hasattr(self, "_cached_events"):
            raw = await self._call("events")
            if isinstance(raw, dict) and "data" in raw: raw = raw["data"]
            self._cached_events = raw if isinstance(raw, list) else []
        known = set()
        for evt in self._cached_events:
            if not isinstance(evt, dict): continue
            try:
                if int(evt.get("id_agente",0)) == int(agent_id):
                    known.add(int(evt.get("id_agentmodule",0)))
            except (ValueError, TypeError): pass
        if not known: return []
        center = min(known)
        found = []
        for mid in range(max(1, center - 15), center + 15):
            try: raw = await self._raw_module_data(mid)
            except PandoraAPIError: continue
            if not raw: continue
            points = _parse_module_data_tokens(raw)
            if points:
                found.append({"module_id":mid,"module_name":f"Module {mid}",
                    "data_points":points,"count":len(points),
                    "avg":sum(p["value"] for p in points)/len(points),
                    "max_val":max(p["value"] for p in points)})
        return sorted(found, key=lambda m: m["module_id"])

    async def _raw_module_data(self, module_id: int) -> str:
        """Fetch raw module_data text without parsing.

        Community Edition returns whitespace-separated timestamp+value tokens
        (e.g. ``178201193295.27000`` -- no delimiter between the 10-digit
        Unix timestamp and the float value).

        Also unwraps JSON-wrapped error messages some Pandora versions return.
        """
        params: dict[str, Any] = {
            "op": "get",
            "op2": "module_data",
            "user": self.api_user,
            "pass": self.api_pass,
            "apipass": self.api_password,
            "return_type": "json",
            "id": str(module_id),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(self.base_url, params=params)
                resp.raise_for_status()
            text = resp.text.strip()
        except Exception:
            return ""

        # Unwrap JSON-wrapped responses: {"type":"string","data":"No data..."}
        if text.startswith("{"):
            try:
                import json as _json
                data = _json.loads(text)
                if isinstance(data, dict):
                    inner = data.get("data") or data.get("error") or ""
                    if isinstance(inner, str):
                        text = inner.strip()
            except (json.JSONDecodeError, ValueError):
                pass

        if not text:
            return ""
        lower = text.lower()
        if lower.startswith("no data") or "no data to show" in lower:
            return ""
        if lower == "error in the parameters.":
            return ""

        return text
