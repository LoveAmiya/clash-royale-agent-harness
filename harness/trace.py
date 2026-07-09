import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


TRACE_LOG_PATH = Path("logs") / "traces.jsonl"


@dataclass(slots=True)
class TraceEvent:
    trace_id: str
    state: str
    selected_skill: str | None
    intent: str | None
    mode: str | None
    metadata: dict | None = None
    latency_ms: int | None = None
    success: bool | None = None
    error: str | None = None
    parsed: dict | None = None
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class TraceRecorder:
    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path or TRACE_LOG_PATH

    def new_trace_id(self) -> str:
        return f"trace-{uuid.uuid4().hex}"

    def record(self, event: TraceEvent) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
