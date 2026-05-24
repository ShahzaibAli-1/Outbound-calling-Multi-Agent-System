from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

from .models import CallEvent, CallRecord, EventType


class CallStore:
    def __init__(self) -> None:
        self._calls: dict[str, CallRecord] = {}
        self._lock = Lock()

    def ensure_call(
        self,
        sid: str,
        *,
        from_number: str | None = None,
        to_number: str | None = None,
        direction: str = "inbound",
    ) -> CallRecord:
        with self._lock:
            record = self._calls.get(sid)
            if record is None:
                record = CallRecord(
                    sid=sid,
                    from_number=from_number,
                    to_number=to_number,
                    direction=direction,
                )
                self._calls[sid] = record
            else:
                if from_number:
                    record.from_number = from_number
                if to_number:
                    record.to_number = to_number
                if direction:
                    record.direction = direction

            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    def add_event(self, sid: str, event_type: EventType, text: str) -> CallRecord:
        with self._lock:
            record = self._calls.setdefault(sid, CallRecord(sid=sid))
            record.events.append(CallEvent(type=event_type, text=text))
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    def update_status(self, sid: str, status: str) -> CallRecord:
        with self._lock:
            record = self._calls.setdefault(sid, CallRecord(sid=sid))
            record.status = status
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    def get(self, sid: str) -> CallRecord | None:
        with self._lock:
            record = self._calls.get(sid)
            return record.model_copy(deep=True) if record else None

    def list_calls(self) -> list[CallRecord]:
        with self._lock:
            ordered = sorted(self._calls.values(), key=lambda item: item.updated_at, reverse=True)
            return [record.model_copy(deep=True) for record in ordered]
