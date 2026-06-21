"""Debug script #2: deeper exploration of Pandora v7.0 NG operations."""
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
        # Test 1: all_agents with id_group as direct query param
        print("=== 1. all_agents + id_group= (direct param, no other_mode) ===")
        r = await c.get(url, params={**base, "op2": "all_agents", "id_group": ""})
        print(f"Status: {r.status_code}, Body[:500]: {r.text[:500]}")

        # Test 2: all_agents CSV with other_mode
        print("\n=== 2. all_agents CSV (other=, other_mode=url_encode_separator_|) ===")
        r = await c.get(url, params={
            **base,
            "op2": "all_agents",
            "other": "",
            "other_mode": "url_encode_separator_|",
        })
        print(f"Body[:2000]: {r.text[:2000]}")

        # Test 3: events with minimal other
        print("\n=== 3. events (other=;) ===")
        r = await c.get(url, params={**base, "op2": "events", "other": ";|||||||||||||"})
        print(f"Body[:500]: {r.text[:500]}")

        # Test 4: events with other_mode
        print("\n=== 4. events (other_mode + other pipe) ===")
        r = await c.get(url, params={
            **base,
            "op2": "events",
            "other": ";|-1||||1717200000|1719792000|-1||500|0||",
            "other_mode": "url_encode_separator_|",
        })
        print(f"Body[:500]: {r.text[:500]}")

        # Test 5: get_agent_modules for agent 21
        print("\n=== 5. get_agent_modules (id=21) ===")
        r = await c.get(url, params={**base, "op2": "get_agent_modules", "id": "21"})
        text = r.text[:1500]
        print(text)
        # Try to parse JSON
        try:
            data = r.json()
            if isinstance(data, dict):
                print(f"Keys: {sorted(data.keys())}")
                if "data" in data:
                    print(f"Module count: {len(data['data'])}")
        except Exception:
            print("[not JSON]")

        # Test 6: module_data for test
        print("\n=== 6. module_data (simple test) ===")
        r = await c.get(url, params={**base, "op2": "module_data", "id": "1"})
        print(f"Body[:500]: {r.text[:500]}")

        # Test 7: try get_groups_data as alias
        print("\n=== 7. get_groups_data ===")
        r = await c.get(url, params={**base, "op2": "get_groups_data"})
        print(f"Body[:300]: {r.text[:300]}")

asyncio.run(main())
