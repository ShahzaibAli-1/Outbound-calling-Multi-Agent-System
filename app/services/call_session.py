from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import WebSocket
from elevenlabs.conversational_ai.conversation import Conversation

from app.config import Settings, normalize_patient_transcript
from app.services.elevenlabs_agent import build_conversation_config, build_elevenlabs_client
from app.services.patient_intake_service import PatientIntakeExtractor
from app.services.twilio_audio_interface import TwilioAudioInterface
from app.store import CallStore


logger = logging.getLogger(__name__)


class CallSession:
    def __init__(
        self,
        *,
        settings: Settings,
        call_store: CallStore,
        intake_extractor: PatientIntakeExtractor | None,
        call_sid: str,
        from_number: str | None,
        to_number: str | None,
        direction: str,
        system_prompt: str | None = None,
        first_message: str | None = None,
        twilio_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._call_store = call_store
        self._intake_extractor = intake_extractor
        self._twilio_client = twilio_client
        self._websocket: WebSocket | None = None
        self._loop = asyncio.get_event_loop()
        self._system_prompt = system_prompt or settings.agent_system_prompt
        self._first_message = (first_message or settings.agent_greeting).strip()
        self._history: list[dict[str, str]] = []
        self._closed = False
        self._end_watcher: threading.Thread | None = None

        self.call_sid = call_sid
        self._call_store.ensure_call(
            call_sid,
            from_number=from_number,
            to_number=to_number,
            direction=direction,
        )

        self._audio_interface: TwilioAudioInterface | None = None
        self._conversation: Conversation | None = None

    async def attach(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def handle_twilio_message(self, payload: dict[str, Any]) -> None:
        if self._closed or self._websocket is None:
            return

        event_type = payload.get("event")
        if event_type == "start":
            await self._start_conversation(payload)
            return

        if self._audio_interface is not None:
            await self._audio_interface.handle_twilio_message(payload)

        if event_type == "stop":
            await self.close(status="completed")

    async def _start_conversation(self, payload: dict[str, Any]) -> None:
        if self._conversation is not None or self._websocket is None:
            return

        if not self._settings.elevenlabs_agent_id:
            self._call_store.add_event(
                self.call_sid,
                "error",
                "ELEVENLABS_AGENT_ID is not configured.",
            )
            return

        self._call_store.update_status(self.call_sid, "connected")
        self._call_store.add_event(self.call_sid, "status", "ElevenLabs agent session starting.")

        self._audio_interface = TwilioAudioInterface(self._websocket, self._loop)
        start_payload = payload.get("start", {})
        stream_sid = start_payload.get("streamSid")
        if stream_sid:
            self._audio_interface.stream_sid = stream_sid

        client = build_elevenlabs_client(self._settings)
        config = build_conversation_config(
            self._settings,
            system_prompt=self._system_prompt,
            first_message=self._first_message,
            client=client,
        )
        override_mode = "with overrides" if config else "using ElevenLabs dashboard config only"
        self._call_store.add_event(
            self.call_sid,
            "system",
            f"ElevenLabs session starting {override_mode}.",
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
            message = str(exc)
            self._conversation = None
            self._call_store.add_event(self.call_sid, "error", f"ElevenLabs session failed: {message}")
            raise
        self._start_end_watcher()
        await self._audio_interface.handle_twilio_message(payload)
        self._call_store.add_event(
            self.call_sid,
            "system",
            "ElevenLabs session active. Audio bridge: Twilio mulaw 8kHz <-> ElevenLabs PCM 16kHz.",
        )

    def _on_agent_response(self, text: str) -> None:
        asyncio.run_coroutine_threadsafe(self._record_agent_response(text), self._loop)

    def _on_user_transcript(self, text: str) -> None:
        asyncio.run_coroutine_threadsafe(self._record_user_transcript(text), self._loop)

    async def _record_agent_response(self, text: str) -> None:
        if self._closed or not text.strip():
            return

        self._history.append({"role": "assistant", "content": text.strip()})
        self._call_store.add_event(self.call_sid, "assistant", text.strip())

    async def _record_user_transcript(self, text: str) -> None:
        if self._closed or not text.strip():
            return

        display_text = normalize_patient_transcript(
            text.strip(),
            self._settings.hardcoded_patient_name,
        )
        self._history.append({"role": "user", "content": display_text})
        self._call_store.add_event(self.call_sid, "user", display_text)
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
            if not intake.phone_number:
                call_record = self._call_store.get(self.call_sid)
                if call_record and call_record.from_number:
                    intake = intake.model_copy(update={"phone_number": call_record.from_number})

            saved = self._call_store.upsert_patient_intake(intake)
            self._call_store.add_event(
                self.call_sid,
                "system",
                f"Patient intake updated ({saved.intake_status}).",
            )
        except Exception as exc:  # pragma: no cover - defensive logging path
            self._call_store.add_event(self.call_sid, "error", f"Intake extraction error: {exc}")

    def _start_end_watcher(self) -> None:
        """Watch for the ElevenLabs agent ending the conversation (End Call tool) and hang up Twilio."""
        conversation = self._conversation
        if conversation is None:
            return

        def _wait_for_end() -> None:
            try:
                conversation.wait_for_session_end()
            except Exception as exc:  # pragma: no cover - defensive logging path
                logger.debug("Session-end watcher stopped for %s: %s", self.call_sid, exc)
            asyncio.run_coroutine_threadsafe(self._handle_agent_ended_call(), self._loop)

        self._end_watcher = threading.Thread(target=_wait_for_end, daemon=True)
        self._end_watcher.start()

    async def _handle_agent_ended_call(self) -> None:
        if self._closed:
            return
        self._call_store.add_event(
            self.call_sid,
            "system",
            "Agent ended the conversation. Hanging up the phone call.",
        )
        await self._hang_up_twilio()
        await self.close(status="completed")

    async def _hang_up_twilio(self) -> None:
        """Terminate the live Twilio call so the line actually disconnects after goodbye."""
        if self._twilio_client is None or not self.call_sid.startswith("CA"):
            return
        try:
            await asyncio.to_thread(
                lambda: self._twilio_client.calls(self.call_sid).update(status="completed")
            )
            self._call_store.add_event(self.call_sid, "status", "Twilio call disconnected by agent.")
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("Failed to hang up Twilio call %s: %s", self.call_sid, exc)

    async def close(self, status: str = "completed") -> None:
        if self._closed:
            return

        if self._history:
            await self._capture_patient_intake(latest_user_text="")

        self._closed = True
        if self._conversation is not None:
            try:
                self._conversation.end_session()
                self._conversation.wait_for_session_end()
            except Exception as exc:  # pragma: no cover - defensive logging path
                logger.warning("Error ending ElevenLabs session: %s", exc)
            finally:
                self._conversation = None

        if self._audio_interface is not None:
            self._audio_interface.stop()
        self._audio_interface = None
        self._call_store.update_status(self.call_sid, status)
        self._call_store.add_event(self.call_sid, "status", f"Call closed with status: {status}")
