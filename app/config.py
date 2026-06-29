from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_FILE = BASE_DIR / "test_system_prompt.txt"

VOICE_REPLY_GUIDANCE = (
    "You are speaking on a live phone call for medical patient intake. Answer the caller's latest question "
    "directly first, then ask at most one short follow-up intake question. Keep every reply extremely short "
    "so it can be spoken almost immediately. Use no more than two short sentences and prefer fewer than thirty "
    "words. Do not use markdown, bullet points, numbered lists, or long monologues. "
    "Always speak in English only with the same calm professional tone throughout the entire call. "
    "If the patient name is already on file, never ask for their name or spelling. "
    "Confirm dates and IDs by repeating them back once, then move to the next missing field. "
    "Never re-ask for information already listed under Current Intake Progress. Never diagnose or prescribe."
)


def hardcoded_patient_context(patient_name: str | None) -> str:
    if not patient_name or not patient_name.strip():
        return ""
    name = patient_name.strip()
    return (
        f"\n\n## Pre-identified patient\n"
        f"The patient's full legal name is already on file as {name}. "
        "Do NOT ask for their name, spelling, name confirmation, or how to spell any part of their name. "
        "If the caller says a different name, ignore it and continue intake using the on-file name only. "
        "Your first question after greeting must be date of birth, reason for visit, or the next missing intake field."
    )


_NAME_TRANSCRIPT_PATTERNS = (
    re.compile(r"(?i)\bmy (?:full )?(?:legal )?name\b"),
    re.compile(r"(?i)\bfull legal name\b"),
    re.compile(r"(?i)\b(?:first|last) name is\b"),
    re.compile(r"(?i)\bspell(?:ing)?\b"),
    re.compile(r"(?i)\bletter by letter\b"),
    re.compile(r"(?i)\bcorrect spelling\b"),
    re.compile(r"(?i)^(?:[a-z]\s+){2,}[a-z]\s*$"),
)


def should_normalize_patient_name_transcript(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    return any(pattern.search(cleaned) for pattern in _NAME_TRANSCRIPT_PATTERNS)


def normalize_patient_transcript(text: str, patient_name: str | None) -> str:
    cleaned = text.strip()
    if not patient_name or not cleaned:
        return cleaned
    if should_normalize_patient_name_transcript(cleaned):
        return f"My full legal name is {patient_name.strip()}."
    return cleaned


def resolved_agent_prompt(settings: "Settings", system_prompt: str | None = None) -> str:
    base = (system_prompt or settings.agent_system_prompt).strip()
    return f"{base}{hardcoded_patient_context(settings.hardcoded_patient_name)}"


def compose_voice_prompt(prompt_text: str) -> str:
    normalized_prompt = prompt_text.strip()
    if not normalized_prompt:
        return VOICE_REPLY_GUIDANCE
    return f"{VOICE_REPLY_GUIDANCE}\n\n{normalized_prompt}"


@lru_cache(maxsize=1)
def load_prompt_file() -> str | None:
    if not PROMPT_FILE.exists():
        return None

    prompt_text = PROMPT_FILE.read_text(encoding="utf-8").strip()
    return prompt_text or None


def default_agent_name() -> str:
    prompt_text = (load_prompt_file() or "").lower()
    if "medical intake" in prompt_text or "healthcare clinic" in prompt_text:
        return "Clinic Intake Assistant"
    return "Voice Agent"


def default_agent_greeting() -> str:
    prompt_text = (load_prompt_file() or "").lower()
    if "medical intake" in prompt_text or "healthcare clinic" in prompt_text:
        return (
            "Hello, this is Medory Call Center. I have you on file as Shahzaib Ali Khan. "
            "What is your date of birth?"
        )
    return "Hello, thank you for taking my call. How can I help you today?"


def default_agent_system_prompt() -> str:
    prompt_text = load_prompt_file()
    if prompt_text:
        return compose_voice_prompt(prompt_text)

    return compose_voice_prompt(
        "You are a phone-based AI assistant. "
        "Give concise, useful answers that sound natural when spoken aloud. "
        "Ask a short follow-up question if you need clarification. "
        "Do not mention internal tools, policies, or that you are reading text."
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    port: int = Field(default=3000, alias="PORT")
    public_base_url: str = Field(default="http://localhost:3000", alias="PUBLIC_BASE_URL")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    elevenlabs_api_key: str = Field(alias="ELEVENLABS_API_KEY")
    elevenlabs_agent_id: str = Field(default="", alias="ELEVENLABS_AGENT_ID")
    elevenlabs_agent_phone_number_id: str | None = Field(
        default=None,
        alias="ELEVENLABS_AGENT_PHONE_NUMBER_ID",
    )
    elevenlabs_override_prompt: bool = Field(default=False, alias="ELEVENLABS_OVERRIDE_PROMPT")
    elevenlabs_override_first_message: bool = Field(
        default=False,
        alias="ELEVENLABS_OVERRIDE_FIRST_MESSAGE",
    )
    elevenlabs_voice_id: str = Field(
        default="",
        alias="ELEVENLABS_VOICE_ID",
    )
    elevenlabs_tts_model: str = Field(
        default="eleven_turbo_v2",
        alias="ELEVENLABS_TTS_MODEL",
    )

    twilio_account_sid: str = Field(alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(alias="TWILIO_AUTH_TOKEN")
    twilio_phone_number: str = Field(alias="TWILIO_PHONE_NUMBER")

    agent_name: str = Field(default_factory=default_agent_name, alias="AGENT_NAME")
    staff_name: str = Field(default="Shahzaib", alias="STAFF_NAME")
    hardcoded_patient_name: str | None = Field(
        default="Shahzaib Ali Khan",
        alias="HARDCODED_PATIENT_NAME",
    )
    agent_greeting: str = Field(
        default_factory=default_agent_greeting,
        alias="AGENT_GREETING",
    )
    agent_system_prompt: str = Field(
        default_factory=default_agent_system_prompt,
        alias="AGENT_SYSTEM_PROMPT",
    )

    @property
    def websocket_base_url(self) -> str:
        parsed = urlparse(self.public_base_url.rstrip("/"))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, parsed.path, "", "", ""))

    @property
    def voice_webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/api/twilio/voice"

    @property
    def status_callback_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/api/twilio/status"

    @property
    def media_stream_url(self) -> str:
        return f"{self.websocket_base_url.rstrip('/')}/ws/twilio-media"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
