"""
Application configuration — all values read from environment variables.
Never hardcode credentials; copy .env.example to .env and fill in real values.
"""

import os
from pathlib import Path

# Auto-load .env file (jika python-dotenv tersedia)
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass  # dotenv is optional; fall back to system env vars

# ── Pandora FMS API ──────────────────────────────────────────────
PANDORA_BASE_URL: str = os.getenv(
    "PANDORA_BASE_URL", "https://your-pandora-instance.example.com/pandora_console"
)
PANDORA_API_USER: str = os.getenv("PANDORA_API_USER", "")
PANDORA_API_USER_PASS: str = os.getenv("PANDORA_API_USER_PASS", "")
PANDORA_API_PASSWORD: str = os.getenv("PANDORA_API_PASSWORD", "")

# ── Application ──────────────────────────────────────────────────
APP_ENV: str = os.getenv("APP_ENV", "development")
APP_PORT: int = int(os.getenv("APP_PORT", "8000"))

# ── Pandora Database (MySQL, read-only) ──────────────────────────
PANDORA_DB_HOST: str = os.getenv("PANDORA_DB_HOST", "localhost")
PANDORA_DB_PORT: int = int(os.getenv("PANDORA_DB_PORT", "3306"))
PANDORA_DB_USER: str = os.getenv("PANDORA_DB_USER", "")
PANDORA_DB_PASS: str = os.getenv("PANDORA_DB_PASS", "")
PANDORA_DB_NAME: str = os.getenv("PANDORA_DB_NAME", "pandora")

# ── Paths ────────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).resolve().parent
OUTPUT_DIR: Path = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
