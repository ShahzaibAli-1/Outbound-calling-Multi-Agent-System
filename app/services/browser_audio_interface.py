from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import deque
from typing import Awaitable, Callable

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from elevenlabs.conversational_ai.conversation import AudioInterface


from app.services.audio_conversion import ELEVENLABS_SAMPLE_RATE


logger = logging.getLogger(__name__)


class BrowserAudioInterface(AudioInterface):
    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self._websocket = websocket
        self._loop = loop
        self._input_callback: Callable[[bytes], None] | None = None
        self._input_buffer: deque[bytes] = deque()
        self._output_buffer: deque[bytes] = deque()

    def start(self, input_callback: Callable[[bytes], None]) -> None:
        self._input_callback = input_callback
        while self._input_buffer:
            input_callback(self._input_buffer.popleft())
        if self._output_buffer:
            asyncio.run_coroutine_threadsafe(self._flush_output_buffer(), self._loop)

    async def _flush_output_buffer(self) -> None:
        while self._output_buffer and self._connected():
            await self._send_agent_audio(self._output_buffer.popleft())

    def stop(self) -> None:
        self._input_callback = None
        self._input_buffer.clear()
        self._output_buffer.clear()

    def output(self, audio: bytes) -> None:
        if not audio:
            return
        asyncio.run_coroutine_threadsafe(self._queue_output(audio), self._loop)

    async def _queue_output(self, audio: bytes) -> None:
        if self._connected():
            await self._send_agent_audio(audio)
        else:
            self._output_buffer.append(audio)

    def interrupt(self) -> None:
        asyncio.run_coroutine_threadsafe(self._send_clear(), self._loop)

    async def handle_browser_audio(self, pcm_audio: bytes) -> None:
        if not pcm_audio:
            return
        if self._input_callback:
            self._input_callback(pcm_audio)
        else:
            self._input_buffer.append(pcm_audio)

    async def send_transcript(self, role: str, text: str) -> None:
        if not text.strip() or not self._connected():
            return
        try:
            await self._websocket.send_text(
                json.dumps({"type": "transcript", "role": role, "text": text.strip()})
            )
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def send_status(self, status: str, detail: str = "") -> None:
        if not self._connected():
            return
        try:
            await self._websocket.send_text(
                json.dumps({"type": "status", "status": status, "detail": detail})
            )
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def _send_agent_audio(self, audio: bytes) -> None:
        if not self._connected():
            return
        try:
            await self._websocket.send_text(
                json.dumps(
                    {
                        "type": "agent_audio",
                        "payload": base64.b64encode(audio).decode("ascii"),
                        "sample_rate": ELEVENLABS_SAMPLE_RATE,
                    }
                )
            )
        except (WebSocketDisconnect, RuntimeError) as exc:
            logger.debug("Demo websocket closed while sending audio: %s", exc)

    async def _send_clear(self) -> None:
        if not self._connected():
            return
        try:
            await self._websocket.send_text(json.dumps({"type": "clear_audio"}))
        except (WebSocketDisconnect, RuntimeError):
            pass

    def _connected(self) -> bool:
        return (
            self._websocket.client_state == WebSocketState.CONNECTED
            and self._websocket.application_state == WebSocketState.CONNECTED
        )
