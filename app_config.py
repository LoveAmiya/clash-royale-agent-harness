import os
import re
from pathlib import Path


DATA_DIR = Path("data")
# This is reviewed terminology configuration, not a published snapshot.
CARD_ALIAS_FILE = DATA_DIR / "card_aliases.zh-CN.json"
# Legacy single-snapshot RAG fallback. Rolling snapshot groups provide the
# production corpus, but the compatibility reader remains for old archives.
RAG_DOCS_FILE = DATA_DIR / "rag_documents.json"

# The credential is supplied by the process environment. Provider routing is a
# project contract so a stale machine-level OPENAI_BASE_URL cannot silently
# redirect this application to a different service.
OPENAI_MODEL = "gpt-5.5"
OPENAI_REVIEW_MODEL = "gpt-5.5"
OPENAI_BASE_URL = "https://crs.ruinique.com"
OPENAI_WIRE_API = "responses"
OPENAI_REASONING_EFFORT = "medium"
PARSER_REASONING_EFFORT = "medium"
SYNTHESIS_REASONING_EFFORT = "medium"
OPENAI_CLIENT_KWARGS = {"base_url": OPENAI_BASE_URL}

def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


RUNTIME_HOST = os.getenv("RUNTIME_HOST", "127.0.0.1").strip()
RUNTIME_PORT = int(os.getenv("RUNTIME_PORT", "8091"))
RUNTIME_ROLE = os.getenv("RUNTIME_ROLE", "all").strip().lower()
if RUNTIME_ROLE not in {"all", "api", "collector"}:
    RUNTIME_ROLE = "all"
