from __future__ import annotations

import re

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import ConversationInitiationData

from app.config import Settings, hardcoded_patient_context, resolved_agent_prompt


def build_elevenlabs_client(settings: Settings) -> ElevenLabs:
    return ElevenLabs(api_key=settings.elevenlabs_api_key)


def format_elevenlabs_error(exc: Exception) -> str:
    text = str(exc)
    match = re.search(r"'message': '([^']+)'", text)
    if match:
        return match.group(1)
    if "document_not_found" in text or "not_found" in text:
        return (
            "ElevenLabs agent not found for this API key. "
            "Update ELEVENLABS_AGENT_ID in .env to an agent from your ElevenLabs account."
        )
    if "invalid_api_key" in text or "401" in text:
        return "Invalid ElevenLabs API key. Check ELEVENLABS_API_KEY in .env."
    return text[:280]


def verify_elevenlabs_agent(settings: Settings, client: ElevenLabs | None = None) -> dict[str, object]:
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        return {
            "valid": False,
            "agent_id": settings.elevenlabs_agent_id,
            "message": "ELEVENLABS_API_KEY or ELEVENLABS_AGENT_ID is not set.",
            "available_agents": [],
        }

    api_client = client or build_elevenlabs_client(settings)
    available: list[dict[str, str]] = []
    try:
        response = api_client.conversational_ai.agents.list()
        agents = getattr(response, "agents", response) or []
        for agent in agents:
            agent_id = getattr(agent, "agent_id", None) or getattr(agent, "id", "")
            name = getattr(agent, "name", "Unnamed agent")
            if agent_id:
                available.append({"agent_id": str(agent_id), "name": str(name)})
    except Exception as exc:
        return {
            "valid": False,
            "agent_id": settings.elevenlabs_agent_id,
            "message": format_elevenlabs_error(exc),
            "available_agents": [],
        }

    configured = settings.elevenlabs_agent_id
    if any(item["agent_id"] == configured for item in available):
        label = next(item["name"] for item in available if item["agent_id"] == configured)
        return {
            "valid": True,
            "agent_id": configured,
            "agent_name": label,
            "message": "Agent is available for this API key.",
            "available_agents": available,
        }

    return {
        "valid": False,
        "agent_id": configured,
        "message": (
            f"Configured agent '{configured}' was not found for this API key. "
            "Use one of the available agent IDs listed in /api/health."
        ),
        "available_agents": available,
    }


def build_conversation_config(
    settings: Settings,
    *,
    system_prompt: str | None,
    first_message: str | None,
    client: ElevenLabs | None = None,
) -> ConversationInitiationData | None:
    permissions = agent_override_permissions(settings, client)
    agent_override: dict[str, object] = {}
    resolved_prompt = resolved_agent_prompt(settings, system_prompt)
    resolved_greeting = (first_message or settings.agent_greeting).strip()

    if settings.elevenlabs_override_first_message and permissions["first_message"]:
        agent_override["first_message"] = resolved_greeting

    if settings.elevenlabs_override_prompt and permissions["prompt"]:
        agent_override["prompt"] = {"prompt": resolved_prompt}

    if not agent_override:
        return None

    return ConversationInitiationData(
        conversation_config_override={"agent": agent_override},
    )


# Sarah — mature, reassuring American voice (consistent for medical intake calls)
DEFAULT_ENGLISH_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
FALLBACK_ENGLISH_VOICE_ID = "hpp4J3VqNfWAUOO0d1Us"
DEFAULT_ENGLISH_TTS_MODEL = "eleven_turbo_v2"
CONSISTENT_TTS_SETTINGS = {
    "stability": 0.62,
    "similarity_boost": 0.88,
    "speed": 0.96,
}


def resolve_voice_id(settings: Settings, client: ElevenLabs | None = None) -> tuple[str, str | None]:
    """Return a voice ID that exists for this API key, with an optional warning."""
    requested = (settings.elevenlabs_voice_id or DEFAULT_ENGLISH_VOICE_ID).strip()
    api_client = client or build_elevenlabs_client(settings)
    try:
        voices = api_client.voices.get_all()
        available = {voice.voice_id for voice in (getattr(voices, "voices", voices) or [])}
    except Exception:
        return requested, None

    if requested in available:
        return requested, None

    if FALLBACK_ENGLISH_VOICE_ID in available:
        return (
            FALLBACK_ENGLISH_VOICE_ID,
            f"Voice '{requested}' was not found. Using fallback voice '{FALLBACK_ENGLISH_VOICE_ID}'.",
        )

    if DEFAULT_ENGLISH_VOICE_ID in available:
        return (
            DEFAULT_ENGLISH_VOICE_ID,
            f"Voice '{requested}' was not found. Using default voice '{DEFAULT_ENGLISH_VOICE_ID}'.",
        )

    first_voice = next(iter(available), requested)
    return first_voice, f"Voice '{requested}' was not found. Using '{first_voice}'."


def agent_override_permissions(
    settings: Settings,
    client: ElevenLabs | None = None,
) -> dict[str, bool]:
    """Read which conversation overrides the ElevenLabs agent allows from the dashboard."""
    if not settings.elevenlabs_agent_id:
        return {"prompt": False, "first_message": False}

    api_client = client or build_elevenlabs_client(settings)
    try:
        agent = api_client.conversational_ai.agents.get(agent_id=settings.elevenlabs_agent_id)
        platform_settings = getattr(agent, "platform_settings", None)
        overrides = getattr(platform_settings, "overrides", None)
        config_override = getattr(overrides, "conversation_config_override", None)
        agent_override = getattr(config_override, "agent", None)
        prompt_override = getattr(agent_override, "prompt", None)
        return {
            "prompt": bool(getattr(prompt_override, "prompt", False)),
            "first_message": bool(getattr(agent_override, "first_message", False)),
        }
    except Exception:
        return {"prompt": False, "first_message": False}


def sync_medory_agent_profile(
    settings: Settings,
    client: ElevenLabs | None = None,
    *,
    system_prompt: str | None = None,
    first_message: str | None = None,
) -> dict[str, object]:
    """Push English Medory medical-intake prompt/greeting to the configured ElevenLabs agent."""
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        return {"synced": False, "message": "ElevenLabs is not configured."}

    api_client = client or build_elevenlabs_client(settings)
    resolved_prompt = resolved_agent_prompt(settings, system_prompt or settings.agent_system_prompt)
    resolved_greeting = (first_message or settings.agent_greeting).strip()
    voice_id, voice_warning = resolve_voice_id(settings, api_client)
    tts_model = settings.elevenlabs_tts_model or DEFAULT_ENGLISH_TTS_MODEL

    try:
        api_client.conversational_ai.agents.update(
            agent_id=settings.elevenlabs_agent_id,
            conversation_config={
                "agent": {
                    "language": "en",
                    "first_message": resolved_greeting,
                    "prompt": {"prompt": resolved_prompt},
                },
                "tts": {
                    "model_id": tts_model,
                    "voice_id": voice_id,
                    "supported_voices": [],
                    **CONSISTENT_TTS_SETTINGS,
                },
                "language_presets": {},
            },
        )
    except Exception as exc:
        return {
            "synced": False,
            "message": format_elevenlabs_error(exc),
        }

    result = {
        "synced": True,
        "message": "ElevenLabs agent synced with a single locked English voice.",
        "language": "en",
        "agent_id": settings.elevenlabs_agent_id,
        "voice_id": voice_id,
        "tts_model": tts_model,
    }
    if voice_warning:
        result["voice_warning"] = voice_warning
    return result
