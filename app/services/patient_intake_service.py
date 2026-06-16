from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.models import PatientIntakeRecord


EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured patient intake data from phone call transcripts. "
    "Return only valid JSON matching the requested schema. "
    "Use null for fields not yet mentioned. Do not invent information. "
    "If the caller corrected a field, use the latest value."
)


class PatientIntakeExtractor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    @staticmethod
    def _build_transcript(history: list[dict[str, str]], latest_user_text: str | None = None) -> str:
        lines: list[str] = []
        for message in history:
            role = message.get("role", "unknown")
            content = message.get("content", "").strip()
            if content:
                lines.append(f"{role}: {content}")
        if latest_user_text and latest_user_text.strip():
            lines.append(f"user: {latest_user_text.strip()}")
        return "\n".join(lines)

    @staticmethod
    def _merge_intake(
        current: PatientIntakeRecord | None,
        extracted: dict[str, Any],
    ) -> PatientIntakeRecord:
        base = current.model_dump() if current else PatientIntakeRecord(call_sid=extracted.get("call_sid", "")).model_dump()
        for field_name, value in extracted.items():
            if field_name in {"call_sid", "created_at", "updated_at", "intake_status"}:
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            base[field_name] = value.strip() if isinstance(value, str) else value

        required_fields = (
            "full_name",
            "date_of_birth",
            "phone_number",
            "reason_for_visit",
        )
        tracked_fields = (
            "full_name",
            "date_of_birth",
            "phone_number",
            "email",
            "reason_for_visit",
            "chief_complaint",
            "symptoms",
            "allergies",
            "current_medications",
            "insurance_provider",
            "insurance_member_id",
            "preferred_appointment",
            "emergency_contact_name",
            "emergency_contact_phone",
            "notes",
        )
        if all(base.get(field) for field in required_fields):
            base["intake_status"] = "complete"
        elif any(base.get(field) for field in tracked_fields):
            base["intake_status"] = "in_progress"

        return PatientIntakeRecord.model_validate(base)

    async def extract_intake(
        self,
        *,
        call_sid: str,
        history: list[dict[str, str]],
        latest_user_text: str | None = None,
        current: PatientIntakeRecord | None = None,
    ) -> PatientIntakeRecord:
        transcript = self._build_transcript(history, latest_user_text)
        if not transcript.strip():
            return current or PatientIntakeRecord(call_sid=call_sid)

        schema_hint = {
            "full_name": "string or null",
            "date_of_birth": "string or null",
            "phone_number": "string or null",
            "email": "string or null",
            "reason_for_visit": "string or null",
            "chief_complaint": "string or null",
            "symptoms": "string or null",
            "allergies": "string or null",
            "current_medications": "string or null",
            "insurance_provider": "string or null",
            "insurance_member_id": "string or null",
            "preferred_appointment": "string or null",
            "emergency_contact_name": "string or null",
            "emergency_contact_phone": "string or null",
            "notes": "string or null",
        }

        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Extract patient intake fields from this call transcript.\n\n"
                    f"Current partial record:\n{json.dumps(current.model_dump(mode='json') if current else {}, indent=2)}\n\n"
                    f"Transcript:\n{transcript}\n\n"
                    f"Return JSON with these keys only: {json.dumps(schema_hint)}"
                ),
            },
        ]

        completion = await self._client.chat.completions.create(
            model=self._settings.openai_model,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=500,
        )

        content = completion.choices[0].message.content or "{}"
        try:
            extracted = json.loads(content)
        except json.JSONDecodeError:
            extracted = {}

        merged = self._merge_intake(current, extracted)
        return merged.model_copy(update={"call_sid": call_sid})
