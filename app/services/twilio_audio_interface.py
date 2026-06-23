from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import deque
from typing import Callable
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.services.audio_conversion import (
    elevenlabs_pcm_to_twilio_mulaw,
    twilio_mulaw_to_elevenlabs_pcm,
)
from elevenlabs.conversational_ai.conversation import AudioInterface


logger = logging.getLogger(__name__)

# 20 ms of mulaw audio at 8 kHz (Twilio media stream packet size).
TWILIO_CHUNK_SIZE = 160


class TwilioAudioInterface(AudioInterface):
    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self._websocket = websocket
        self._loop = loop
        self._input_callback: Callable[[bytes], None] | None = None
        self.stream_sid: str | None = None
        self._input_buffer: deque[bytes] = deque()
        self._output_buffer: deque[bytes] = deque()
        self._input_ratecv_state: tuple[int, ...] | None = None
        self._output_ratecv_state: tuple[int, ...] | None = None
        self._bytes_sent_to_twilio = 0

    def start(self, input_callback: Callable[[bytes], None]) -> None:
        self._input_callback = input_callback
        while self._input_buffer:
            input_callback(self._input_buffer.popleft())

    def stop(self) -> None:
        self._input_callback = None
        self.stream_sid = None
        self._input_buffer.clear()
        self._output_buffer.clear()
        self._input_ratecv_state = None
        self._output_ratecv_state = None
        self._bytes_sent_to_twilio = 0

    def output(self, audio: bytes) -> None:
        if not audio:
            return

        if not self.stream_sid:
            self._output_buffer.append(audio)
            return

        asyncio.run_coroutine_threadsafe(self._send_audio_to_twilio(audio), self._loop)

    def interrupt(self) -> None:
        asyncio.run_coroutine_threadsafe(self._send_clear_message_to_twilio(), self._loop)

    async def handle_twilio_message(self, data: dict) -> None:
        event_type = data.get("event")
        if event_type == "start":
            self.stream_sid = data["start"]["streamSid"]
            await self._flush_output_buffer()
        elif event_type == "media":
            mulaw_audio = base64.b64decode(data["media"]["payload"])
            pcm_16k, self._input_ratecv_state = twilio_mulaw_to_elevenlabs_pcm(
                mulaw_audio,
                self._input_ratecv_state,
            )
            if not pcm_16k:
                return

            if self._input_callback:
                self._input_callback(pcm_16k)
            else:
                self._input_buffer.append(pcm_16k)

    async def _flush_output_buffer(self) -> None:
        while self._output_buffer:
            await self._send_audio_to_twilio(self._output_buffer.popleft())

    async def _send_audio_to_twilio(self, pcm_audio: bytes) -> None:
        if not self.stream_sid or not pcm_audio:
            return

        if not self._websocket_connected():
            return

        mulaw_audio, self._output_ratecv_state = elevenlabs_pcm_to_twilio_mulaw(
            pcm_audio,
            self._output_ratecv_state,
        )
        if not mulaw_audio:
            return

        try:
            for index in range(0, len(mulaw_audio), TWILIO_CHUNK_SIZE):
                chunk = mulaw_audio[index : index + TWILIO_CHUNK_SIZE]
                payload = base64.b64encode(chunk).decode("ascii")
                await self._websocket.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": payload},
                        }
                    )
                )
                self._bytes_sent_to_twilio += len(chunk)
                await asyncio.sleep(0)

            await self._websocket.send_text(
                json.dumps(
                    {
                        "event": "mark",
                        "streamSid": self.stream_sid,
                        "mark": {"name": f"agent-{uuid4().hex[:8]}"},
                    }
                )
            )
            logger.debug("Sent %s mulaw bytes to Twilio.", self._bytes_sent_to_twilio)
        except (WebSocketDisconnect, RuntimeError) as exc:
            logger.warning("Twilio websocket closed while sending agent audio: %s", exc)

    async def _send_clear_message_to_twilio(self) -> None:
        if not self.stream_sid or not self._websocket_connected():
            return

        message = {
            "event": "clear",
            "streamSid": self.stream_sid,
        }
        try:
            await self._websocket.send_text(json.dumps(message))
        except (WebSocketDisconnect, RuntimeError) as exc:
            logger.warning("Twilio websocket closed while clearing audio: %s", exc)

    def _websocket_connected(self) -> bool:
        return (
            self._websocket.client_state == WebSocketState.CONNECTED
            and self._websocket.application_state == WebSocketState.CONNECTED
        )
