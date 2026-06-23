from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


EventType = Literal["system", "status", "user", "assistant", "error"]


class CallEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EventType
    text: str


IntakeStatus = Literal["not_started", "in_progress", "complete"]


class PatientIntakeRecord(BaseModel):
    call_sid: str
    full_name: str | None = None
    date_of_birth: str | None = None
    phone_number: str | None = None
    email: str | None = None
    reason_for_visit: str | None = None
    chief_complaint: str | None = None
    symptoms: str | None = None
    allergies: str | None = None
    current_medications: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_appointment: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    notes: str | None = None
    intake_status: IntakeStatus = "not_started"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CallRecord(BaseModel):
    sid: str
    direction: str = "inbound"
    status: str = "created"
    call_type: str = "phone"
    scenario_id: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[CallEvent] = Field(default_factory=list)
    patient_intake: PatientIntakeRecord | None = None


class DemoCallRequest(BaseModel):
    scenario_id: str | None = None
    system_prompt: str | None = None


class DashboardStats(BaseModel):
    total_calls_today: int = 0
    intakes_complete: int = 0
    intakes_in_progress: int = 0
    demo_calls_today: int = 0


class ChatRequest(BaseModel):
    message: str
    system_prompt: str | None = None
    scenario_id: str | None = None


class ChatResponse(BaseModel):
    answer: str


class OutboundCallRequest(BaseModel):
    to_number: str
    system_prompt: str | None = None
    scenario_id: str | None = None
