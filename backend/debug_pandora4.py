"""Debug #4: find agent modules op2 + module_data + events structure."""
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
        # ── 1. agent_modules CSV ──────────────────────────────
        print("=== 1. agent_modules (CSV) ===")
        r = await c.get(url, params={**base, "op2": "agent_modules", "return_type": "csv", "id_agent": "21"})
        print(f"Body[:800]: {r.text[:800]}")
        print()

        # Also try with 'id' param
        print("=== 2. agent_modules (CSV, id=21) ===")
        r = await c.get(url, params={**base, "op2": "agent_modules", "return_type": "csv", "id": "21"})
        print(f"Body[:800]: {r.text[:800]}")
        print()

        # ── 3. events CSV header ──────────────────────────────
        print("=== 3. events CSV (to see header) ===")
        r = await c.get(url, params={
            **base, "op2": "events", "return_type": "csv",
            "other": ";|1388534400|1719792000",
            "other_mode": "url_encode_separator_|",
        })
        if r.text.strip():
            lines = r.text.strip().split("\n")
            print(f"Header ({len(lines[0].split(';'))} fields):")
            for i, h in enumerate(lines[0].split(";")):
                print(f"  [{i}] {h}")
            if len(lines) > 1:
                print(f"First row: {lines[1][:500]}")
        else:
            print("Empty response")
        print()

        # ── 4. Try events JSON with only period params ──────
        print("=== 4. events JSON + other_mode + other period ===")
        r = await c.get(url, params={
            **base, "op2": "events", "return_type": "json",
            "other": "1388534400|1719792000",
            "other_mode": "url_encode_separator_|",
        })
        print(f"Body[:500]: {r.text[:500]}")
        print()

        # ── 5. module_data CSV ───────────────────────────────
        # We need to find a valid module_id first
        r = await c.get(url, params={
            **base, "op2": "agent_modules", "return_type": "csv", "id": "21",
        })
        print(f"=== 5. agent_modules (id=21, CSV, first line) ===")
        lines = r.text.strip().split("\n")
        num_fields = len(lines[0].split(";"))
        print(f"Header has {num_fields} fields: {lines[0]}")
        if len(lines) > 1:
            print(f"First data row: {lines[1]}")

        # ── 6. Try agent_module (singular) ──────────────────
        print("\n=== 6. agent_module operations ===")
        for op2 in ["agent_module", "get_agent_module", "module_by_agent"]:
            r = await c.get(url, params={**base, "op2": op2, "return_type": "json", "id": "21"})
            print(f"  {op2}: {r.text[:200]}")

asyncio.run(main())
