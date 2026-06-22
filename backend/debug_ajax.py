"""Debug: test AJAX module listing for specific agent from ACA_Insurance group."""
import asyncio, httpx, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def main():
    # Force fresh login
    base = os.getenv("PANDORA_BASE_URL")
    login_url = base.rstrip("/") + "/index.php?login=1"
    ajax_url = base.rstrip("/") + "/ajax.php"

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
        # 1. Login
        print("=== 1. Login ===")
        r = await c.post(login_url, data={
            "nick": os.getenv("PANDORA_API_USER"),
            "pass": os.getenv("PANDORA_API_USER_PASS"),
            "login_button": "Login",
        })
        phpsessid = r.cookies.get("PHPSESSID", "")
        print(f"PHPSESSID: {phpsessid[:30] if phpsessid else 'NOT FOUND'}")

        # 2. Test AJAX for agent 453 (ACA_SUBSYSTEM_P - should have CPU/Mem/Disk)
        print("\n=== 2. AJAX agent=453 (ACA_SUBSYSTEM_P) ===")
        r = await c.post(ajax_url, data={
            "page": "operation/agentes/ver_agente",
            "get_modules_group_json": "1",
            "id_module_group": "0",
            "id_agents": "453",
        }, cookies={"PHPSESSID": phpsessid})
        print(f"Status: {r.status_code}")
        text = r.text[:500]
        print(f"Response[:500]: {text}")

        if not text or text == "null":
            print("EMPTY/NULL response!")
        else:
            try:
                data = r.json()
                if isinstance(data, dict):
                    print(f"Module count: {len(data)}")
                    for mid, info in list(data.items())[:10]:
                        name = info.get("nombre","?") if isinstance(info,dict) else str(info)
                        print(f"  {mid}: {name}")
                    # Check if any match our keywords
                    print("\n  CPU/Mem/Disk matches:")
                    for mid, info in data.items():
                        name = (info.get("nombre","") if isinstance(info,dict) else str(info)).lower()
                        if any(kw in name for kw in ["cpu","proc","load","iowait","mem","ram","swap","disk","storage"]):
                            print(f"    {mid}: {info.get('nombre','?') if isinstance(info,dict) else str(info)}")
            except Exception as e:
                print(f"JSON parse error: {e}")

        # 3. Test AJAX for agent 762 (try an agent from group 36)
        print("\n=== 3. AJAX agent=762 ===")
        r = await c.post(ajax_url, data={
            "page": "operation/agentes/ver_agente",
            "get_modules_group_json": "1",
            "id_module_group": "0",
            "id_agents": "762",
        }, cookies={"PHPSESSID": phpsessid})
        text = r.text[:500]
        print(f"Response[:500]: {text}")
        try:
            data = r.json()
            if isinstance(data, dict) and data:
                print(f"Module count: {len(data)}")
                # Show first 5
                for mid, info in list(data.items())[:5]:
                    name = info.get("nombre","?") if isinstance(info,dict) else str(info)
                    print(f"  {mid}: {name}")
        except:
            print("Not JSON or empty")

asyncio.run(main())
