"""
Manual verification script for pandora_client.py.

Run this against the real Pandora FMS instance to verify:
  1. Connection (op2=test)
  2. Group / tenant listing (get_groups / module_groups)
  3. Agents in a group (all_agents)
  4. Agent modules (get_agent_modules)
  5. Module historical data (module_data)
  6. Events in a group + date range (events)

Usage:
    cd backend
    python test_pandora_client.py

All credentials are read from environment variables via config.py.
Make sure .env exists and is filled in before running this.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass  # dotenv is optional; user can set env vars directly

from config import (
    PANDORA_API_PASSWORD,
    PANDORA_API_USER,
    PANDORA_API_USER_PASS,
    PANDORA_BASE_URL,
)
from pandora_client import PandoraAuthError, PandoraClient


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_json(obj, max_items: int = 5) -> None:
    """Pretty-print a list/dict, truncating long lists."""
    if isinstance(obj, list):
        print(f"  Count: {len(obj)}")
        for item in obj[:max_items]:
            print(f"  - {json.dumps(item, indent=4, default=str)[:300]}")
        if len(obj) > max_items:
            print(f"  ... and {len(obj) - max_items} more items")
    elif isinstance(obj, dict):
        print(json.dumps(obj, indent=2, default=str)[:2000])
    else:
        print(f"  {obj}")


async def main() -> None:
    client = PandoraClient(
        base_url=PANDORA_BASE_URL,
        api_user=PANDORA_API_USER,
        api_pass=PANDORA_API_USER_PASS,
        api_password=PANDORA_API_PASSWORD,
    )

    # ── Step 1: Test connection ───────────────────────────────────────
    print_section("1. Testing connection (op2=test)")
    try:
        info = await client.test()
        print("  ✅ Connection OK — server info:")
        print_json(info)
    except PandoraAuthError as e:
        print(f"  ❌ AUTH ERROR: {e}")
        return
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return

    # ── Step 2: List groups / tenants ─────────────────────────────────
    print_section("2. Listing groups (op2=get_groups)")
    try:
        groups = await client.get_groups()
        print_json(groups, max_items=10)
        if not groups:
            print("  (empty — trying module_groups as fallback...)")
            groups = await client.get_module_groups()
            print_json(groups, max_items=10)
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        print("  (trying module_groups as fallback...)")
        try:
            groups = await client.get_module_groups()
            print_json(groups, max_items=10)
        except Exception as e2:
            print(f"  ❌ ALSO FAILED: {e2}")
            groups = []

    # Pick first group for further tests
    first_group_id = None
    first_group_name = "N/A"
    if groups:
        first = groups[0]
        if not isinstance(first, dict):
            print(f"  ⚠ Unexpected group format (not dict): {first!r}")
            print("  Skipping agent/event tests.")
            return
        # Pandora uses different key names across versions.
        # Fallback extraction may return {"id", "name"} format.
        first_group_id = (
            first.get("id_grupo")
            or first.get("id_group")
            or first.get("id")
        )
        first_group_name = (
            first.get("nombre")
            or first.get("name")
            or first.get("group_name")
            or "N/A"
        )
        print(f"\n  → Will use group id={first_group_id} ('{first_group_name}') for next tests")

    if first_group_id is None:
        print("\n  ⚠ No groups found — skipping agent/event tests.")
        return

    # ── Step 3: Agents in group ───────────────────────────────────────
    print_section(f"3. Agents in group {first_group_id} (op2=all_agents)")
    try:
        agents = await client.get_agents_by_group(int(first_group_id))
        print_json(agents, max_items=10)
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        agents = []

    # ── Step 4: Modules for first agent ───────────────────────────────
    if agents:
        first_agent = agents[0]
        if not isinstance(first_agent, dict):
            print(f"  ⚠ Unexpected agent format (not dict): {first_agent!r}")
            print("  Skipping module/event tests.")
            return
        first_agent_id = first_agent.get("id_agente")
        first_agent_name = first_agent.get("alias") or first_agent.get("nombre", "N/A")
        print_section(
            f"4. Modules for agent {first_agent_id} ('{first_agent_name}') "
            f"(op2=get_agent_modules)"
        )
        try:
            modules = await client.get_agent_modules(int(first_agent_id))
            print_json(modules, max_items=20)
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            modules = []
    else:
        modules = []

    # ── Step 5: Module data for first module ──────────────────────────
    first_mod_id = None
    first_mod_name = "N/A"
    if modules:
        first_mod = modules[0]
        if not isinstance(first_mod, dict):
            print(f"  ⚠ Unexpected module format (not dict): {first_mod!r}")
        else:
            first_mod_id = first_mod.get("id_agente_modulo")
            first_mod_name = first_mod.get("nombre", "N/A")
    if first_mod_id:
        print_section(
            f"5. Data for module {first_mod_id} ('{first_mod_name}') "
            f"last 7 days (op2=module_data)"
        )
        try:
            data = await client.get_module_data(
                module_id=int(first_mod_id),
                date_start="2025-06-01",
                date_end="2025-06-21",
            )
            print_json(data, max_items=10)
        except Exception as e:
            print(f"  ❌ FAILED: {e}")

    # ── Step 6: Events for group ──────────────────────────────────────
    print_section(
        f"6. Events for group {first_group_id} "
        f"last 30 days (op2=events)"
    )
    try:
        events = await client.get_events_by_group(
            id_group=int(first_group_id),
            date_start="2025-06-01",
            date_end="2025-06-21",
            limit=20,
        )
        print_json(events, max_items=10)
    except Exception as e:
        print(f"  ❌ FAILED: {e}")

    print_section("Done — all tests completed")
    print("  If data returned correctly for all steps, pandora_client.py is ready.")
    print("  If any step failed, check:")
    print("    - Are the credentials correct in .env?")
    print("    - Does this Pandora version use a different op2 name for groups?")
    print("    - Is the 'other' parameter order correct for your version?")


if __name__ == "__main__":
    asyncio.run(main())
