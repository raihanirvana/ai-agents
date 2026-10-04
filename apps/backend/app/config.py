"""Local process settings; existing environment overrides env files."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env.local", override=False)
load_dotenv(ROOT / ".env", override=False)

API_HOST = os.getenv("API_HOST", "127.0.0.1")
if API_HOST != "127.0.0.1":
    raise ValueError("API_HOST must be 127.0.0.1 for the local control API")
API_PORT = int(os.getenv("API_PORT", "8000"))
if not 1 <= API_PORT <= 65535:
    raise ValueError("API_PORT must be an integer between 1 and 65535")
web_port = int(os.getenv("WEB_PORT", "5173"))
preview_port = int(os.getenv("WEB_PREVIEW_PORT", "5174"))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        f"http://127.0.0.1:{web_port},http://127.0.0.1:{preview_port}",
    ).split(",")
    if origin.strip()
]
