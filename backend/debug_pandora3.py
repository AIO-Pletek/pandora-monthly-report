"""Debug script #3: find correct op2 names + events format."""
import asyncio
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def try_op(url, base, label, params_override):
    """Try one API call and print brief result."""
    p = {**base, **params_override}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url, params=p)
            text = r.text[:300]
            if text.startswith('{"type":"string"'):
                print(f"  {label}: NOT EXISTS")
            elif text.startswith('{"type":"array"'):
                try:
                    j = r.json()
                    count = len(j.get("data", []))
                    print(f"  {label}: OK ({count} items)")
                except Exception:
                    print(f"  {label}: {text}")
            else:
                print(f"  {label}: {text}")
    except Exception as e:
        print(f"  {label}: ERROR - {e}")


async def main():
    url = os.getenv("PANDORA_BASE_URL").rstrip("/") + "/include/api.php"
    base = {
        "op": "get",
        "user": os.getenv("PANDORA_API_USER"),
        "pass": os.getenv("PANDORA_API_USER_PASS"),
        "apipass": os.getenv("PANDORA_API_PASSWORD"),
        "return_type": "json",
    }

    print("=== FINDING AGENT MODULES OP2 ===")
    for op2 in [
        "agent_module",
        "modules",
        "get_modules",
        "agent_modules",
        "get_modules_by_agent",
        "module_list",
        "get_agent_module_data",
        "module",
    ]:
        await try_op(url, base, op2, {"op2": op2, "id": "21"})

    print("\n=== FINDING EVENTS OP2 VARIANTS ===")
    for op2 in ["events", "get_events", "event", "event_list", "alerts", "get_alerts"]:
        await try_op(url, base, op2, {"op2": op2})

    print("\n=== EVENTS WITH PARAMS ===")
    # Try events with various param combos
    await try_op(url, base, "events (no filter)", {"op2": "events"})
    await try_op(url, base, "events (other=;)", {"op2": "events", "other": ";"})
    await try_op(url, base, "events (other=;|4)", {"op2": "events", "other": ";|4"})
    await try_op(url, base, "events (other=;|-1)", {"op2": "events", "other": ";|-1"})
    await try_op(url, base, "events (return_type=csv)", {"op2": "events", "return_type": "csv"})
    await try_op(url, base, "events CSV+other", {
        "op2": "events", "return_type": "csv",
        "other": ";||||||||||20||",
        "other_mode": "url_encode_separator_|",
    })

    print("\n=== CHECK IF AGENTS HAVE GROUP VIA DIFFERENT FIELD ===")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={**base, "op2": "all_agents"})
        j = r.json()
        agents = j.get("data", [])
        if agents:
            print(f"All fields on first agent: {sorted(agents[0].keys())}")
            print(f"First agent full: {agents[0]}")

    print("\n=== TRY PANDEIRA GROUP / TAG FIELD ===")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={**base, "op2": "all_agents", "return_type": "csv"})
        lines = r.text.strip().split("\n")
        if len(lines) >= 2:
            print(f"CSV header: {lines[0]}")
            print(f"First row: {lines[1]}")

asyncio.run(main())
