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
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/include/api.php"
        self.api_user = api_user
        self.api_pass = api_pass
        self.api_password = api_password
        self.timeout = timeout

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

    async def get_groups_with_agents(self) -> list[dict]:
        """Return groups enriched with a mapping to which agents belong.

        Since Community Edition has no agent→group mapping, this method:
          1. Loads all agents and all events.
          2. Maps agents to groups based on which group their events appear in.
          3. Returns groups with ``agent_ids`` list attached.

        Returns:
            List of dicts: ``{"id": str, "name": str, "agent_ids": [int, ...]}``
        """
        agents = await self.get_agents()
        events = await self._call("events")
        if not isinstance(events, list):
            events = []

        # Build agent→group mapping from events
        agent_group: dict[int, set[int]] = defaultdict(set)
        for evt in events:
            if not isinstance(evt, dict):
                continue
            aid = evt.get("id_agente")
            gid = evt.get("id_grupo")
            if aid and gid:
                try:
                    agent_group[int(aid)].add(int(gid))
                except (ValueError, TypeError):
                    pass

        # Build group registry from events
        groups: dict[int, dict] = {}
        for evt in events:
            if not isinstance(evt, dict):
                continue
            gid = evt.get("id_grupo")
            gname = evt.get("group_name")
            if gid and gname:
                try:
                    gid_int = int(gid)
                except (ValueError, TypeError):
                    continue
                if gid_int not in groups:
                    groups[gid_int] = {
                        "id": gid_int,
                        "name": gname,
                        "agent_ids": [],
                    }

        # Populate agent_ids
        for aid, gids in agent_group.items():
            for gid in gids:
                if gid in groups:
                    groups[gid]["agent_ids"].append(aid)
        for g in groups.values():
            g["agent_ids"] = sorted(set(g["agent_ids"]))
            g["agent_count"] = len(g["agent_ids"])

        return sorted(groups.values(), key=lambda g: str(g["name"]))

    async def get_groups(self) -> list[dict]:
        """Return all groups (tenants) for UI dropdown.

        Merges two sources:
          1. Groups extracted from events (has agent mapping + real names).
          2. Module groups from ``module_groups`` (structural groups).

        Returns list of dicts with ``id``, ``name``, ``agent_count``.
        """
        merged: dict[int, dict] = {}

        # Source 1: module_groups (structural — all groups)
        try:
            mgroups = await self.get_module_groups()
            for g in mgroups:
                gid = g.get("id", "")
                try:
                    gid_int = int(gid)
                except (ValueError, TypeError):
                    continue
                merged[gid_int] = {
                    "id": gid_int,
                    "name": g.get("name", f"Group {gid_int}"),
                    "agent_ids": [],
                }
            logger.info("Got %d groups from module_groups", len(mgroups))
        except PandoraAPIError:
            logger.info("module_groups failed")

        # Source 2: events (enriches with real group names + agent mapping)
        try:
            agents = await self.get_agents()
            events = await self._call("events")
            if isinstance(events, dict) and "data" in events:
                events = events["data"]
            if not isinstance(events, list):
                events = []

            agent_group: dict[int, set[int]] = defaultdict(set)
            for evt in events:
                if not isinstance(evt, dict):
                    continue
                aid = evt.get("id_agente")
                gid = evt.get("id_grupo")
                if aid and gid:
                    try:
                        agent_group[int(aid)].add(int(gid))
                    except (ValueError, TypeError):
                        pass

            for evt in events:
                if not isinstance(evt, dict):
                    continue
                gid = evt.get("id_grupo")
                gname = evt.get("group_name", "")
                if gid:
                    try:
                        gid_int = int(gid)
                    except (ValueError, TypeError):
                        continue
                    if gid_int not in merged:
                        merged[gid_int] = {
                            "id": gid_int,
                            "name": gname or f"Group {gid_int}",
                            "agent_ids": [],
                        }
                    elif gname and merged[gid_int]["name"].startswith("Group "):
                        merged[gid_int]["name"] = gname

            # Count total agents (Community Ed: agent→group from events only)
            for aid, gids in agent_group.items():
                for gid in gids:
                    if gid in merged:
                        merged[gid]["agent_ids"].append(aid)
            for g in merged.values():
                g["agent_ids"] = sorted(set(g["agent_ids"]))
                g["agent_count"] = len(g["agent_ids"])
        except PandoraAPIError:
            logger.info("Could not enrich groups from events")

        result = sorted(merged.values(), key=lambda g: str(g.get("name", "")))
        logger.info("get_groups: %d total groups", len(result))
        return result

    # -- 3.3  Agents ----------------------------------------------------

    async def get_agents(self) -> list[dict]:
        """Return ALL agents from Pandora.

        Community Edition has no server-side group filter for agents.
        Use :meth:`get_agents_for_group` to filter by group client-side.

        Returns:
            List of agent dicts with keys:
            ``id_agente``, ``alias``, ``name`` (OS), ``direccion``,
            ``comentarios``, ``nombre``, ``url_address``.
        """
        agents = await self._call("all_agents")
        if isinstance(agents, list):
            return agents
        if isinstance(agents, dict) and "data" in agents:
            return agents["data"]
        return []

    async def get_agents_for_group(
        self, id_group: int,
    ) -> list[dict]:
        """Return ALL agents for report generation.

        Pandora Community Edition has NO server-side agent→group filter
        and events (40 items) are too sparse to reliably map agents to
        groups.  Therefore we return all agents for every group — the
        report will list all monitored VMs.

        Args:
            id_group: Group ID (used only for logging).
        """
        return await self.get_agents()

    # -- 3.4  Module data -----------------------------------------------

    async def get_module_data(
        self,
        module_id: int,
        date_start: str,
        date_end: str,
        *,
        period: int = 0,
    ) -> list[dict]:
        """Return historical data points for a single module.

        Uses ``op2=module_data&id=<module_id>``.

        For Community Edition, ``other`` / ``other_mode`` params are NOT
        compatible — they cause "Error in the parameters".  We rely on
        ``return_type=json`` and filter client-side if needed.

        Args:
            module_id: Module ID (from events' ``id_agentmodule``).
            date_start: Start date ``"YYYY-MM-DD"``.
            date_end: End date ``"YYYY-MM-DD"``.
            period: Not used in Community Ed (ignored).

        Returns:
            List of data-point dicts. Each may contain ``utimestamp``,
            ``datos`` (value), etc. Empty list if no data.
        """
        # Try JSON first (no other/other_mode params)
        result = await self._call("module_data", id_=str(module_id))
        if isinstance(result, list):
            data = result
        elif isinstance(result, dict) and "data" in result:
            data = result["data"]
        else:
            data = []

        # Filter client-side by date range
        if data and date_start and date_end:
            ts_start = _to_unix(date_start, end_of_day=False)
            ts_end = _to_unix(date_end, end_of_day=True)
            filtered = []
            for dp in data:
                if not isinstance(dp, dict):
                    continue
                uts = dp.get("utimestamp")
                if uts is not None:
                    try:
                        uts_int = int(uts)
                    except (ValueError, TypeError):
                        continue
                    if ts_start <= uts_int <= ts_end:
                        filtered.append(dp)
            return filtered

        return data

    # -- 3.5  Events / alerts -------------------------------------------

    async def get_events(
        self,
        *,
        id_group: int | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        criticity: int | None = None,
        event_type: str | None = None,
        status: int | None = None,
        limit: int = 0,
    ) -> list[dict]:
        """Return events from Pandora, optionally filtered.

        Community Edition does NOT support ``other`` / ``other_mode``
        params for events — they cause "Error in the parameters".
        All filtering is done client-side.

        Args:
            id_group: Filter by group ID (events' ``id_grupo``).
            date_start: Start date ``"YYYY-MM-DD"``.
            date_end: End date ``"YYYY-MM-DD"``.
            criticity: Severity filter (0=info, 1=low, 2=medium, 3=warning, 4=critical).
            event_type: ``"alert_fired"``, ``"alert_recovered"``, etc.
            status: Event status (0=new, 1=validated, 2=in progress).
            limit: If > 0, return only first N events.

        Returns:
            List of event dicts with keys: ``id_evento``, ``id_agente``,
            ``agent_name``, ``id_grupo``, ``group_name``, ``criticity``,
            ``criticity_name``, ``event_type``, ``timestamp``, ``utimestamp``,
            ``evento`` (description), ``estado``, ``module_name``,
            ``id_agentmodule``, etc.
        """
        # Pandora returns max ~40 events per call without pagination.
        events = await self._call("events")
        if not isinstance(events, list):
            if isinstance(events, dict) and "data" in events:
                events = events["data"]
            else:
                events = []

        # Client-side filters
        ts_start = _to_unix(date_start, end_of_day=False) if date_start else None
        ts_end = _to_unix(date_end, end_of_day=True) if date_end else None

        filtered: list[dict] = []
        for evt in events:
            if not isinstance(evt, dict):
                continue

            # Group filter
            if id_group is not None:
                try:
                    evt_group = int(evt.get("id_grupo") or 0)
                except (ValueError, TypeError):
                    continue
                if evt_group != int(id_group):
                    continue

            # Date filter
            uts_str = evt.get("utimestamp")
            if ts_start is not None or ts_end is not None:
                if uts_str is None:
                    continue
                try:
                    uts = int(uts_str)
                except (ValueError, TypeError):
                    continue
                if ts_start is not None and uts < ts_start:
                    continue
                if ts_end is not None and uts > ts_end:
                    continue

            # Severity filter
            if criticity is not None:
                try:
                    evt_crit = int(evt.get("criticity") or 0)
                except (ValueError, TypeError):
                    continue
                if evt_crit != int(criticity):
                    continue

            # Event type filter
            if event_type and evt.get("event_type") != event_type:
                continue

            # Status filter
            if status is not None:
                try:
                    evt_status = int(evt.get("estado") or 0)
                except (ValueError, TypeError):
                    continue
                if evt_status != int(status):
                    continue

            filtered.append(evt)

        if limit and limit > 0:
            return filtered[:limit]
        return filtered

    # -- 3.6  Convenience: agents with modules (via events) -------------

    async def get_agent_module_ids(
        self, agent_id: int,
    ) -> list[int]:
        """Return module IDs for an agent, extracted from events.

        Since Community Edition has no ``agent_modules`` operation,
        we discover module IDs by looking at which ``id_agentmodule``
        values appear in events for this agent.

        This is necessarily incomplete — only modules that fired events
        will appear.
        """
        events = await self.get_events()
        module_ids: set[int] = set()
        for evt in events:
            if not isinstance(evt, dict):
                continue
            try:
                evt_agent = int(evt.get("id_agente") or 0)
                evt_module = int(evt.get("id_agentmodule") or 0)
            except (ValueError, TypeError):
                continue
            if evt_agent == int(agent_id) and evt_module > 0:
                module_ids.add(evt_module)
        return sorted(module_ids)

    async def discover_agent_modules(
        self, agent_id: int, scan_range: int = 8,
    ) -> list[dict]:
        """Discover all modules for an agent by scanning around known IDs.

        Modules are sequential in Pandora — scanning ±``scan_range``
        around a known ``id_agentmodule`` from events reveals all
        modules belonging to this agent.

        Each token from Pandora is ``timestamp(10) + value`` concatenated
        without delimiter, e.g. ``178201193295.27000`` → ts=1782011932, val=95.27.

        Args:
            agent_id: Pandora agent ID.
            scan_range: How many IDs to scan in each direction.

        Returns:
            List of module dicts with keys:
              - ``module_id`` (int)
              - ``data_points`` (list of dicts: ``{timestamp, value}``)
              - ``avg`` (float) — average value
              - ``max_val`` (float) — maximum value
        """
        known_ids = await self.get_agent_module_ids(agent_id)
        if not known_ids:
            return []

        # Modules are sequential; scan around the first known ID
        center = known_ids[0]
        start = max(1, center - scan_range)
        end = center + scan_range

        found: list[dict] = []
        for mid in range(start, end + 1):
            try:
                raw = await self._raw_module_data(mid)
            except PandoraAPIError:
                continue
            if not raw:
                continue
            points = _parse_module_data_tokens(raw)
            if not points:
                continue
            avg = sum(p["value"] for p in points) / len(points)
            max_val = max(p["value"] for p in points)
            found.append({
                "module_id": mid,
                "data_points": points,
                "count": len(points),
                "avg": avg,
                "max_val": max_val,
            })

        return found

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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()
        text = resp.text.strip()

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
