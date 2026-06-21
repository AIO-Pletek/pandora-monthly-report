"""Debug #5: final discovery — modules + events filter + module_data."""
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
        # ── 1. Try all param names for agent_modules ────────
        print("=== 1. agent_modules param variations ===")
        for param in [("id", "21"), ("id_agent", "21"), ("id_agente", "21"),
                       ("id_agent", "21"), ("agent_id", "21")]:
            r = await c.get(url, params={
                **base, "op2": "agent_modules", "return_type": "json",
                param[0]: param[1],
            })
            info = r.text[:200]
            if '"data":' in info and '"data":""' not in info:
                print(f"  {param}: HIT! {info[:300]}")
            else:
                print(f"  {param}: {info[:150]}")

        # ── 2. Maybe modules are in all_agents with extra param ──
        print("\n=== 2. all_agents with extra params ===")
        for opts in [
            {"modules": "1"},
            {"get_modules": "1"},
            {"show_modules": "1"},
        ]:
            r = await c.get(url, params={
                **base, "op2": "all_agents", "return_type": "json", **opts,
            })
            text = r.text[:500]
            print(f"  {opts}: {text[:200]}")

        # ── 3. Events CSV to see header ──────────────────────
        print("\n=== 3. events CSV (return_type=csv, no params) ===")
        r = await c.get(url, params={
            **base, "op2": "events", "return_type": "csv",
        })
        lines = r.text.strip().split("\n")
        print(f"Total lines: {len(lines)}")
        if lines:
            # Check if first line is header or data
            first = lines[0]
            nfields = len(first.split(";"))
            print(f"Line 1 ({nfields} fields): {first[:400]}")
        if len(lines) > 1:
            second = lines[1]
            nfields2 = len(second.split(";"))
            print(f"Line 2 ({nfields2} fields): {second[:400]}")

        # ── 4. Events JSON structure ─────────────────────────
        print("\n=== 4. events JSON (first 1 item) ===")
        r = await c.get(url, params={
            **base, "op2": "events", "return_type": "json",
        })
        try:
            data = r.json()
            items = data.get("data", [])
            if isinstance(items, list) and items:
                print(f"Total: {len(items)} events")
                print(f"Keys: {sorted(items[0].keys())}")
                print(f"Sample: {items[0]}")
                # Extract a valid module ID
                mid = items[0].get("id_agente_modulo")
                print(f"Sample module_id: {mid}")
        except Exception as e:
            print(f"Parse error: {e}")
            print(r.text[:500])

        # ── 5. Module data with real module ID ───────────────
        print("\n=== 5. module_data with real module ID ===")
        r1 = await c.get(url, params={
            **base, "op2": "events", "return_type": "json",
        })
        try:
            data1 = r1.json()
            items = data1.get("data", [])
            if isinstance(items, list) and items:
                mid = items[0].get("id_agente_modulo")
                agent_id = items[0].get("id_agente")
                if mid:
                    r2 = await c.get(url, params={
                        **base, "op2": "module_data",
                        "id": str(mid),
                        "return_type": "json",
                    })
                    print(f"module_data id={mid}: {r2.text[:300]}")
                    # Also try CSV
                    r3 = await c.get(url, params={
                        **base, "op2": "module_data",
                        "id": str(mid),
                        "return_type": "csv",
                        "other": "0|1717200000|1719792000",
                        "other_mode": "url_encode_separator_|",
                    })
                    print(f"module_data CSV id={mid}: {r3.text[:300]}")
        except Exception as e:
            print(f"Error: {e}")

asyncio.run(main())
