"""Central place for reading environment variables (loaded from backend/.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

# Session cookie
SESSION_COOKIE_NAME = "nyaya_session"
SESSION_EXPIRY_DAYS = int(os.getenv("SESSION_EXPIRY_DAYS", "7"))
# Secure cookies are required in production (HTTPS). SameSite=none is needed when
# frontend and backend are on different domains (e.g. Render + Vercel).
COOKIE_SECURE = _bool("COOKIE_SECURE", ENVIRONMENT == "production")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "none" if COOKIE_SECURE else "lax")

# LLM (any OpenAI-compatible chat-completions API)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-6-sol")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))

# Chat limits
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "2000"))
HISTORY_MESSAGES = int(os.getenv("HISTORY_MESSAGES", "6"))
