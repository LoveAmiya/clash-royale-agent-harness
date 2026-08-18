"""Minimal persistent Alertmanager webhook receiver for deployment drills."""

from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from clashroyale_agent.ops.runtime_hardening import redact_for_client


ALERT_STORE_FILE = Path(os.getenv("ALERT_STORE_FILE", "/var/lib/alert-receiver/alerts.jsonl"))
ALERT_PAYLOAD_MAX_BYTES = max(1024, min(int(os.getenv("ALERT_PAYLOAD_MAX_BYTES", "262144")), 1_048_576))
ALERT_STORE_MAX_BYTES = max(1_048_576, min(int(os.getenv("ALERT_STORE_MAX_BYTES", "10485760")), 104_857_600))
_write_lock = threading.Lock()
_received_total = 0
_last_received_at: str | None = None

def summarize_alert_store(path: Path) -> tuple[int, str | None]:
    total = 0
    last_received_at = None
    if not path.exists():
        return total, last_received_at
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += len(record.get("alerts", [])) if isinstance(record.get("alerts"), list) else 0
            last_received_at = record.get("received_at") or last_received_at
    return total, last_received_at


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _received_total, _last_received_at
    _received_total, _last_received_at = summarize_alert_store(ALERT_STORE_FILE)
    yield


app = FastAPI(title="ClashRoyaleAlertReceiver", lifespan=lifespan)


def normalize_alert_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise ValueError("Alertmanager webhook payload must contain an alerts list")
    alerts = []
    for item in payload["alerts"][:100]:
        if not isinstance(item, dict):
            continue
        alerts.append(
            redact_for_client(
                {
                    "status": item.get("status"),
                    "labels": item.get("labels") if isinstance(item.get("labels"), dict) else {},
                    "annotations": item.get("annotations") if isinstance(item.get("annotations"), dict) else {},
                    "startsAt": item.get("startsAt"),
                    "endsAt": item.get("endsAt"),
                }
            )
        )
    return {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "status": payload.get("status"),
        "receiver": payload.get("receiver"),
        "groupKey": str(payload.get("groupKey") or "")[:512],
        "commonLabels": redact_for_client(payload.get("commonLabels") or {}),
        "alerts": alerts,
    }


def persist_alert(payload: dict, path: Path = ALERT_STORE_FILE) -> dict:
    global _received_total, _last_received_at
    record = normalize_alert_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    with _write_lock:
        if path.exists() and path.stat().st_size + len(encoded) + 1 > ALERT_STORE_MAX_BYTES:
            rotated = path.with_suffix(path.suffix + ".1")
            if rotated.exists():
                rotated.unlink()
            path.replace(rotated)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        _received_total += len(record["alerts"])
        _last_received_at = record["received_at"]
    return record


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/alerts")
async def receive_alerts(request: Request):
    body = await request.body()
    if len(body) > ALERT_PAYLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="alert payload too large")
    try:
        payload = json.loads(body)
        record = persist_alert(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "recorded", "alerts": len(record["alerts"]), "received_at": record["received_at"]}


@app.get("/alerts/stats")
async def alert_stats():
    return {"received_total": _received_total, "last_received_at": _last_received_at}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(
        "# HELP cr_agent_alert_notifications_total Alerts accepted from Alertmanager.\n"
        "# TYPE cr_agent_alert_notifications_total counter\n"
        f"cr_agent_alert_notifications_total {_received_total}\n",
        media_type="text/plain; version=0.0.4",
    )

__all__ = [
    "ALERT_PAYLOAD_MAX_BYTES",
    "ALERT_STORE_FILE",
    "ALERT_STORE_MAX_BYTES",
    "app",
    "normalize_alert_payload",
    "persist_alert",
    "summarize_alert_store",
]
