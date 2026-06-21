"""
Manual verification for pandora_client.py — Pandora v7.0 NG Community Edition.

Usage:
    cd /opt/pandora-monthly-report
    .venv/bin/python backend/test_pandora_client.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

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

    # ── Step 1: Test connection ─────────────────────────────────────
    print_section("1. Test connection (op2=test)")
    try:
        info = await client.test()
        print("  ✅ Connected —", json.dumps(info))
    except PandoraAuthError as e:
        print(f"  ❌ AUTH ERROR: {e}")
        return
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return

    # ── Step 2: List groups ─────────────────────────────────────────
    print_section("2. List groups (from events + module_groups)")
    try:
        groups = await client.get_groups()
        print_json(groups, max_items=15)
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        groups = []

    if not groups:
        print("  ⚠ No groups found — skipping further tests.")
        return

    # Pick first group
    first = groups[0]
    if not isinstance(first, dict):
        print(f"  ⚠ Bad group format: {first!r}")
        return
    first_group_id = first.get("id")
    first_group_name = first.get("name", "N/A")
    agent_count = first.get("agent_count", "?")
    print(f"\n  → Using group: '{first_group_name}' (id={first_group_id}, {agent_count} agents)")

    # ── Step 3: Agents ──────────────────────────────────────────────
    print_section(f"3. Agents (all, then filtered for group {first_group_id})")
    try:
        all_agents = await client.get_agents()
        print(f"  All agents: {len(all_agents)}")
    except Exception as e:
        print(f"  ❌ get_agents failed: {e}")
        all_agents = []

    try:
        group_agents = await client.get_agents_for_group(int(first_group_id))
        print_json(group_agents, max_items=5)
        agents = group_agents
    except Exception as e:
        print(f"  ❌ get_agents_for_group failed: {e}")
        agents = all_agents[:5]  # fallback: show first 5 agents

    if not agents:
        print("  ⚠ No agents in this group — skipping module/events tests.")
        return

    # ── Step 4: Module IDs from events ──────────────────────────────
    first_agent = agents[0]
    if not isinstance(first_agent, dict):
        return
    first_agent_id = first_agent.get("id_agente")
    first_agent_alias = first_agent.get("alias", "N/A")

    print_section(f"4. Module IDs for agent {first_agent_id} ({first_agent_alias})")
    try:
        mod_ids = await client.get_agent_module_ids(int(first_agent_id))
        print(f"  Module IDs from events: {mod_ids[:20]}")
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        mod_ids = []

    # ── Step 5: Module data ─────────────────────────────────────────
    if mod_ids:
        first_mod_id = mod_ids[0]
        print_section(f"5. Module data for module {first_mod_id}")
        try:
            data = await client.get_module_data(
                module_id=first_mod_id,
                date_start="2026-06-01",
                date_end="2026-06-21",
            )
            print_json(data, max_items=5)
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
    else:
        print_section("5. Module data — SKIPPED (no module IDs found)")

    # ── Step 6: Events ──────────────────────────────────────────────
    print_section(f"6. Events for group {first_group_id} (last 30 days)")
    try:
        events = await client.get_events(
            id_group=int(first_group_id),
            date_start="2026-06-01",
            date_end="2026-06-21",
            limit=20,
        )
        print_json(events, max_items=5)
    except Exception as e:
        print(f"  ❌ FAILED: {e}")

    print_section("Done — all tests completed")
    print("  Check results above. If data returned correctly, pandora_client.py is ready.")


if __name__ == "__main__":
    asyncio.run(main())
