from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from app.config import BASE_DIR


DB_PATH = BASE_DIR / "medory.db"
_lock = Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS calls (
                sid TEXT PRIMARY KEY,
                direction TEXT NOT NULL DEFAULT 'inbound',
                status TEXT NOT NULL DEFAULT 'created',
                call_type TEXT NOT NULL DEFAULT 'phone',
                scenario_id TEXT,
                from_number TEXT,
                to_number TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS call_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_sid TEXT NOT NULL,
                event_type TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (call_sid) REFERENCES calls(sid)
            );

            CREATE TABLE IF NOT EXISTS patient_intakes (
                call_sid TEXT PRIMARY KEY,
                full_name TEXT,
                date_of_birth TEXT,
                phone_number TEXT,
                email TEXT,
                reason_for_visit TEXT,
                chief_complaint TEXT,
                symptoms TEXT,
                allergies TEXT,
                current_medications TEXT,
                insurance_provider TEXT,
                insurance_member_id TEXT,
                preferred_appointment TEXT,
                emergency_contact_name TEXT,
                emergency_contact_phone TEXT,
                notes TEXT,
                intake_status TEXT NOT NULL DEFAULT 'not_started',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (call_sid) REFERENCES calls(sid)
            );

            CREATE INDEX IF NOT EXISTS idx_call_events_sid ON call_events(call_sid);
            CREATE INDEX IF NOT EXISTS idx_calls_updated ON calls(updated_at);
            """
        )


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def with_db_lock(func):
    def wrapper(*args, **kwargs):
        with _lock:
            return func(*args, **kwargs)

    return wrapper
