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
# Generated-app previews are served on localhost (never on the 127.0.0.1 control host), see ARCHITECTURE section 10.
PREVIEW_PORT = int(os.getenv("PREVIEW_PORT", "5180"))
if not 1024 <= PREVIEW_PORT <= 65535 or PREVIEW_PORT in (API_PORT, web_port, preview_port):
    raise ValueError("PREVIEW_PORT must be an unprivileged port different from the API and web ports")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        f"http://127.0.0.1:{web_port},http://127.0.0.1:{preview_port}",
    ).split(",")
    if origin.strip()
]


def _local_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    path = Path(raw).expanduser() if raw else default
    return path if path.is_absolute() else ROOT / path


# Runtime state (database, artifacts) lives under the gitignored data directory.
DATA_DIR = _local_path("DATA_DIR", ROOT / "data")
DATABASE_PATH = _local_path("DATABASE_PATH", DATA_DIR / "app.sqlite3")
ARTIFACT_DIR = _local_path("ARTIFACT_DIR", DATA_DIR / "artifacts")
