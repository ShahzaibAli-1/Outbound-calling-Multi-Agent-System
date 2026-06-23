from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.models import PatientIntakeRecord


EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured patient intake data from phone call transcripts. "
    "Return only valid JSON matching the requested schema. "
    "Use null for fields not yet mentioned. Do not invent information. "
    "If the caller corrected a field, use the latest value. "
    "If full_name is already on file for this patient, keep that exact value and do not change it from transcript guesses."
)

INTAKE_FIELD_ORDER: list[tuple[str, str]] = [
    ("full_name", "full legal name"),
    ("date_of_birth", "date of birth"),
    ("phone_number", "callback phone number"),
    ("reason_for_visit", "reason for visit or chief complaint"),
    ("symptoms", "current symptoms and how long they have been present"),
    ("allergies", "known allergies"),
    ("current_medications", "current medications"),
    ("insurance_provider", "insurance provider"),
    ("insurance_member_id", "insurance member ID"),
    ("preferred_appointment", "preferred appointment date or time"),
    ("email", "email address"),
    ("emergency_contact_name", "emergency contact name"),
    ("emergency_contact_phone", "emergency contact phone"),
]


_NAME_CORRECTION_PATTERN = re.compile(
    r"\b(?:actually|correction|correct spelling|i said|it's|it is|not)\b",
    re.IGNORECASE,
)
_SPACED_LETTERS_PATTERN = re.compile(
    r"\b(?:[A-Za-z]\s+){2,}[A-Za-z]\b",
)
_HYPHENATED_SPELLING_PATTERN = re.compile(
    r"\b(?:[A-Za-z]-){2,}[A-Za-z]\b",
)


def _title_case_name(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split())


def _name_from_spelling(text: str) -> str | None:
    hyphenated = _HYPHENATED_SPELLING_PATTERN.findall(text)
    if hyphenated:
        parts = [_title_case_name(item.replace("-", "")) for item in hyphenated]
        if parts:
            return " ".join(parts)

    spaced = _SPACED_LETTERS_PATTERN.findall(text)
    if spaced:
        words: list[str] = []
        for chunk in spaced:
            letters = re.findall(r"[A-Za-z]", chunk)
            if len(letters) >= 3:
                words.append(_title_case_name("".join(letters)))
        if words:
            return " ".join(words)
    return None


def _extract_name_hint(history: list[dict[str, str]], latest_user_text: str | None = None) -> str | None:
    user_lines = [
        message.get("content", "").strip()
        for message in history
        if message.get("role") == "user" and message.get("content", "").strip()
    ]
    if latest_user_text and latest_user_text.strip():
        user_lines.append(latest_user_text.strip())

    for line in reversed(user_lines):
        spelled = _name_from_spelling(line)
        if spelled:
            return spelled

    for line in reversed(user_lines):
        correction = re.search(
            r"(?i)(?:actually(?:\s+it(?:'s| is))?|correction|correct spelling|i said|it is|it's)\s+"
            r"([A-Za-z][A-Za-z' -]{1,60}?)(?:\s+not\b|\s*$)",
            line,
        )
        if correction:
            candidate = correction.group(1).strip(" .,-")
            if candidate and len(candidate.split()) <= 6:
                return _title_case_name(candidate)

    for line in reversed(user_lines):
        if _NAME_CORRECTION_PATTERN.search(line):
            cleaned = re.sub(
                r"(?i)\b(?:actually|correction|correct spelling|i said|it's|it is|not|my name is)\b",
                " ",
                line,
            )
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
            if cleaned and len(cleaned.split()) <= 6:
                return _title_case_name(cleaned)

    for line in reversed(user_lines):
        match = re.search(
            r"(?i)(?:my name is|this is|i am|i'm)\s+([A-Za-z][A-Za-z' -]{1,60})",
            line,
        )
        if match:
            candidate = match.group(1).strip(" .,-")
            if candidate and len(candidate.split()) <= 6:
                return _title_case_name(candidate)
    return None


def build_intake_progress_context(intake: PatientIntakeRecord | None) -> str:
    collected_lines: list[str] = []
    next_label: str | None = None

    for field_name, label in INTAKE_FIELD_ORDER:
        value = getattr(intake, field_name, None) if intake else None
        if value:
            collected_lines.append(f"- {label}: {value}")
        elif next_label is None:
            next_label = label

    lines = [
        "## Current Intake Progress",
        "Already collected (do NOT ask for these again):",
    ]
    lines.extend(collected_lines or ["- None yet"])
    lines.append("")
    if next_label:
        lines.extend(
            [
                f"Ask about ONLY the next missing item: {next_label}.",
                "Do not repeat earlier questions. Acknowledge the caller's last answer briefly, then move on.",
            ]
        )
    else:
        lines.append(
            "Standard intake fields are complete. Briefly summarize what you captured and ask if anything needs correction."
        )

    return "\n".join(lines)


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
        name_hint = _extract_name_hint(history, latest_user_text)
        if name_hint:
            merged = merged.model_copy(update={"full_name": name_hint})
        if self._settings.hardcoded_patient_name:
            merged = merged.model_copy(update={"full_name": self._settings.hardcoded_patient_name})
        return merged.model_copy(update={"call_sid": call_sid})
