from __future__ import annotations

from datetime import datetime, timezone

from app.database import _connect, _utc_now, init_db
from .models import CallEvent, CallRecord, EventType, PatientIntakeRecord


class CallStore:
    def __init__(self) -> None:
        init_db()

    def ensure_call(
        self,
        sid: str,
        *,
        from_number: str | None = None,
        to_number: str | None = None,
        direction: str = "inbound",
        call_type: str = "phone",
        scenario_id: str | None = None,
    ) -> CallRecord:
        now = _utc_now()
        with _connect() as conn:
            existing = conn.execute("SELECT sid FROM calls WHERE sid = ?", (sid,)).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO calls (sid, direction, status, call_type, scenario_id, from_number, to_number, created_at, updated_at)
                    VALUES (?, ?, 'created', ?, ?, ?, ?, ?, ?)
                    """,
                    (sid, direction, call_type, scenario_id, from_number, to_number, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE calls
                    SET from_number = COALESCE(?, from_number),
                        to_number = COALESCE(?, to_number),
                        direction = COALESCE(?, direction),
                        call_type = COALESCE(?, call_type),
                        scenario_id = COALESCE(?, scenario_id),
                        updated_at = ?
                    WHERE sid = ?
                    """,
                    (from_number, to_number, direction, call_type, scenario_id, now, sid),
                )
        return self.get(sid) or CallRecord(sid=sid)

    def add_event(self, sid: str, event_type: EventType, text: str) -> CallRecord:
        now = _utc_now()
        call_type = "demo" if sid.startswith("demo_") else "phone"
        direction = "demo" if call_type == "demo" else "inbound"
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO calls (sid, direction, status, call_type, created_at, updated_at)
                VALUES (?, ?, 'created', ?, ?, ?)
                """,
                (sid, direction, call_type, now, now),
            )
            conn.execute(
                "INSERT INTO call_events (call_sid, event_type, text, timestamp) VALUES (?, ?, ?, ?)",
                (sid, event_type, text, now),
            )
            conn.execute("UPDATE calls SET updated_at = ? WHERE sid = ?", (now, sid))
        return self.get(sid) or CallRecord(sid=sid)

    def update_status(self, sid: str, status: str) -> CallRecord:
        now = _utc_now()
        with _connect() as conn:
            conn.execute("UPDATE calls SET status = ?, updated_at = ? WHERE sid = ?", (status, now, sid))
        return self.get(sid) or CallRecord(sid=sid)

    def get(self, sid: str) -> CallRecord | None:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM calls WHERE sid = ?", (sid,)).fetchone()
            if row is None:
                return None
            events = conn.execute(
                "SELECT event_type, text, timestamp FROM call_events WHERE call_sid = ? ORDER BY id ASC",
                (sid,),
            ).fetchall()
            intake_row = conn.execute("SELECT * FROM patient_intakes WHERE call_sid = ?", (sid,)).fetchone()
        return self._row_to_call(row, events, intake_row)

    def list_calls(self, limit: int = 50) -> list[CallRecord]:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM calls ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            results: list[CallRecord] = []
            for row in rows:
                sid = row["sid"]
                events = conn.execute(
                    "SELECT event_type, text, timestamp FROM call_events WHERE call_sid = ? ORDER BY id ASC",
                    (sid,),
                ).fetchall()
                intake_row = conn.execute(
                    "SELECT * FROM patient_intakes WHERE call_sid = ?",
                    (sid,),
                ).fetchone()
                results.append(self._row_to_call(row, events, intake_row))
            return results

    def upsert_patient_intake(self, intake: PatientIntakeRecord) -> PatientIntakeRecord:
        now = _utc_now()
        with _connect() as conn:
            existing = conn.execute(
                "SELECT * FROM patient_intakes WHERE call_sid = ?",
                (intake.call_sid,),
            ).fetchone()
            if existing:
                merged = dict(existing)
                for key, value in intake.model_dump().items():
                    if key in {"call_sid", "created_at"}:
                        continue
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    merged[key] = value.strip() if isinstance(value, str) else value
                merged["updated_at"] = now
            else:
                merged = intake.model_dump()
                merged["created_at"] = now
                merged["updated_at"] = now

            conn.execute(
                """
                INSERT INTO patient_intakes (
                    call_sid, full_name, date_of_birth, phone_number, email, reason_for_visit,
                    chief_complaint, symptoms, allergies, current_medications, insurance_provider,
                    insurance_member_id, preferred_appointment, emergency_contact_name,
                    emergency_contact_phone, notes, intake_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_sid) DO UPDATE SET
                    full_name = excluded.full_name,
                    date_of_birth = excluded.date_of_birth,
                    phone_number = excluded.phone_number,
                    email = excluded.email,
                    reason_for_visit = excluded.reason_for_visit,
                    chief_complaint = excluded.chief_complaint,
                    symptoms = excluded.symptoms,
                    allergies = excluded.allergies,
                    current_medications = excluded.current_medications,
                    insurance_provider = excluded.insurance_provider,
                    insurance_member_id = excluded.insurance_member_id,
                    preferred_appointment = excluded.preferred_appointment,
                    emergency_contact_name = excluded.emergency_contact_name,
                    emergency_contact_phone = excluded.emergency_contact_phone,
                    notes = excluded.notes,
                    intake_status = excluded.intake_status,
                    updated_at = excluded.updated_at
                """,
                (
                    intake.call_sid,
                    merged.get("full_name"),
                    merged.get("date_of_birth"),
                    merged.get("phone_number"),
                    merged.get("email"),
                    merged.get("reason_for_visit"),
                    merged.get("chief_complaint"),
                    merged.get("symptoms"),
                    merged.get("allergies"),
                    merged.get("current_medications"),
                    merged.get("insurance_provider"),
                    merged.get("insurance_member_id"),
                    merged.get("preferred_appointment"),
                    merged.get("emergency_contact_name"),
                    merged.get("emergency_contact_phone"),
                    merged.get("notes"),
                    merged.get("intake_status", "not_started"),
                    merged.get("created_at", now),
                    merged.get("updated_at", now),
                ),
            )
        return self.get_patient_intake(intake.call_sid) or intake

    def get_patient_intake(self, call_sid: str) -> PatientIntakeRecord | None:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM patient_intakes WHERE call_sid = ?", (call_sid,)).fetchone()
        if row is None:
            return None
        return PatientIntakeRecord(
            call_sid=row["call_sid"],
            full_name=row["full_name"],
            date_of_birth=row["date_of_birth"],
            phone_number=row["phone_number"],
            email=row["email"],
            reason_for_visit=row["reason_for_visit"],
            chief_complaint=row["chief_complaint"],
            symptoms=row["symptoms"],
            allergies=row["allergies"],
            current_medications=row["current_medications"],
            insurance_provider=row["insurance_provider"],
            insurance_member_id=row["insurance_member_id"],
            preferred_appointment=row["preferred_appointment"],
            emergency_contact_name=row["emergency_contact_name"],
            emergency_contact_phone=row["emergency_contact_phone"],
            notes=row["notes"],
            intake_status=row["intake_status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_patient_intakes(self, limit: int = 50) -> list[PatientIntakeRecord]:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT call_sid FROM patient_intakes ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        intakes = [self.get_patient_intake(row["call_sid"]) for row in rows]
        return [item for item in intakes if item is not None]

    def delete_call(self, sid: str) -> bool:
        with _connect() as conn:
            existing = conn.execute("SELECT sid FROM calls WHERE sid = ?", (sid,)).fetchone()
            if existing is None:
                return False
            conn.execute("DELETE FROM call_events WHERE call_sid = ?", (sid,))
            conn.execute("DELETE FROM patient_intakes WHERE call_sid = ?", (sid,))
            conn.execute("DELETE FROM calls WHERE sid = ?", (sid,))
        return True

    def dashboard_stats(self) -> dict[str, int]:
        today = datetime.now(timezone.utc).date().isoformat()
        with _connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM calls WHERE date(created_at) = date(?)",
                (today,),
            ).fetchone()["c"]
            demo = conn.execute(
                "SELECT COUNT(*) AS c FROM calls WHERE call_type = 'demo' AND date(created_at) = date(?)",
                (today,),
            ).fetchone()["c"]
            complete = conn.execute(
                "SELECT COUNT(*) AS c FROM patient_intakes WHERE intake_status = 'complete'",
            ).fetchone()["c"]
            in_progress = conn.execute(
                "SELECT COUNT(*) AS c FROM patient_intakes WHERE intake_status = 'in_progress'",
            ).fetchone()["c"]
        return {
            "total_calls_today": total,
            "demo_calls_today": demo,
            "intakes_complete": complete,
            "intakes_in_progress": in_progress,
        }

    @staticmethod
    def _row_to_call(row, events, intake_row) -> CallRecord:
        intake = None
        if intake_row is not None:
            intake = PatientIntakeRecord(
                call_sid=intake_row["call_sid"],
                full_name=intake_row["full_name"],
                date_of_birth=intake_row["date_of_birth"],
                phone_number=intake_row["phone_number"],
                email=intake_row["email"],
                reason_for_visit=intake_row["reason_for_visit"],
                chief_complaint=intake_row["chief_complaint"],
                symptoms=intake_row["symptoms"],
                allergies=intake_row["allergies"],
                current_medications=intake_row["current_medications"],
                insurance_provider=intake_row["insurance_provider"],
                insurance_member_id=intake_row["insurance_member_id"],
                preferred_appointment=intake_row["preferred_appointment"],
                emergency_contact_name=intake_row["emergency_contact_name"],
                emergency_contact_phone=intake_row["emergency_contact_phone"],
                notes=intake_row["notes"],
                intake_status=intake_row["intake_status"],
                created_at=datetime.fromisoformat(intake_row["created_at"]),
                updated_at=datetime.fromisoformat(intake_row["updated_at"]),
            )
        return CallRecord(
            sid=row["sid"],
            direction=row["direction"],
            status=row["status"],
            call_type=row["call_type"],
            scenario_id=row["scenario_id"],
            from_number=row["from_number"],
            to_number=row["to_number"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            events=[
                CallEvent(
                    type=event["event_type"],
                    text=event["text"],
                    timestamp=datetime.fromisoformat(event["timestamp"]),
                )
                for event in events
            ],
            patient_intake=intake,
        )
