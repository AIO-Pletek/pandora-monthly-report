"""
Pandora FMS REST API client — read-only wrapper module.

Wraps Pandora FMS External API (op=get) for the operations needed by the
monthly report generator. Every function that queries agents/events/modules
MUST accept an explicit id_group parameter and filter server-side.

Reference: Pandora FMS External API documentation
  https://pandorafms.com/manual/en/documentation/08_technical_reference/02_annex_externalapi

Parameter ordering in `other` is STRICT — wrong order silently returns empty data
instead of an error. Each function documents the pipe-separated position mapping.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30.0  # seconds
DATE_FMT_MODULE = "%Y%m%dT%H:%M"  # module_data: YYYYMMDDThh:mm
DATE_FMT_DISPLAY = "%Y-%m-%d"     # human-readable date input


# ── Exceptions ───────────────────────────────────────────────────────────────
class PandoraAPIError(Exception):
    """Generic Pandora API error (non-auth)."""


class PandoraAuthError(PandoraAPIError):
    """Raised when Pandora returns 'auth error' — credentials are wrong."""


# ── CSV/plain-text helpers ──────────────────────────────────────────────────

def _parse_pandora_csv(text: str, delimiter: str = ";") -> list[dict]:
    """Parse Pandora CSV response into list of dicts.

    Pandora returns CSV with first line as header and ``delimiter``-separated
    fields on subsequent lines. Lines are ``\\n``-separated.

    Returns empty list if text is empty or only contains a header row.
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
        row = {}
        for i, hdr in enumerate(headers):
            row[hdr] = values[i] if i < len(values) else ""
        rows.append(row)
    return rows


def _parse_test_response(text: str) -> dict:
    """Parse the special CSV response from ``op2=test``.

    Expected format: ``status,version,build``
    e.g. ``OK,v7.0NG.720,PC180320``
    """
    parts = text.strip().split(",")
    return {
        "status": parts[0] if len(parts) > 0 else "",
        "version": parts[1] if len(parts) > 1 else "",
        "build": parts[2] if len(parts) > 2 else "",
    }


# ── Helpers ──────────────────────────────────────────────────────────────────
def _to_module_date(date_str: str) -> str:
    """Convert human-readable date (YYYY-MM-DD) to module_data format.

    ``date_str`` can be:
      - ``"2025-06-01"`` → ``"20250601T00:00"``
      - ``"20250601T00:00"`` → returned as-is
    """
    date_str = date_str.strip()
    if "T" in date_str:
        return date_str  # already in API format
    dt = datetime.strptime(date_str, DATE_FMT_DISPLAY)
    return dt.strftime(DATE_FMT_MODULE)


def _to_unix(date_str: str, end_of_day: bool = False) -> int:
    """Convert human-readable date to Unix timestamp (int seconds).

    Args:
        date_str: ``"2025-06-01"`` or ``"2025-06-01T23:59"``
        end_of_day: if True and only a date is given, set time to 23:59:59.
    """
    date_str = date_str.strip()
    try:
        dt = datetime.strptime(date_str, DATE_FMT_MODULE)
    except ValueError:
        dt = datetime.strptime(date_str, DATE_FMT_DISPLAY)
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


