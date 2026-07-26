import os
import re
from pathlib import Path


DATA_DIR = Path("data")
SCHEDULE_FILE = DATA_DIR / "schedule.json"
TOP_DECKS_FILE = DATA_DIR / "top_decks.json"
CARDS_META_FILE = DATA_DIR / "cards_meta.json"
RAG_DOCS_FILE = DATA_DIR / "rag_documents.json"

# The API key is supplied by the process environment, never by source files.
# This project uses the same OpenAI-compatible relay configured for Codex.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://crs.ruinique.com").strip().rstrip("/")
OPENAI_WIRE_API = os.getenv("OPENAI_WIRE_API", "responses").strip().lower()
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip().lower()
# The project uses a consistent medium reasoning effort for parsing and final synthesis.
PARSER_REASONING_EFFORT = os.getenv("PARSER_REASONING_EFFORT", "medium").strip().lower()
SYNTHESIS_REASONING_EFFORT = os.getenv("SYNTHESIS_REASONING_EFFORT", "medium").strip().lower()
OPENAI_CLIENT_KWARGS = {"base_url": OPENAI_BASE_URL}

def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


RUNTIME_HOST = os.getenv("RUNTIME_HOST", "127.0.0.1").strip()
RUNTIME_PORT = int(os.getenv("RUNTIME_PORT", "8091"))

WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1").strip()
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8091/process")

# Request limits are deliberately process-local. A reverse proxy remains the
# production boundary for multi-worker or multi-instance deployments.
MAX_REQUEST_BODY_BYTES = _bounded_int("MAX_REQUEST_BODY_BYTES", 65_536, 1_024, 1_048_576)
MAX_QUERY_CHARS = _bounded_int("MAX_QUERY_CHARS", 8_000, 64, 100_000)
PROCESS_MAX_CONCURRENT = _bounded_int("PROCESS_MAX_CONCURRENT", 8, 1, 128)
PROCESS_RATE_LIMIT_PER_MINUTE = _bounded_int("PROCESS_RATE_LIMIT_PER_MINUTE", 30, 0, 10_000)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080").split(",")
    if origin.strip()
)

OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")
# bge-m3 can take several seconds to load after an Ollama restart.  Ten seconds
# prevents a healthy cold-starting embedding service from permanently disabling
# dense retrieval for the lifetime of the backend process.
OLLAMA_EMBED_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_EMBED_TIMEOUT_SECONDS", "10"))
# Batch snapshot evidence during background preheating. This is deliberately
# bounded: a failed batch can fall back to BM25 without thousands of requests.
EMBED_BATCH_SIZE = max(1, min(int(os.getenv("EMBED_BATCH_SIZE", "32")), 128))
PARSER_CALL_TIMEOUT_SECONDS = float(os.getenv("PARSER_CALL_TIMEOUT_SECONDS", "45"))
# A bounded timeout keeps slow relay calls from leaving an SSE request pending forever.
MODEL_CALL_TIMEOUT_SECONDS = float(os.getenv("MODEL_CALL_TIMEOUT_SECONDS", "120"))

RETRIEVAL_TOP_K_BM25 = int(os.getenv("RETRIEVAL_TOP_K_BM25", "10"))
RETRIEVAL_TOP_K_DENSE = int(os.getenv("RETRIEVAL_TOP_K_DENSE", "10"))
RETRIEVAL_FINAL_TOP_K = int(os.getenv("RETRIEVAL_FINAL_TOP_K", "8"))
RETRIEVAL_ALPHA = float(os.getenv("RETRIEVAL_ALPHA", "0.5"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "4"))
COMPRESS_MAX_ITEMS = int(os.getenv("COMPRESS_MAX_ITEMS", "4"))
COMPRESS_CHAR_BUDGET = int(os.getenv("COMPRESS_CHAR_BUDGET", "1200"))

# Official live data is opt-in because Supercell API credentials are IP-restricted.
SUPERCELL_API_TOKEN = os.getenv("SUPERCELL_API_TOKEN", "").strip()
SUPERCELL_LIVE_DATA_ENABLED = os.getenv("SUPERCELL_LIVE_DATA_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
# Enabled by the production PowerShell launcher. Unit tests and offline work can
# opt out explicitly, but a live run must never silently report JSON snapshots
# as official Supercell data.
EXTERNAL_API_REQUIRED = os.getenv("EXTERNAL_API_REQUIRED", "false").strip().lower() in {"1", "true", "yes"}
# A leaderboard request may require multiple official API calls. Five seconds is
# too aggressive on ordinary residential networks and causes avoidable strict-mode failures.
SUPERCELL_API_TIMEOUT_SECONDS = float(os.getenv("SUPERCELL_API_TIMEOUT_SECONDS", "15"))
SUPERCELL_CACHE_TTL_SECONDS = 86400
# Production uses one complete daily official sample. The public API exposes
# player battle logs rather than global card statistics, so every answer is
# bound to this fixed, labelled sample instead of a user-selected sample size.
SUPERCELL_MAX_TARGET_BATTLES = 20000
SUPERCELL_TARGET_BATTLES = SUPERCELL_MAX_TARGET_BATTLES
SUPERCELL_LEADERBOARD_PLAYERS = max(1, min(int(os.getenv("SUPERCELL_LEADERBOARD_PLAYERS", "3000")), 3000))
SUPERCELL_BATTLES_PER_PLAYER = max(1, min(int(os.getenv("SUPERCELL_BATTLES_PER_PLAYER", "25")), 25))
# Sampling intentionally walks leaderboard rank order. A later rank must not
# overtake an earlier one merely because its request returned faster.
SUPERCELL_FETCH_CONCURRENCY = 1
LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED = os.getenv("LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
# Large samples intentionally trade freshness for controlled official API usage.
SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND = max(0.1, float(os.getenv("SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND", "2")))
SUPERCELL_HIGH_VOLUME_MAX_RETRIES = max(0, min(int(os.getenv("SUPERCELL_HIGH_VOLUME_MAX_RETRIES", "0")), 5))
SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS = max(60, min(int(os.getenv("SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS", "3600")), 7200))


def _parse_supercell_player_tags(value: str) -> tuple[str, ...]:
    tags = []
    for item in value.split(","):
        tag = item.strip().upper()
        if re.fullmatch(r"#[0289PYLQGRJCUV]{3,15}", tag) and tag not in tags:
            tags.append(tag)
    return tuple(tags[:25])


# Used only if both official global ranking sources return no players. This is
# administrator configuration, never populated from a user request.
SUPERCELL_FALLBACK_PLAYER_TAGS = _parse_supercell_player_tags(os.getenv("SUPERCELL_FALLBACK_PLAYER_TAGS", ""))
