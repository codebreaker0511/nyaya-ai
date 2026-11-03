"""Central place for reading environment variables (loaded from backend/.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").strip().rstrip("/")

# Session cookie
SESSION_COOKIE_NAME = "nyaya_session"
SESSION_EXPIRY_DAYS = int(os.getenv("SESSION_EXPIRY_DAYS", "7"))
# Secure cookies are required in production (HTTPS). SameSite=none is needed when
# frontend and backend are on different domains (e.g. Render + Vercel).
COOKIE_SECURE = _bool("COOKIE_SECURE", ENVIRONMENT == "production" or FRONTEND_ORIGIN.startswith("https://"))
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "none" if COOKIE_SECURE else "lax")

# LLM (any OpenAI-compatible chat-completions API)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-6-sol")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
# Cap on output tokens per call. Needed so the quota can reserve the worst case before calling.
# For reasoning models this cap includes reasoning tokens, so don't set it too low.
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000"))
LLM_TITLE_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_TITLE_MAX_OUTPUT_TOKENS", "300"))
# Name of that cap in the request body: "max_completion_tokens" for current OpenAI models,
# "max_tokens" for some other OpenAI-compatible providers.
LLM_MAX_TOKENS_PARAM = os.getenv("LLM_MAX_TOKENS_PARAM", "max_completion_tokens")

# Embeddings (same key/base URL as the LLM unless overridden) for the RAG index
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# Chat limits
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "2000"))
HISTORY_MESSAGES = int(os.getenv("HISTORY_MESSAGES", "6"))

# RAG over the pre-built Constitution index (backend/data/index, built by scripts/ingest.py)
RAG_ENABLED = _bool("RAG_ENABLED", True)
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "4"))  # passages given to the LLM
# Cosine-similarity cutoff: passages scoring below this are treated as "not relevant" and the
# LLM answers without them. Tune with scripts/eval_rag.py.
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.30"))
RAG_EMBED_TIMEOUT_SECONDS = float(os.getenv("RAG_EMBED_TIMEOUT_SECONDS", "10"))

# Abuse protection (see services/limits.py). Counters are stored in PostgreSQL, shared by all workers.
DAILY_TOKEN_LIMIT = int(os.getenv("DAILY_TOKEN_LIMIT", "40000"))  # input + output tokens per session per day
# Ceiling for ALL sessions together per day. Protects the budget when people create many sessions.
GLOBAL_DAILY_TOKEN_LIMIT = int(os.getenv("GLOBAL_DAILY_TOKEN_LIMIT", "2000000"))
QUOTA_TIMEZONE = os.getenv("QUOTA_TIMEZONE", "UTC")  # the day resets at midnight in this timezone
SESSION_RATE_LIMIT_PER_MINUTE = int(os.getenv("SESSION_RATE_LIMIT_PER_MINUTE", "5"))  # 0 disables
IP_RATE_LIMIT_PER_MINUTE = int(os.getenv("IP_RATE_LIMIT_PER_MINUTE", "10"))  # 0 disables
SESSION_CREATE_LIMIT_PER_IP_PER_HOUR = int(os.getenv("SESSION_CREATE_LIMIT_PER_IP_PER_HOUR", "30"))  # 0 disables
# How many reverse proxies in front of the app append to X-Forwarded-For. 0 = use the socket peer
# address and ignore X-Forwarded-For completely. Must match the real deployment (see README).
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
# A reservation older than this belongs to a request that died; it is cleaned up.
RESERVATION_TTL_SECONDS = int(os.getenv("RESERVATION_TTL_SECONDS", str(max(300, int(LLM_TIMEOUT_SECONDS * 4)))))
