from __future__ import annotations

import asyncio
import base64
import json
from uuid import uuid4

from fastapi import WebSocket

from app.config import Settings
from app.services.deepgram_service import DeepgramLiveTranscriber, DeepgramSpeechSynthesizer
from app.services.openai_service import OpenAIResponder
from app.store import CallStore


class CallSession:
    def __init__(
        self,
        *,
        settings: Settings,
        call_store: CallStore,
        responder: OpenAIResponder,
        call_sid: str,
        from_number: str | None,
        to_number: str | None,
        direction: str,
        system_prompt: str | None = None,
    ) -> None:
        self._settings = settings
        self._call_store = call_store
        self._responder = responder
        self._websocket: WebSocket | None = None
        self._stream_sid: str | None = None
        self._system_prompt = system_prompt or settings.agent_system_prompt
        self._history: list[dict[str, str]] = []
        self._reply_lock = False
        self._closed = False

        self.call_sid = call_sid
        self._call_store.ensure_call(
            call_sid,
            from_number=from_number,
            to_number=to_number,
            direction=direction,
        )

        self._transcriber = DeepgramLiveTranscriber(settings, self._handle_final_transcript)
        self._synthesizer = DeepgramSpeechSynthesizer(settings)

    async def attach(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def start(self, stream_sid: str) -> None:
        self._stream_sid = stream_sid
        self._call_store.update_status(self.call_sid, "connected")
        self._call_store.add_event(self.call_sid, "status", "Media stream connected.")
        await self._transcriber.start()
        # Give Twilio a brief moment to finish bridging the answered call audio path
        # before the first synthesized greeting is sent.
        await asyncio.sleep(0.75)
        await self._send_agent_message(self._settings.agent_greeting, record_history=False)

    async def ingest_audio(self, payload: str) -> None:
        if self._closed:
            return
        await self._transcriber.send_audio(base64.b64decode(payload))

    async def _handle_final_transcript(self, transcript: str) -> None:
        if self._closed or self._reply_lock:
            return

        self._reply_lock = True
        try:
            self._call_store.add_event(self.call_sid, "user", transcript)

            answer = await self._responder.generate_reply(
                history=self._history,
                user_text=transcript,
                system_prompt=self._system_prompt,
            )

            self._history.append({"role": "user", "content": transcript})
            self._history.append({"role": "assistant", "content": answer})
            await self._send_agent_message(answer, record_history=False)
        except Exception as exc:  # pragma: no cover - defensive logging path
            self._call_store.add_event(self.call_sid, "error", f"Agent error: {exc}")
            await self._send_agent_message(
                "I hit a processing issue. Please repeat that in a different way.",
                record_history=False,
            )
        finally:
            self._reply_lock = False

    async def _send_agent_message(self, text: str, *, record_history: bool) -> None:
        if record_history:
            self._history.append({"role": "assistant", "content": text})

        self._call_store.add_event(self.call_sid, "assistant", text)
        audio_bytes = await self._synthesizer.synthesize(text)
        await self._send_audio(audio_bytes)

    async def _send_audio(self, audio_bytes: bytes) -> None:
        if self._websocket is None or self._stream_sid is None:
            return

        chunk_size = 320
        for index in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[index : index + chunk_size]
            payload = base64.b64encode(chunk).decode("ascii")
            await self._websocket.send_text(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": self._stream_sid,
                        "media": {"payload": payload},
                    }
                )
            )

        await self._websocket.send_text(
            json.dumps(
                {
                    "event": "mark",
                    "streamSid": self._stream_sid,
                    "mark": {"name": f"reply-{uuid4().hex[:8]}"},
                }
            )
        )

    async def close(self, status: str = "completed") -> None:
        if self._closed:
            return

        self._closed = True
        self._call_store.update_status(self.call_sid, status)
        self._call_store.add_event(self.call_sid, "status", f"Call closed with status: {status}")
        await self._transcriber.close()
        await self._synthesizer.close()
