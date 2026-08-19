"""Pure external-review validation for snapshot RAG exports."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Callable

NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_])")


def read_jsonl_documents(path: Path, *, max_bytes: int, error_type: type[ValueError]) -> list[dict]:
    documents: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if len(line.encode("utf-8")) > max_bytes:
                raise error_type(f"review document line {line_number} exceeds size limit")
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise error_type(f"invalid review JSON on line {line_number}") from exc
            if not isinstance(document, dict):
                raise error_type(f"review line {line_number} is not an object")
            documents.append(document)
    return documents


def normalized_numbers(value: object) -> Counter[str]:
    tokens: Counter[str] = Counter()
    def add(number: object) -> None:
        if isinstance(number, bool):
            return
        try:
            decimal = Decimal(str(number)).normalize()
        except (InvalidOperation, ValueError):
            return
        tokens[format(decimal, "f")] += 1
    if isinstance(value, str):
        for match in NUMBER_TOKEN.finditer(value):
            add(match.group(0))
    elif isinstance(value, dict):
        for item in value.values():
            if isinstance(item, (int, float, Decimal)) and not isinstance(item, bool):
                add(item)
            elif isinstance(item, (dict, list, tuple)):
                tokens.update(normalized_numbers(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            tokens.update(normalized_numbers(item))
    return tokens


def verify_audit_files(audit_dir: Path, manifest: dict, *, sha256: Callable[[Path], str]) -> list[str]:
    mismatches: list[str] = []
    for entry in manifest.get("files", []):
        relative = str(entry.get("path") or "")
        candidate = (audit_dir / relative).resolve()
        try:
            candidate.relative_to(audit_dir.resolve())
        except ValueError:
            mismatches.append(relative or "<missing-path>")
            continue
        if not candidate.is_file() or sha256(candidate) != entry.get("sha256"):
            mismatches.append(relative or "<missing-path>")
    return mismatches


def review_validation_report(*, snapshot_id: str, expected: list[dict], reviewed: list[dict], audit_hash_mismatches: list[str], reviewed_path: Path, schema_version: int, source: str, fingerprint: Callable[[list[dict]], str], sha256: Callable[[Path], str]) -> dict:
    failures: set[str] = set()
    invalid_doc_ids: set[str] = set()
    if audit_hash_mismatches:
        failures.add("audit_file_hash_mismatch")
    expected_by_id = {document.get("doc_id"): document for document in expected if isinstance(document, dict) and isinstance(document.get("doc_id"), str)}
    reviewed_by_id: dict[str, dict] = {}
    for document in reviewed:
        doc_id = document.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id or doc_id in reviewed_by_id:
            failures.add("duplicate_or_missing_doc_id")
            invalid_doc_ids.add(str(doc_id or "<missing-doc-id>"))
            continue
        reviewed_by_id[doc_id] = document
    if set(expected_by_id) != set(reviewed_by_id):
        failures.add("document_id_coverage_mismatch")
        invalid_doc_ids.update(str(value) for value in set(expected_by_id) ^ set(reviewed_by_id))
    for doc_id in set(expected_by_id) & set(reviewed_by_id):
        source_doc, candidate = expected_by_id[doc_id], reviewed_by_id[doc_id]
        if set(candidate) != {"doc_id", "source_type", "text", "metadata"}:
            failures.add("invalid_document_shape"); invalid_doc_ids.add(doc_id)
        if candidate.get("source_type") != source_doc.get("source_type"):
            failures.add("source_field_mismatch"); invalid_doc_ids.add(doc_id)
        metadata = candidate.get("metadata")
        if metadata != source_doc.get("metadata"):
            failures.add("metadata_mismatch"); invalid_doc_ids.add(doc_id)
        if not isinstance(metadata, dict) or metadata.get("snapshot_id") != snapshot_id:
            failures.add("snapshot_id_mismatch"); invalid_doc_ids.add(doc_id)
        if not isinstance(metadata, dict) or metadata.get("source") != source:
            failures.add("source_field_mismatch"); invalid_doc_ids.add(doc_id)
        text = candidate.get("text")
        if not isinstance(text, str) or not text.strip():
            failures.add("invalid_document_text"); invalid_doc_ids.add(doc_id); continue
        allowed = normalized_numbers(source_doc.get("text", "")); allowed.update(normalized_numbers(source_doc.get("metadata", {})))
        if any(normalized_numbers(text)[token] > allowed[token] for token in normalized_numbers(text)):
            failures.add("numeric_claim_mismatch"); invalid_doc_ids.add(doc_id)
    return {"schema_version": schema_version, "snapshot_id": snapshot_id, "validated_at": datetime.now(timezone.utc).isoformat(), "passed": not failures, "failures": sorted(failures), "invalid_doc_ids": sorted(invalid_doc_ids), "audit_hash_mismatches": sorted(audit_hash_mismatches), "document_count": len(reviewed), "expected_document_count": len(expected), "reviewed_file_sha256": sha256(reviewed_path), "reviewed_docs_fingerprint": fingerprint(reviewed), "generated_docs_fingerprint": fingerprint(expected), "activation": "staged_only", "active_rag_documents_changed": False, "cost_boundaries": {"supercell_requests": 0, "cloud_llm_calls": 0, "cloud_embedding_calls": 0, "local_embedding_calls": 0}}
