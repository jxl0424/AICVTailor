"""Per-run JSONL log.

The brief asks for a log that says which stage produced bad output. Every LLM
call and pipeline stage appends one line, so a disappointing tailored resume
can be traced to the exact call, model and prompt that caused it.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .. import paths


class RunLog:
    """Append-only log for one pipeline run.

    Prompts are recorded in full by default. The file lives under data/, which
    is gitignored and never leaves the machine, and a redacted log is close to
    useless for working out why a rewrite came back wrong.
    """

    def __init__(self, run_id: str | None = None, *, log_prompts: bool = True) -> None:
        paths.ensure_dirs()
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.path = paths.RUNS_DIR / f"{self.run_id}.jsonl"
        self.log_prompts = log_prompts
        self._lock = threading.Lock()

    def write(self, stage: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "stage": stage,
            **fields,
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def call(
        self,
        *,
        stage: str,
        provider: str,
        model: str,
        role: str,
        system: str,
        user: str,
        response: str = "",
        attempts: int = 1,
        latency_ms: float = 0.0,
        error: str | None = None,
        **extra: Any,
    ) -> None:
        payload: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "role": role,
            "attempts": attempts,
            "latency_ms": round(latency_ms, 1),
            "prompt_chars": len(system) + len(user),
            "response_chars": len(response),
            **extra,
        }
        if error:
            payload["error"] = error
        if self.log_prompts:
            payload["system"] = system
            payload["user"] = user
            payload["response"] = response
        self.write(stage, **payload)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


def list_runs(limit: int = 50) -> list[str]:
    paths.ensure_dirs()
    files = sorted(paths.RUNS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.stem for f in files[:limit]]
