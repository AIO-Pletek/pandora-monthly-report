"""Debug: find how to list ALL agent groups in Pandora Community Ed."""
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
        # Try get_groups with CSV
        print("=== get_groups (csv) ===")
        r = await c.get(url, params={**base, "op2": "get_groups", "return_type": "csv"})
        print(r.text[:1000])

        print("\n=== group_list (csv) ===")
        r = await c.get(url, params={**base, "op2": "group_list", "return_type": "csv"})
        print(r.text[:1000])

        print("\n=== groups (csv) ===")
        r = await c.get(url, params={**base, "op2": "groups", "return_type": "csv"})
        print(r.text[:1000])

        print("\n=== get_groups (json) ===")
        r = await c.get(url, params={**base, "op2": "get_groups", "return_type": "json"})
        print(r.text[:1000])

        # Try all_agents with group filter=0 to see all group names
        print("\n=== all_agents CSV (header + first 5 rows) ===")
        r = await c.get(url, params={**base, "op2": "all_agents",
            "return_type": "csv", "other": "||||||0",
            "other_mode": "url_encode_separator_|"})
        lines = r.text.strip().split("\n")
        if len(lines) >= 2:
            hdr = lines[0].split(";")
            print(f"Header: {hdr}")
            for ln in lines[1:6]:
                print(ln[:300])

        # Extract ALL unique id_grupo from agent names/comentarios fields
        print("\n=== all_agents JSON (extract groups from agent fields) ===")
        r = await c.get(url, params={**base, "op2": "all_agents", "return_type": "json"})
        data = r.json()
        agents = data.get("data", []) if isinstance(data, dict) else []
        print(f"Total agents: {len(agents)}")
        print(f"Agent fields: {sorted(agents[0].keys()) if agents else 'none'}")

        # Check if any agent has id_grupo or group name in any field
        if agents:
            for a in agents[:5]:
                print(f"  {a}")

asyncio.run(main())
