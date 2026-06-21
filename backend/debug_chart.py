"""Debug chart data parsing and date filtering."""

import asyncio, httpx, os, sys
from datetime import datetime
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
        # Get events
        r = await c.get(url, params={**base, "op2": "events", "return_type": "json"})
        events = r.json().get("data", [])

    # Find first event with agent+module
    for evt in events[:10]:
        aid = evt.get("id_agente")
        mid = evt.get("id_agentmodule")
        aname = evt.get("agent_name", "?")
        mname = evt.get("module_name", "?")
        if not aid or not mid:
            continue

        # Get raw module data
        async with httpx.AsyncClient(timeout=30) as c:
            r2 = await c.get(url, params={
                **base, "op2": "module_data", "id": str(mid), "return_type": "json",
            })
        raw = r2.text.strip()[:600]

        print(f"Agent={aid} ({aname}) Module={mid} ({mname})")
        print(f"RAW[:600]: {raw}")
        print()

        # Parse first and last token
        tokens = raw.split()
        if tokens:
            first = tokens[0]
            last = tokens[-1]
            ts_first = int(first[:10])
            ts_last = int(last[:10])
            val_first = first[10:]
            val_last = last[10:]
            print(f"Token count: {len(tokens)}")
            print(f"First: ts={ts_first} -> {datetime.fromtimestamp(ts_first)}, val={val_first}")
            print(f"Last:  ts={ts_last} -> {datetime.fromtimestamp(ts_last)}, val={val_last}")

            # Test date filter
            date_start = "2026-06-01"
            date_end = "2026-06-30"
            ts_start = int(datetime.strptime(date_start, "%Y-%m-%d").timestamp())
            ts_end = int(datetime.strptime(date_end, "%Y-%m-%d").replace(hour=23, minute=59, second=59).timestamp())
            print(f"\nFilter: {date_start} ({ts_start}) to {date_end} ({ts_end})")
            print(f"ts_first ({ts_first}) >= ts_start ({ts_start})? {ts_first >= ts_start}")
            print(f"ts_first ({ts_first}) <= ts_end ({ts_end})? {ts_first <= ts_end}")
            print(f"ts_last ({ts_last}) <= ts_end ({ts_end})? {ts_last <= ts_end}")

            # Count how many pass filter
            in_range = 0
            for tok in tokens:
                tok_ts = int(tok[:10])
                if ts_start <= tok_ts <= ts_end:
                    in_range += 1
            print(f"Tokens in range: {in_range} / {len(tokens)}")
        break


asyncio.run(main())
