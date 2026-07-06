from __future__ import annotations

import asyncio
import base64
import json
import logging
from uuid import uuid4

from fastapi import WebSocket
from elevenlabs.conversational_ai.conversation import Conversation

from app.config import Settings, normalize_patient_transcript
from app.models import PatientIntakeRecord
from app.services.agent_response_sanitizer import sanitize_agent_spoken_text
from app.services.elevenlabs_agent import (
    build_conversation_config,
    build_elevenlabs_client,
    format_elevenlabs_error,
)
from app.services.patient_intake_service import PatientIntakeExtractor
from app.store import CallStore


logger = logging.getLogger(__name__)


class DemoCallSession:
    def __init__(
        self,
        *,
        settings: Settings,
        call_store: CallStore,
        intake_extractor: PatientIntakeExtractor | None,
        call_sid: str,
        system_prompt: str,
        scenario_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._call_store = call_store
        self._intake_extractor = intake_extractor
        self._websocket: WebSocket | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._system_prompt = system_prompt
        self._scenario_id = scenario_id
        self._history: list[dict[str, str]] = []
        self._closed = False
        self.call_sid = call_sid

        self._call_store.ensure_call(
            call_sid,
            direction="demo",
            call_type="demo",
            scenario_id=scenario_id,
        )

        self._audio_interface: BrowserAudioInterface | None = None
        self._conversation: Conversation | None = None

    async def attach(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def start(self) -> None:
        if self._conversation is not None or self._websocket is None:
            return

        if not self._settings.elevenlabs_agent_id:
            await self._send_error("ELEVENLABS_AGENT_ID is not configured.")
            return

        self._loop = asyncio.get_running_loop()
        self._call_store.update_status(self.call_sid, "connected")
        self._call_store.add_event(self.call_sid, "status", "Demo call session starting.")
        if self._settings.hardcoded_patient_name:
            self._call_store.upsert_patient_intake(
                PatientIntakeRecord(
                    call_sid=self.call_sid,
                    full_name=self._settings.hardcoded_patient_name,
                    intake_status="in_progress",
                )
            )

        self._audio_interface = BrowserAudioInterface(self._websocket, self._loop)
        client = build_elevenlabs_client(self._settings)
        config = build_conversation_config(
            self._settings,
            system_prompt=self._system_prompt,
            first_message=self._settings.agent_greeting,
            client=client,
        )

        self._conversation = Conversation(
            client=client,
            agent_id=self._settings.elevenlabs_agent_id,
            requires_auth=True,
            audio_interface=self._audio_interface,
            config=config,
            callback_agent_response=self._on_agent_response,
            callback_user_transcript=self._on_user_transcript,
        )
        try:
            self._conversation.start_session()
        except Exception as exc:
            message = format_elevenlabs_error(exc)
            self._conversation = None
            await self._send_error(message)
            raise RuntimeError(message) from exc

        await self._ensure_opening_greeting()
        await self._audio_interface.send_status("connected", "Demo agent ready. Speak into your microphone.")
        self._call_store.add_event(self.call_sid, "system", "ElevenLabs demo session active.")

    async def handle_message(self, payload: dict) -> None:
        if self._closed:
            return

        message_type = payload.get("type")
        if message_type == "start":
            await self.start()
            return

        if message_type == "audio" and self._audio_interface is not None:
            audio_bytes = base64.b64decode(payload.get("payload", ""))
            await self._audio_interface.handle_browser_audio(audio_bytes)
            return

        if message_type == "user_text" and self._conversation is not None:
            text = str(payload.get("text", "")).strip()
            if text:
                self._conversation.send_user_message(text)
                await self._record_user_transcript(text)

    def _on_agent_response(self, text: str) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._record_agent_response(text), self._loop)

    def _on_user_transcript(self, text: str) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._record_user_transcript(text), self._loop)

    async def _ensure_opening_greeting(self) -> None:
        greeting = self._settings.agent_greeting.strip()
        if not greeting:
            return
        await asyncio.sleep(1.5)
        if any(message.get("role") == "assistant" for message in self._history):
            return
        await self._record_agent_response(greeting)

    async def _record_agent_response(self, text: str) -> None:
        if self._closed or not text.strip():
            return
        normalized = sanitize_agent_spoken_text(text)
        if not normalized:
            return
        if self._history and self._history[-1].get("content") == normalized:
            return
        self._history.append({"role": "assistant", "content": normalized})
        self._call_store.add_event(self.call_sid, "assistant", normalized)
        if self._audio_interface:
            await self._audio_interface.send_transcript("assistant", normalized)

    async def _record_user_transcript(self, text: str) -> None:
        if self._closed or not text.strip():
            return
        display_text = normalize_patient_transcript(
            text.strip(),
            self._settings.hardcoded_patient_name,
        )
        self._history.append({"role": "user", "content": display_text})
        self._call_store.add_event(self.call_sid, "user", display_text)
        if self._audio_interface:
            await self._audio_interface.send_transcript("user", display_text)
        await self._capture_patient_intake(display_text)

    async def _capture_patient_intake(self, latest_user_text: str) -> None:
        if self._intake_extractor is None:
            return
        try:
            current = self._call_store.get_patient_intake(self.call_sid)
            intake = await self._intake_extractor.extract_intake(
                call_sid=self.call_sid,
                history=self._history,
                latest_user_text=latest_user_text,
                current=current,
            )
            saved = self._call_store.upsert_patient_intake(intake)
            self._call_store.add_event(
                self.call_sid,
                "system",
                f"Patient intake updated ({saved.intake_status}).",
            )
            if self._audio_interface:
                await self._audio_interface.send_transcript(
                    "system",
                    f"Intake saved: {saved.full_name or 'in progress'}",
                )
        except Exception as exc:
            self._call_store.add_event(self.call_sid, "error", f"Intake extraction error: {exc}")

    async def _send_error(self, message: str) -> None:
        self._call_store.add_event(self.call_sid, "error", message)
        if self._websocket is None:
            return
        try:
            await self._websocket.send_text(
                json.dumps({"type": "status", "status": "error", "detail": message})
            )
        except Exception:
            pass

    async def close(self, status: str = "completed") -> None:
        if self._closed:
            return
        if self._history:
            await self._capture_patient_intake("")
        self._closed = True
        if self._conversation is not None:
            try:
                self._conversation.end_session()
                self._conversation.wait_for_session_end()
            except Exception as exc:
                if "not started" not in str(exc).lower():
                    logger.warning("Error ending demo session: %s", exc)
            finally:
                self._conversation = None
        self._audio_interface = None
        self._call_store.update_status(self.call_sid, status)
        self._call_store.add_event(self.call_sid, "status", f"Demo call closed with status: {status}")


def new_demo_call_sid() -> str:
    return f"demo_{uuid4().hex[:12]}"
