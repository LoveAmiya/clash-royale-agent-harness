import os
from pathlib import Path


DATA_DIR = Path("data")
SCHEDULE_FILE = DATA_DIR / "schedule.json"
TOP_DECKS_FILE = DATA_DIR / "top_decks.json"
CARDS_META_FILE = DATA_DIR / "cards_meta.json"
RAG_DOCS_FILE = DATA_DIR / "rag_documents.json"

# The API key is supplied by the process environment, never by source files.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

RUNTIME_HOST = os.getenv("RUNTIME_HOST", "0.0.0.0")
RUNTIME_PORT = int(os.getenv("RUNTIME_PORT", "8091"))

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8091/process")

OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embed")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")

RETRIEVAL_TOP_K_BM25 = int(os.getenv("RETRIEVAL_TOP_K_BM25", "10"))
RETRIEVAL_TOP_K_DENSE = int(os.getenv("RETRIEVAL_TOP_K_DENSE", "10"))
RETRIEVAL_FINAL_TOP_K = int(os.getenv("RETRIEVAL_FINAL_TOP_K", "8"))
RETRIEVAL_ALPHA = float(os.getenv("RETRIEVAL_ALPHA", "0.5"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "4"))
COMPRESS_MAX_ITEMS = int(os.getenv("COMPRESS_MAX_ITEMS", "4"))
COMPRESS_CHAR_BUDGET = int(os.getenv("COMPRESS_CHAR_BUDGET", "1200"))
