"""Debug: discover how to list modules per agent in Pandora Community Ed.

Run on VPS:
    cd /opt/pandora-monthly-report && git pull
    .venv/bin/python backend/debug_modules.py
"""

import asyncio
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def main():
    url = os.getenv("PANDORA_BASE_URL").rstrip("/") + "/include/api.php"
    base = {
        "op": "get",
        "user": os.getenv("PANDORA_API_USER"),
        "pass": os.getenv("PANDORA_API_USER_PASS"),
        "apipass": os.getenv("PANDORA_API_PASSWORD"),
    }

    async with httpx.AsyncClient(timeout=30) as c:

        # ── 1. Try ALL op2 names for agent modules ──────────────────
        print("=== 1. Agent module operations (op2 variants) ===")
        agent_id = "453"  # ACA_SUBSYSTEM_P (from events we know it has module 8935)
        for op2 in [
            "get_agent_modules", "agent_modules", "agent_module",
            "get_modules_agent", "get_agent_module",
            "module_list", "get_modules", "modules",
        ]:
            for param in [{}, {"id": agent_id}, {"id_agent": agent_id},
                          {"id_agente": agent_id}, {"agent_id": agent_id}]:
                p = {**base, "op2": op2, "return_type": "json", **param}
                try:
                    r = await c.get(url, params=p)
                    text = r.text[:200].replace("\n", " ")
                    ok = "HIT" if (
                        '"type":"array"' in text and '"data":""' not in text
                    ) or (text.startswith("[") and len(text) > 5) else "..."
                    if op2 == "agent_modules":
                        print(f"  {op2:25s} {str(param):35s} => {ok} {text[:150]}")
                except Exception as e:
                    pass

        # ── 2. Try module_data with module IDs from events ──────────
        print("\n=== 2. module_data with module IDs from events ===")
        # First get events to collect module IDs
        r = await c.get(url, params={**base, "op2": "events", "return_type": "json"})
        events = []
        try:
            j = r.json()
            events = j.get("data", []) if isinstance(j, dict) else (j if isinstance(j, list) else [])
        except Exception:
            pass

        # Collect unique module IDs from events
        mod_ids = set()
        for evt in events:
            mid = evt.get("id_agentmodule")
            aid = evt.get("id_agente")
            if mid and aid:
                mod_ids.add((int(aid), int(mid)))

        print(f"  Unique agent-module pairs from events: {len(mod_ids)}")
        for aid, mid in sorted(mod_ids)[:10]:
            print(f"    agent={aid} module={mid}")

        # Test module_data for a few IDs
        print("\n  Testing module_data for sample IDs:")
        for mid in [8935, 18714, 22230, 4473, 1, 2, 100, 500, 1000]:
            r = await c.get(url, params={
                **base, "op2": "module_data", "id": str(mid), "return_type": "json",
            })
            text = r.text[:200].replace("\n", " ")
            print(f"    module_id={mid:5d} => {text}")

        # ── 3. Try agent_modules CSV with various formats ───────────
        print("\n=== 3. agent_modules CSV variants ===")
        for fmt in [
            {"id": agent_id, "return_type": "csv"},
            {"id_agent": agent_id, "return_type": "csv"},
            {"id_agente": agent_id, "return_type": "csv"},
        ]:
            p = {**base, "op2": "agent_modules", **fmt}
            r = await c.get(url, params=p)
            lines = r.text.strip().split("\n")
            nlines = len(lines)
            first = lines[0][:200] if lines else "(empty)"
            print(f"  {fmt} => {nlines} lines, first: {first}")

        # ── 4. Find module IDs for agent 453 (ACA_SUBSYSTEM_P) ──────
        print(f"\n=== 4. Module IDs for agent {agent_id} from events ===")
        agent_mods = [(mid, evt.get("module_name","?"))
                      for evt in events
                      if str(evt.get("id_agente")) == agent_id
                      and (mid := evt.get("id_agentmodule"))]
        for mid, mname in agent_mods[:10]:
            print(f"    module_id={mid} name={mname}")

        # ── 5. Try scanning module IDs near known values ────────────
        print("\n=== 5. Scanning module IDs near known values ===")
        # Check if module IDs are sequential per agent
        for mid in [8930, 8931, 8932, 8933, 8934, 8935, 8936, 8937, 8938, 8939, 8940]:
            r = await c.get(url, params={
                **base, "op2": "module_data", "id": str(mid), "return_type": "json",
            })
            text = r.text[:150].replace("\n", " ")
            if "No data" not in text and "error" not in text.lower() and text.strip():
                print(f"    module_id={mid} HAS DATA: {text}")
            else:
                print(f"    module_id={mid} => {text[:80]}")


asyncio.run(main())
