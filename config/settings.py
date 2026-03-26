"""
config/settings.py
Central configuration loader for STEG Data Lakehouse.
Reads from .env file or environment variables.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from config/ directory
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)

# ── Database ────────────────────────────────────────────────
NEON_DATABASE_URL: str = os.environ["NEON_DATABASE_URL"]

# ── LLM ─────────────────────────────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "llama3-70b-8192")

# ── External APIs ────────────────────────────────────────────
WORLD_BANK_API: str = os.environ.get("WORLD_BANK_API", "https://api.worldbank.org/v2")
OPEN_METEO_API: str = os.environ.get("OPEN_METEO_API", "https://archive-api.open-meteo.com/v1/archive")

# ── Paths ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
RAW_DATA_DIR = Path(os.environ.get("RAW_DATA_DIR", str(PROJECT_ROOT / "data" / "raw")))
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── PROSOL regions ───────────────────────────────────────────
_regions_env = os.environ.get("PROSOL_REGIONS", "bizerte")
PROSOL_REGIONS: list[str] = [r.strip().lower() for r in _regions_env.split(",") if r.strip()]

# ── Logging ──────────────────────────────────────────────────
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

# ── Tunisia geography reference ──────────────────────────────
# MENA peer countries for benchmarking (World Bank country codes)
BENCHMARK_COUNTRIES = {
    "TUN": "Tunisia",
    "MAR": "Morocco",
    "DZA": "Algeria",
    "EGY": "Egypt",
    "JOR": "Jordan",
    "LBN": "Lebanon",
    "TUR": "Turkey",
    "PRT": "Portugal",   # EU comparison (similar climate)
    "GRC": "Greece",
}

# Tunisia bounding box for Open-Meteo
TUNISIA_LAT = 33.8869
TUNISIA_LON = 9.5375