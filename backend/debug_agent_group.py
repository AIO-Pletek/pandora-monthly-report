"""Debug: find agent->group mapping API."""
import asyncio, httpx, os
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
        # Try passing id_group as direct query param
        print("=== all_agents + id_group=36 as query param ===")
        r = await c.get(url, params={**base, "op2": "all_agents", "return_type": "json", "id_group": "36"})
        data = r.json()
        agents = data.get("data", []) if isinstance(data, dict) else []
        print(f"Agents in group 36: {len(agents)}")
        if agents:
            for a in agents[:3]:
                print(f"  {a.get('alias','?')}: {sorted(a.keys())}")

        # Try ALL variations of group filter parameter name
        print("\n=== all_agents with group filter params ===")
        for gparam in ["id_group", "id_grupo", "group", "group_id", "id_groupo"]:
            r = await c.get(url, params={**base, "op2": "all_agents", "return_type": "json", gparam: "36"})
            data = r.json()
            agents = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            n = len(agents)
            if n > 0 and n < 177:  # partial filter worked
                print(f"  {gparam}=36: {n} agents ← FILTER WORKS!")
            else:
                print(f"  {gparam}=36: {n} agents")

        # Try op2 that might list agents by group
        print("\n=== Agent-group operations ===")
        for op2 in ["group_agents", "get_group_agents", "agents_by_group", "agent_group_list"]:
            r = await c.get(url, params={**base, "op2": op2, "return_type": "json", "id_group": "36"})
            print(f"  {op2}: {r.text[:200]}")

        # Try using all_agents CSV with proper CSV separator
        print("\n=== all_agents CSV (properly delimited) ===")
        r = await c.get(url, params={
            **base, "op2": "all_agents", "return_type": "csv",
            "other": ",",  # just the CSV separator as first field
            "other_mode": "url_encode_separator_|",
        })
        lines = r.text.strip().split("\n")
        if lines:
            hdr = lines[0].split(";")
            print(f"Fields ({len(hdr)}): {hdr}")
            for ln in lines[1:3]:
                fields = ln.split(";")
                print(f"  {fields}")

        # Try with all_agents + other=||||||1 (recursion)
        print("\n=== all_agents CSV + other=||||||1 (recursion) ===")
        r = await c.get(url, params={
            **base, "op2": "all_agents", "return_type": "csv",
            "other": "||||||1",
            "other_mode": "url_encode_separator_|",
        })
        lines = r.text.strip().split("\n")
        if lines and lines[0]:
            hdr = lines[0].split(";")
            print(f"Fields ({len(hdr)}): {hdr}")
            # Show first row with indices
            if len(lines) > 1:
                fields = lines[1].split(";")
                for i, f in enumerate(fields):
                    print(f"  [{i}] {f[:60]}")

        # Most important: try filter all_agents by group using other
        print("\n=== all_agents CSV + id_group in other ===")
        # Format: id_os|id_group|module_state|alias|policy|separator|recursion
        for gid in [36, 14, 78]:
            r = await c.get(url, params={
                **base, "op2": "all_agents", "return_type": "csv",
                "other": f"|{gid}|||||",
                "other_mode": "url_encode_separator_|",
            })
            lines = r.text.strip().split("\n")
            if len(lines) >= 2:
                print(f"  id_group={gid}: {len(lines)-1} agents")
            else:
                print(f"  id_group={gid}: {r.text[:100]}")

asyncio.run(main())