SNAPSHOT_FOLLOWER_POLL_SECONDS = max(5, min(int(os.getenv("SNAPSHOT_FOLLOWER_POLL_SECONDS", "30")), 3600))
SNAPSHOT_AUTO_FOLLOW_ENABLED = os.getenv("SNAPSHOT_AUTO_FOLLOW_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}
RAG_INDEX_MODE = os.getenv("RAG_INDEX_MODE", "persistent").strip().lower()
if RAG_INDEX_MODE not in {"persistent", "memory"}:
    RAG_INDEX_MODE = "persistent"

WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1").strip()
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8091/process")

# Request limits are deliberately process-local. A reverse proxy remains the
# production boundary for multi-worker or multi-instance deployments.
MAX_REQUEST_BODY_BYTES = _bounded_int("MAX_REQUEST_BODY_BYTES", 65_536, 1_024, 1_048_576)
MAX_QUERY_CHARS = _bounded_int("MAX_QUERY_CHARS", 8_000, 64, 100_000)
PROCESS_MAX_CONCURRENT = _bounded_int("PROCESS_MAX_CONCURRENT", 8, 1, 128)
PROCESS_RATE_LIMIT_PER_MINUTE = _bounded_int("PROCESS_RATE_LIMIT_PER_MINUTE", 30, 0, 10_000)
PROCESS_QUOTA_BACKEND = os.getenv("PROCESS_QUOTA_BACKEND", "memory").strip().lower()
if PROCESS_QUOTA_BACKEND not in {"memory", "redis"}:
    PROCESS_QUOTA_BACKEND = "memory"
REDIS_URL = os.getenv("REDIS_URL", "").strip()
PROCESS_QUOTA_KEY_PREFIX = os.getenv("PROCESS_QUOTA_KEY_PREFIX", "cr-agent:process-quota").strip()
PROCESS_QUOTA_LEASE_SECONDS = max(5, min(int(os.getenv("PROCESS_QUOTA_LEASE_SECONDS", "300")), 3600))
PROCESS_QUOTA_FAIL_MODE = os.getenv("PROCESS_QUOTA_FAIL_MODE", "closed").strip().lower()
if PROCESS_QUOTA_FAIL_MODE not in {"closed", "open"}:
    PROCESS_QUOTA_FAIL_MODE = "closed"
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in {"1", "true", "yes"}
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
# Reasoning models can spend most of a request budget before publishing text.
# Keep the total safety bound above, but stop an entirely silent generation
# earlier so the runtime can return its already validated evidence instead.
MODEL_FIRST_TOKEN_TIMEOUT_SECONDS = max(
    5.0,
    min(float(os.getenv("MODEL_FIRST_TOKEN_TIMEOUT_SECONDS", "75")), MODEL_CALL_TIMEOUT_SECONDS),
)
MODEL_PROGRESS_INTERVAL_SECONDS = max(
    0.5,
    min(float(os.getenv("MODEL_PROGRESS_INTERVAL_SECONDS", "2")), 10.0),
)
MODEL_CIRCUIT_FAILURE_THRESHOLD = _bounded_int("MODEL_CIRCUIT_FAILURE_THRESHOLD", 3, 1, 20)
MODEL_CIRCUIT_RECOVERY_SECONDS = max(
    1.0,
    min(float(os.getenv("MODEL_CIRCUIT_RECOVERY_SECONDS", "60")), 3600.0),
)

RETRIEVAL_TOP_K_BM25 = _bounded_int("RETRIEVAL_TOP_K_BM25", 32, 1, 200)
RETRIEVAL_TOP_K_DENSE = _bounded_int("RETRIEVAL_TOP_K_DENSE", 32, 1, 200)
RETRIEVAL_FINAL_TOP_K = _bounded_int("RETRIEVAL_FINAL_TOP_K", 24, 1, 100)
RETRIEVAL_ALPHA = max(0.0, min(float(os.getenv("RETRIEVAL_ALPHA", "0.5")), 1.0))
RETRIEVAL_FUSION_MODE = os.getenv("RETRIEVAL_FUSION_MODE", "rrf").strip().lower()
if RETRIEVAL_FUSION_MODE not in {"rrf", "weighted"}:
    RETRIEVAL_FUSION_MODE = "rrf"
RETRIEVAL_RRF_K = _bounded_int("RETRIEVAL_RRF_K", 60, 1, 1_000)
RERANK_TOP_N = _bounded_int("RERANK_TOP_N", 8, 1, 50)
COMPRESS_MAX_ITEMS = _bounded_int("COMPRESS_MAX_ITEMS", 6, 1, 20)
COMPRESS_CHAR_BUDGET = _bounded_int("COMPRESS_CHAR_BUDGET", 2000, 256, 20_000)
META_RETRIEVAL_LANE_TOP_K = _bounded_int("META_RETRIEVAL_LANE_TOP_K", 8, 1, 50)
META_RERANK_TOP_N = _bounded_int("META_RERANK_TOP_N", 12, 1, 50)
META_COMPRESS_MAX_ITEMS = _bounded_int("META_COMPRESS_MAX_ITEMS", 10, 1, 20)
META_COMPRESS_CHAR_BUDGET = _bounded_int("META_COMPRESS_CHAR_BUDGET", 4200, 512, 30_000)
RAG_QUALITY_GATE_ENABLED = os.getenv("RAG_QUALITY_GATE_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
RAG_MIN_DOCUMENTS = _bounded_int("RAG_MIN_DOCUMENTS", 100, 1, 100_000)
RAG_MIN_SOURCE_TYPES = _bounded_int("RAG_MIN_SOURCE_TYPES", 6, 1, 100)
RAG_MIN_PROBE_RECALL_PERCENT = _bounded_int("RAG_MIN_PROBE_RECALL_PERCENT", 60, 0, 100)
RAG_PROBES_PER_SOURCE = _bounded_int("RAG_PROBES_PER_SOURCE", 3, 1, 20)
RAG_QUALITY_REPORT_DIR = Path(os.getenv("RAG_QUALITY_REPORT_DIR", "data/rag_quality"))
RAG_FACT_VALIDATION_ENABLED = os.getenv("RAG_FACT_VALIDATION_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
FEEDBACK_DB_FILE = Path(os.getenv("FEEDBACK_DB_FILE", "data/feedback.sqlite3"))
FEEDBACK_CACHE_MAX_ITEMS = _bounded_int("FEEDBACK_CACHE_MAX_ITEMS", 512, 16, 10_000)
FEEDBACK_CACHE_TTL_SECONDS = max(60, min(int(os.getenv("FEEDBACK_CACHE_TTL_SECONDS", "3600")), 86_400))
FEEDBACK_MAX_CORRECTION_CHARS = _bounded_int("FEEDBACK_MAX_CORRECTION_CHARS", 4000, 64, 20_000)

# Official live data is opt-in because Supercell API credentials are IP-restricted.
SUPERCELL_API_TOKEN = os.getenv("SUPERCELL_API_TOKEN", "").strip()
SUPERCELL_LIVE_DATA_ENABLED = os.getenv("SUPERCELL_LIVE_DATA_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
# Enabled by the production PowerShell launcher. Unit tests and offline work can
# opt out explicitly, but a live run must never silently report JSON snapshots
# as official Supercell data.
EXTERNAL_API_REQUIRED = os.getenv("EXTERNAL_API_REQUIRED", "false").strip().lower() in {"1", "true", "yes"}
# A leaderboard request may require multiple official API calls. Five seconds is
# too aggressive on ordinary residential networks and causes avoidable strict-mode failures.
SUPERCELL_API_TIMEOUT_SECONDS = float(os.getenv("SUPERCELL_API_TIMEOUT_SECONDS", "30"))
SUPERCELL_CACHE_TTL_SECONDS = 86400
# Production uses one complete weekly official sample. The public API exposes
# player battle logs rather than global card statistics, so every answer is
# bound to this fixed, labelled sample instead of a user-selected sample size.
SUPERCELL_MAX_TARGET_BATTLES = 200000
SUPERCELL_TARGET_BATTLES = SUPERCELL_MAX_TARGET_BATTLES
# Collection starts from the global Path of Legend top 1,000, then expands
# through opponent tags while the larger queue budget remains bounded.
SUPERCELL_POL_SEED_PLAYERS = 1000
SUPERCELL_LEADERBOARD_PLAYERS = max(1, min(int(os.getenv("SUPERCELL_LEADERBOARD_PLAYERS", "12000")), 20000))
SUPERCELL_BATTLES_PER_PLAYER = max(1, min(int(os.getenv("SUPERCELL_BATTLES_PER_PLAYER", "25")), 25))
# Sampling intentionally walks leaderboard rank order. A later rank must not
# overtake an earlier one merely because its request returned faster.
SUPERCELL_FETCH_CONCURRENCY = 1
LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED = os.getenv("LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
# Large samples intentionally trade freshness for controlled official API usage.
SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND = max(0.1, float(os.getenv("SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND", "1")))
SUPERCELL_HIGH_VOLUME_MAX_RETRIES = max(0, min(int(os.getenv("SUPERCELL_HIGH_VOLUME_MAX_RETRIES", "2")), 5))
SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS = max(60, min(int(os.getenv("SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS", "28800")), 86400))
SNAPSHOT_PROGRESS_INTERVAL_SECONDS = max(60, min(int(os.getenv("SNAPSHOT_PROGRESS_INTERVAL_SECONDS", "3600")), 21600))


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
