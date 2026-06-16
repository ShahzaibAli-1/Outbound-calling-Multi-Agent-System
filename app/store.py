from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

from .models import CallEvent, CallRecord, EventType, PatientIntakeRecord


class CallStore:
    def __init__(self) -> None:
        self._calls: dict[str, CallRecord] = {}
        self._patient_intakes: dict[str, PatientIntakeRecord] = {}
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
            if record is None:
                return None
            return self._attach_intake(record.model_copy(deep=True))

    def list_calls(self) -> list[CallRecord]:
        with self._lock:
            ordered = sorted(self._calls.values(), key=lambda item: item.updated_at, reverse=True)
            return [self._attach_intake(record.model_copy(deep=True)) for record in ordered]

    def upsert_patient_intake(self, intake: PatientIntakeRecord) -> PatientIntakeRecord:
        with self._lock:
            existing = self._patient_intakes.get(intake.call_sid)
            if existing is not None:
                merged = existing.model_copy(
                    update={
                        **{
                            key: value
                            for key, value in intake.model_dump().items()
                            if key not in {"call_sid", "created_at", "updated_at"}
                            and value is not None
                            and (not isinstance(value, str) or value.strip())
                        },
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
            else:
                merged = intake.model_copy(update={"updated_at": datetime.now(timezone.utc)})

            self._patient_intakes[intake.call_sid] = merged
            call_record = self._calls.get(intake.call_sid)
            if call_record is not None:
                call_record.patient_intake = merged.model_copy(deep=True)
                call_record.updated_at = datetime.now(timezone.utc)

            return merged.model_copy(deep=True)

    def get_patient_intake(self, call_sid: str) -> PatientIntakeRecord | None:
        with self._lock:
            intake = self._patient_intakes.get(call_sid)
            return intake.model_copy(deep=True) if intake else None

    def list_patient_intakes(self) -> list[PatientIntakeRecord]:
        with self._lock:
            ordered = sorted(
                self._patient_intakes.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            return [record.model_copy(deep=True) for record in ordered]

    def _attach_intake(self, record: CallRecord) -> CallRecord:
        intake = self._patient_intakes.get(record.sid)
        if intake is not None:
            record.patient_intake = intake.model_copy(deep=True)
        return record
