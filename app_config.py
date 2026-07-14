import os
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

RUNTIME_HOST = os.getenv("RUNTIME_HOST", "0.0.0.0")
RUNTIME_PORT = int(os.getenv("RUNTIME_PORT", "8091"))

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8091/process")

OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")
OLLAMA_EMBED_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_EMBED_TIMEOUT_SECONDS", "3"))
PARSER_CALL_TIMEOUT_SECONDS = float(os.getenv("PARSER_CALL_TIMEOUT_SECONDS", "20"))
# A bounded timeout keeps slow relay calls from leaving an SSE request pending forever.
MODEL_CALL_TIMEOUT_SECONDS = float(os.getenv("MODEL_CALL_TIMEOUT_SECONDS", "120"))

RETRIEVAL_TOP_K_BM25 = int(os.getenv("RETRIEVAL_TOP_K_BM25", "10"))
RETRIEVAL_TOP_K_DENSE = int(os.getenv("RETRIEVAL_TOP_K_DENSE", "10"))
RETRIEVAL_FINAL_TOP_K = int(os.getenv("RETRIEVAL_FINAL_TOP_K", "8"))
RETRIEVAL_ALPHA = float(os.getenv("RETRIEVAL_ALPHA", "0.5"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "4"))
COMPRESS_MAX_ITEMS = int(os.getenv("COMPRESS_MAX_ITEMS", "4"))
COMPRESS_CHAR_BUDGET = int(os.getenv("COMPRESS_CHAR_BUDGET", "1200"))
