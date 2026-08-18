"""File-backed browser UI template loader."""

from __future__ import annotations

from pathlib import Path


TEMPLATE_DIR = Path(__file__).with_name("templates")
INDEX_TEMPLATE_PATH = TEMPLATE_DIR / "index.html"


def load_index_html(path: Path = INDEX_TEMPLATE_PATH) -> str:
    return path.read_text(encoding="utf-8")


HTML_PAGE = load_index_html()


__all__ = ["HTML_PAGE", "INDEX_TEMPLATE_PATH", "TEMPLATE_DIR", "load_index_html"]
