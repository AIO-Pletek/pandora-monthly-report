"""Debug #2: try ALL param combinations for agent_modules."""
import asyncio, httpx, os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def main():
    url = os.getenv("PANDORA_BASE_URL").rstrip("/") + "/include/api.php"
    base = {
        "op": "get", "user": os.getenv("PANDORA_API_USER"),
        "pass": os.getenv("PANDORA_API_USER_PASS"),
        "apipass": os.getenv("PANDORA_API_PASSWORD"),
    }

    # Get one agent's full details
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={**base, "op2": "all_agents", "return_type": "json"})
        agents = r.json().get("data", [])
    agent = agents[0]  # agent 21 (Router VPN Mikrotik)
    a_id = agent["id_agente"]
    a_nombre = agent["nombre"]
    a_alias = agent["alias"]
    print(f"Test agent: id={a_id}, alias={a_alias}, nombre={a_nombre}")

    # Also get agent 453 (ACA_SUBSYSTEM_P - we know modules 8932-8937)
    for a in agents:
        if a.get("id_agente") == "453":
            a453 = a
            break

    async with httpx.AsyncClient(timeout=30) as c:
        print("\n=== 1. agent_modules with different ID formats ===")
        for id_field, id_val in [
            ("id", a_id),
            ("id_agent", a_id),
            ("id_agente", a_id),
            ("id_agentmodule", a_id),
            ("id_agent_module", a_id),
            ("agent", a_id),
            ("agent_id", a_id),
            # Using nombre hash
            ("id", a_nombre),
            ("id_agent", a_nombre),
            ("id_agente", a_nombre),
            # Using alias
            ("id", a_alias),
            ("id_agent", a_alias),
        ]:
            for fmt in ["json", "csv"]:
                p = {**base, "op2": "agent_modules", "return_type": fmt, id_field: id_val}
                if fmt == "csv":
                    p["other_mode"] = "url_encode_separator_|"
                r = await c.get(url, params=p)
                text = r.text[:150].replace("\n", " ")
                ok = "✓" if ("data" in text and '"data":""' not in text and "No modules" not in text) \
                     or (text.startswith("[") and len(text) > 10) \
                     or (text.count(";") > 3) else "..."
                if ok == "✓":
                    print(f"  {ok} {id_field}={id_val[:20]} ({fmt}): {text}")
                # Just print ones that work

        print("\n=== 2. agent_modules with other param ===")
        for other_val in ["", "|", f"|{a_id}|", f"|{a_nombre}|"]:
            r = await c.get(url, params={
                **base, "op2": "agent_modules", "return_type": "csv",
                "other": other_val, "other_mode": "url_encode_separator_|",
            })
            text = r.text[:200].replace("\n", " ")
            print(f"  other='{other_val}': {text}")

        print("\n=== 3. Alternative op2 names ===")
        for op2 in [
            "list_modules", "get_modules_by_agent", "agent_module_list",
            "get_agent_modules_list", "modules_by_agent",
            "get_agent_data", "agent_data",
            "module", "get_module", "module_agent",
        ]:
            for idf in ["id", "id_agent", "id_agente"]:
                r = await c.get(url, params={**base, "op2": op2, "return_type": "json", idf: a_id})
                text = r.text[:100]
                if "does not exist" not in text.lower() and "no modules" not in text.lower() and text not in ('""', "[]", ""):
                    print(f"  {op2}?{idf}={a_id}: {text}")

        # Deep try: maybe we need ALL agents' module data in one call?
        print("\n=== 4. Bulk module operations ===")
        r = await c.get(url, params={**base, "op2": "module_data", "return_type": "json"})
        print(f"  module_data (no id): {r.text[:200]}")
        r = await c.get(url, params={**base, "op2": "module_list", "return_type": "json"})
        print(f"  module_list: {r.text[:200]}")

        print("\n=== 5. Agent 453 + agent_modules with different IDs ===")
        if a453:
            for idf, idv in [
                ("id", a453["id_agente"]),
                ("id_agent", a453["id_agente"]),
                ("id_agente", a453["id_agente"]),
                ("id", a453["nombre"]),
                ("id_agent", a453["nombre"]),
            ]:
                for fmt in ["json", "csv"]:
                    r = await c.get(url, params={**base, "op2": "agent_modules", "return_type": fmt, idf: idv})
                    text = r.text[:200].replace("\n", " ")
                    print(f"  {idf}={idv[:30]} ({fmt}): {text}")

asyncio.run(main())
