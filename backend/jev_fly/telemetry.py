"""Bounded local telemetry with best-effort append-only persistence."""
from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class MatchTelemetry:
    def __init__(
        self,
        root: Path = Path("data/runs"),
        capacity: int = 512,
        open_file: Callable = open,
    ):
        self.root = Path(root)
        self.events: deque[dict[str, object]] = deque(maxlen=capacity)
        self.open_file = open_file
        self.match_id: str | None = None
        self.directory: Path | None = None
        self.write_failures = 0

    def start(self, match_id: str) -> None:
        self.events.clear()
        self.write_failures = 0
        self.match_id = str(match_id)
        if not self.match_id or Path(self.match_id).name != self.match_id:
            self.directory = None
            self._write_failure("start")
            return
        directory = self.root / self.match_id
        self.directory = None
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except OSError:
            self._write_failure("start")
        else:
            self.directory = directory

    def _write_failure(self, operation: str) -> None:
        self.write_failures += 1
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "lifecycle",
            "event": "telemetry_write_failure",
            "match_id": self.match_id,
            "operation": operation,
        }
        self.events.append(payload)
        try:
            print(json.dumps(payload), flush=True)
        except OSError:
            pass

    def emit(self, event: dict[str, object]) -> dict[str, object]:
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
        self.events.append(payload)
        if self.directory is not None:
            try:
                with self.open_file(self.directory / "events.jsonl", "a", encoding="utf-8") as output:
                    output.write(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")
            except (OSError, TypeError, ValueError):
                self._write_failure("append")
        return payload

    def lifecycle(self, event: str, **fields: object) -> None:
        payload = self.emit({"type": "lifecycle", "event": event, **fields})
        try:
            print(json.dumps({key: value for key, value in payload.items()
                              if key not in {"snapshot", "result"}}, default=str), flush=True)
        except OSError:
            pass

    def finish(self, summary: dict[str, object]) -> None:
        payload = {**summary, "events_retained": len(self.events),
                   "telemetry_write_failures": self.write_failures}
        if self.directory is not None:
            try:
                with self.open_file(self.directory / "summary.json", "w", encoding="utf-8") as output:
                    json.dump(payload, output, separators=(",", ":"))
                    output.write("\n")
            except (OSError, TypeError, ValueError):
                self._write_failure("summary")

    def recent(self, limit: int = 100) -> list[dict[str, object]]:
        limit = max(0, min(limit, self.events.maxlen or limit))
        return list(self.events)[-limit:]