# ── Client ───────────────────────────────────────────────────────────────────
class PandoraClient:
    """Async read-only client for Pandora FMS External API.

    Usage::

        client = PandoraClient(
            base_url=config.PANDORA_BASE_URL,
            api_user=config.PANDORA_API_USER,
            api_pass=config.PANDORA_API_USER_PASS,
            api_password=config.PANDORA_API_PASSWORD,
        )
        info = await client.test()
        groups = await client.get_groups()
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
    ) -> dict | list:
        """Issue a GET request to the Pandora API.

        All requests include ``op=get``, auth, and ``return_type=json``.

        Args:
            op2: Operation name (e.g. ``"test"``, ``"all_agents"``).
            extra_params: Additional query params merged into the request.
            id_: Value for the ``id`` param (agent id / module id etc.).
            timeout: Per-call override (seconds).

        Returns:
            Parsed JSON body — dict or list depending on the operation.

        Raises:
            PandoraAuthError: Auth rejected by Pandora.
            PandoraAPIError: Non-200 or JSON-decode failure after body check.
        """
        params: dict[str, Any] = {
            "op": "get",
            "op2": op2,
            "user": self.api_user,
            "pass": self.api_pass,
            "apipass": self.api_password,
            "return_type": "json",
        }
        if id_ is not None:
            params["id"] = id_
        if extra_params:
            params.update(extra_params)

        t = timeout if timeout is not None else self.timeout
        logger.debug("Pandora API call: op2=%s params=%s", op2, params)

        async with httpx.AsyncClient(timeout=t) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()

        text = resp.text.strip()

        # Pandora returns plain-string "auth error" on bad credentials.
        if text.lower() in ('"auth error"', "'auth error'", "auth error"):
            raise PandoraAuthError(
                "Pandora API returned 'auth error' — "
                "check PANDORA_API_USER, PANDORA_API_USER_PASS, "
                "and PANDORA_API_PASSWORD in your .env file."
            )

        # Some operations return empty string when no data exists.
        if not text:
            return []

        # Try JSON first.
        try:
            data = resp.json()
            # Pandora sometimes wraps JSON in a dict with "error" key.
            return data
        except json.JSONDecodeError:
            pass  # not JSON — try CSV

        # Pandora v7.0 NG often ignores return_type=json and returns CSV.
        # The CSV format varies by operation:
        #   - op2=test: single CSV line "status,version,build"
        #   - other op2: header row + data rows, semicolon-delimited
        if op2 == "test":
            logger.info("Pandora returned CSV for test — parsing manually")
            return _parse_test_response(text)

        # Attempt generic CSV parse (semicolon is Pandora's default delimiter).
        logger.warning(
            "Pandora returned non-JSON for op2=%s — parsing as CSV. "
            "First 200 chars: %s",
            op2, text[:200],
        )
        csv_data = _parse_pandora_csv(text, delimiter=";")
        if csv_data:
            return csv_data

        # If CSV parsing also failed, check if it's comma-separated CSV.
        if "," in text[:200]:
            csv_data_comma = _parse_pandora_csv(text, delimiter=",")
            if csv_data_comma:
                return csv_data_comma

        raise PandoraAPIError(
            f"Pandora returned unrecognized format for op2={op2}: "
            f"{text[:500]}"
        )

    # ── Public API wrappers ────────────────────────────────────────────

    # -- 3.1  Connection test -------------------------------------------

    async def test(self) -> dict:
        """Test connection to Pandora FMS and return server info.

        Uses ``op2=test``. Does NOT require an ``id`` or ``other`` param.

        Pandora v7.0 NG returns CSV ``OK,v7.0NG.720,PC180320`` (not JSON),
        so this method always returns a properly parsed dict with keys
        ``status``, ``version``, ``build``.
        """
        result = await self._call("test")
        if isinstance(result, list):
            return {"data": result}
        if isinstance(result, dict) and "version" not in result:
            # got some unexpected dict structure, wrap it
            return {"data": result}
        return result

    # -- 3.2  Groups (tenants) ------------------------------------------

    async def get_groups(self) -> list[dict]:
        """Return all groups configured in Pandora.

        Uses ``op2=get_groups`` — the standard operation for listing groups
        in Pandora FMS v7+. If your version does not support ``get_groups``,
        try :meth:`get_module_groups` instead.

        Each group dict typically contains ``id_grupo``, ``nombre``, etc.
        """
        result = await self._call("get_groups")
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return []

    async def get_module_groups(self) -> list[dict]:
        """Return all module groups (alternative group listing).

        Uses ``op2=module_groups``. Some Pandora versions expose group
        listings through this endpoint instead of ``get_groups``.
        """
        result = await self._call("module_groups")
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return []

    # -- 3.3  Agents by group (tenant) ----------------------------------

    async def get_agents_by_group(
        self,
        id_group: int,
        *,
        recursion: int = 0,
        id_os: str = "",
        module_state: str = "",
        alias_filter: str = "",
        policy_id: str = "",
    ) -> list[dict]:
        """Return all agents belonging to a specific group.

        Uses ``op2=all_agents`` with server-side group filter.

        ``other`` parameter format (pipe-separated, positions from docs):
          1. id_os          — OS filter (empty = all)
          2. id_group       — Group ID  ← **our primary filter**
          3. module_state   — Module state filter (empty = all)
          4. alias_filter   — Substring match on agent alias
          5. policy_id      — Policy ID filter
          6. csv_separator  — CSV field delimiter (irrelevant for JSON)
          7. recursion      — 1 = include children of subgroups, 0 = exact

        Args:
            id_group: Pandora group ID (tenant).
            recursion: 0 = only agents directly in this group;
                       1 = include agents in child groups recursively.
            id_os: Optional OS filter (e.g. ``"1"`` for Linux).
            module_state: Filter by worst module state
                          (``"critical"``, ``"warning"``, ``"unknown"``, ``"no_modules"``).
            alias_filter: Substring to match against agent alias.
            policy_id: Filter by policy ID.

        Returns:
            List of agent dicts. Each dict typically contains
            ``id_agente``, ``alias``, ``nombre``, ``id_grupo``, etc.
        """
        # Build other:  id_os | id_group | module_state | alias | policy | separator | recursion
        other = "|".join(
            [
                str(id_os),
                str(id_group),
                str(module_state),
                str(alias_filter),
                str(policy_id),
                "",            # csv_separator — not needed for JSON
                str(recursion),
            ]
        )
        logger.info("Fetching agents for group id=%s", id_group)
        result = await self._call(
            "all_agents",
            extra_params={
                "other": other,
                "other_mode": "url_encode_separator_|",
            },
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return []

    # -- 3.4  Agent modules ---------------------------------------------

    async def get_agent_modules(self, agent_id: int) -> list[dict]:
        """Return all modules belonging to a specific agent.

        Uses ``op2=get_agent_modules&id=<agent_id>``.
        No ``other`` parameter is needed.

        Each module dict typically contains ``id_agente_modulo``, ``nombre``,
        ``descripcion``, ``id_tipo_modulo``, etc.

        This is essential for discovering which module IDs to pass to
        :meth:`get_module_data` for CPU, RAM, disk, Host Alive, etc.
        """
        logger.debug("Fetching modules for agent id=%s", agent_id)
        result = await self._call("get_agent_modules", id_=str(agent_id))
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return []

    # -- 3.5  Module historical data ------------------------------------

    async def get_module_data(
        self,
        module_id: int,
        date_start: str,
        date_end: str,
        *,
        period: int = 0,
        csv_separator: str = ";",
        use_agent_alias: int = 0,
    ) -> list[dict]:
        """Return historical data points for a single module.

        Uses ``op2=module_data&id=<module_id>``.

        ``other`` parameter format (pipe-separated, positions from docs):
          1. csv_separator     — Field delimiter for CSV (default ``;``)
          2. period            — Time period in seconds
                                 (0 = use explicit date range below)
          3. date_from         — ``YYYYMMDDThh:mm``
          4. date_to           — ``YYYYMMDDThh:mm``
          5. use_agent_alias   — 0 = use agent name, 1 = use alias

        Args:
            module_id: Pandora module ID.
            date_start: Start date — ``"YYYY-MM-DD"`` or ``"YYYYMMDDThh:mm"``.
            date_end: End date — same formats as date_start.
            period: Period in seconds; if > 0, date_start/date_end are ignored
                    by the API (set them to empty strings if using period).
            csv_separator: Field delimiter (only matters for CSV; harmless for JSON).
            use_agent_alias: 1 to label data with agent alias instead of name.

        Returns:
            List of data-point dicts. Each typically contains
            ``utimestamp``, ``datos`` (value), ``nombre``, etc.
            Returns empty list if no data exists in the range.
        """
        date_from = _to_module_date(date_start)
        date_to = _to_module_date(date_end)

        other = "|".join(
            [
                csv_separator,
                str(period),
                date_from,
                date_to,
                str(use_agent_alias),
            ]
        )
        logger.debug(
            "Fetching module_data id=%s from %s to %s",
            module_id, date_from, date_to,
        )
        result = await self._call(
            "module_data",
            extra_params={
                "other": other,
                "other_mode": "url_encode_separator_|",
            },
            id_=str(module_id),
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return []

    # -- 3.6  Events / alerts by group ----------------------------------

    async def get_events_by_group(
        self,
        id_group: int,
        date_start: str,
        date_end: str,
        *,
        criticity: int = -1,
        status: int = -1,
        event_type: str = "",
        tags: str = "",
        agent_alias: str = "",
        module_name: str = "",
        event_substring: str = "",
        limit: int = 500,
        offset: int = 0,
        optional_style: str = "",
    ) -> list[dict]:
        """Return events/alerts for a group in a date range.

        Uses ``op2=events`` with server-side group + date filter.

        ``other`` parameter format (pipe-separated, 16 positions):
          1.  csv_separator        — Field delimiter (``;``)
          2.  criticity            — Severity: -1=all, 0=info, 1=low, 2=medium, 3=warning, 4=critical
          3.  agent_alias          — Filter by agent alias substring
          4.  module_name          — Filter by module name substring
          5.  alert_template_name  — Filter by alert template
          6.  user                 — Filter by user
          7.  min_interval         — Start Unix timestamp
          8.  max_interval         — End Unix timestamp
          9.  status               — Event status: -1=all, 0=new, 1=validated, 2=in progress
          10. event_substring      — Text search within event description
          11. limit                — Max records to return
          12. offset               — Pagination offset
          13. optional_style       — ``"total"`` for count only, ``"more_criticity"`` for max severity
          14. td_grupo (id_group)  — **Group ID filter**
          15. tags                 — Filter by tags
          16. event_type           — ``"alert_fired"``, ``"alert_recovered"``,
                                      ``"unknown"``, ``"not_normal"``, or empty for all

        Args:
            id_group: Pandora group ID (tenant).
            date_start: Start date (``"YYYY-MM-DD"`` or ``"YYYYMMDDThh:mm"``).
            date_end: End date (same formats).
            criticity: Severity filter (-1 = all).
            status: Event status filter (-1 = all).
            event_type: Type filter (empty = all).
            tags: Tag filter (empty = all).
            agent_alias: Agent alias substring filter.
            module_name: Module name substring filter.
            event_substring: Text search in event description.
            limit: Max events to return.
            offset: Pagination offset.
            optional_style: ``"total"`` to get count instead of records.

        Returns:
            List of event dicts. Each typically contains
            ``id_evento``, ``criticidad``, ``nombre``, ``utimestamp``, etc.
        """
        ts_start = _to_unix(date_start, end_of_day=False)
        ts_end = _to_unix(date_end, end_of_day=True)

        other = "|".join(
            [
                ";",                        # 1. csv_separator
                str(criticity),             # 2. criticity
                str(agent_alias),           # 3. agent alias
                str(module_name),           # 4. module name
                "",                         # 5. alert template name (unused)
                "",                         # 6. user (unused)
                str(ts_start),              # 7. min_interval (unix ts)
                str(ts_end),                # 8. max_interval (unix ts)
                str(status),                # 9. status
                str(event_substring),       # 10. event substring
                str(limit),                 # 11. register limit
                str(offset),                # 12. offset
                str(optional_style),        # 13. optional style
                str(id_group),              # 14. td_grupo (GROUP FILTER)
                str(tags),                  # 15. tags
                str(event_type),            # 16. event type
            ]
        )
        logger.info(
            "Fetching events for group id=%s range %s..%s",
            id_group, ts_start, ts_end,
        )
        result = await self._call(
            "events",
            extra_params={
                "other": other,
                "other_mode": "url_encode_separator_|",
            },
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return []

    # -- 3.7  Convenience: get all agent modules for a group ------------

    async def get_agents_with_modules(
        self, id_group: int, *, recursion: int = 0
    ) -> list[dict]:
        """Return agents in a group, each enriched with its module list.

        Convenience method that combines :meth:`get_agents_by_group` and
        :meth:`get_agent_modules` into one call tree.

        Args:
            id_group: Pandora group ID.
            recursion: Passed through to :meth:`get_agents_by_group`.

        Returns:
            List of agent dicts, each with an added ``"modules"`` key
            containing the result of :meth:`get_agent_modules`.
        """
        agents = await self.get_agents_by_group(id_group, recursion=recursion)
        for agent in agents:
            agent_id = agent.get("id_agente")
            if agent_id:
                agent["modules"] = await self.get_agent_modules(int(agent_id))
            else:
                agent["modules"] = []
        return agents
