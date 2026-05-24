from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


EventType = Literal["system", "status", "user", "assistant", "error"]


class CallEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EventType
    text: str


class CallRecord(BaseModel):
    sid: str
    direction: str = "inbound"
    status: str = "created"
    from_number: str | None = None
    to_number: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[CallEvent] = Field(default_factory=list)


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
