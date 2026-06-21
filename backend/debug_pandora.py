"""Debug script: explore Pandora v7.0 NG available operations."""
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
        "return_type": "json",
    }

    async with httpx.AsyncClient(timeout=30) as c:
        # Test 1: all_agents JSON (no other_mode)
        print("=== 1. all_agents (JSON, no filter) ===")
        p1 = {**base, "op2": "all_agents"}
        r1 = await c.get(url, params=p1)
        data = r1.json()
        if isinstance(data, dict) and "data" in data:
            agents = data["data"]
        else:
            agents = data if isinstance(data, list) else []
        print(f"Agents: {len(agents)}")
        if agents:
            print(f"First agent keys: {sorted(agents[0].keys())}")
            # Show id_grupo if present
            for a in agents[:3]:
                gid = a.get("id_grupo", "MISSING")
                gname = a.get("grupo", "MISSING")
                print(f"  {a.get('alias','?')}: id_grupo={gid}, grupo={gname}")

        # Test 2: events
        print("\n=== 2. events (JSON, last 100) ===")
        p2 = {**base, "op2": "events", "other": ";"}
        r2 = await c.get(url, params=p2)
        print(r2.text[:500])

        # Test 3: module_groups
        print("\n=== 3. module_groups ===")
        p3 = {**base, "op2": "module_groups"}
        r3 = await c.get(url, params=p3)
        print(r3.text[:500])

        # Test 4: group_list
        print("\n=== 4. group_list ===")
        p4 = {**base, "op2": "group_list"}
        r4 = await c.get(url, params=p4)
        print(r4.text[:500])

        # Test 5: agent_group
        print("\n=== 5. agent_group ===")
        p5 = {**base, "op2": "agent_group"}
        r5 = await c.get(url, params=p5)
        print(r5.text[:500])

        # Test 6: get_agent_groups
        print("\n=== 6. get_agent_groups ===")
        p6 = {**base, "op2": "get_agent_groups"}
        r6 = await c.get(url, params=p6)
        print(r6.text[:500])


asyncio.run(main())
