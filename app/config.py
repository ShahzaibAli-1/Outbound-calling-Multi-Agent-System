from __future__ import annotations

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
    "words. Do not use markdown, bullet points, numbered lists, or long monologues. Confirm names, dates, and "
    "IDs by repeating them back. Never diagnose or prescribe."
)


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
            "Hello, this is the clinic intake line. I can help register you and collect a few details. "
            "May I start with your full name?"
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

    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    deepgram_api_key: str = Field(alias="DEEPGRAM_API_KEY")
    deepgram_stt_model: str = Field(default="nova-2-phonecall", alias="DEEPGRAM_STT_MODEL")
    deepgram_tts_model: str = Field(default="aura-2-thalia-en", alias="DEEPGRAM_TTS_MODEL")

    twilio_account_sid: str = Field(alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(alias="TWILIO_AUTH_TOKEN")
    twilio_phone_number: str = Field(alias="TWILIO_PHONE_NUMBER")

    agent_name: str = Field(default_factory=default_agent_name, alias="AGENT_NAME")
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
