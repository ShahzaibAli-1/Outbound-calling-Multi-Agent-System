from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import deque
from typing import Callable

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.services.audio_conversion import (
    TWILIO_SAMPLE_RATE,
    elevenlabs_pcm_to_twilio_mulaw,
    twilio_mulaw_to_elevenlabs_pcm,
)
from elevenlabs.conversational_ai.conversation import AudioInterface


logger = logging.getLogger(__name__)

# 20 ms of mulaw audio at 8 kHz — Twilio's expected real-time frame size.
TWILIO_CHUNK_SIZE = 160
TWILIO_FRAME_INTERVAL_SEC = TWILIO_CHUNK_SIZE / TWILIO_SAMPLE_RATE
MULAW_SILENCE_BYTE = 0xFF


class TwilioAudioInterface(AudioInterface):
    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self._websocket = websocket
        self._loop = loop
        self._input_callback: Callable[[bytes], None] | None = None
        self.stream_sid: str | None = None
        self._input_buffer: deque[bytes] = deque()
        self._pre_start_pcm: deque[bytes] = deque()
        self._input_ratecv_state: tuple[int, ...] | None = None
        self._output_ratecv_state: tuple[int, ...] | None = None
        self._pcm_queue: asyncio.Queue[bytes | None] | None = None
        self._mulaw_pending = bytearray()
        self._playback_task: asyncio.Task[None] | None = None
        self._next_frame_time: float | None = None

    def start(self, input_callback: Callable[[bytes], None]) -> None:
        self._input_callback = input_callback
        while self._input_buffer:
            input_callback(self._input_buffer.popleft())

    def stop(self) -> None:
        self._input_callback = None
        self.stream_sid = None
        self._input_buffer.clear()
        self._pre_start_pcm.clear()
        self._input_ratecv_state = None
        if self._pcm_queue is not None:
            try:
                self._pcm_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def output(self, audio: bytes) -> None:
        if not audio:
            return

        if not self.stream_sid:
            self._pre_start_pcm.append(audio)
            return

        asyncio.run_coroutine_threadsafe(self._enqueue_pcm(audio), self._loop)

    def interrupt(self) -> None:
        asyncio.run_coroutine_threadsafe(self._handle_interrupt(), self._loop)

    async def handle_twilio_message(self, data: dict) -> None:
        event_type = data.get("event")
        if event_type == "start":
            self.stream_sid = data["start"]["streamSid"]
            await self._start_output_pipeline()
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

    async def _start_output_pipeline(self) -> None:
        self._reset_output_pipeline()
        self._pcm_queue = asyncio.Queue()
        self._playback_task = asyncio.create_task(self._playback_loop())
        while self._pre_start_pcm:
            await self._pcm_queue.put(self._pre_start_pcm.popleft())

    def _reset_output_pipeline(self) -> None:
        if self._playback_task is not None and not self._playback_task.done():
            self._playback_task.cancel()
        self._playback_task = None
        self._pcm_queue = None
        self._mulaw_pending.clear()
        self._output_ratecv_state = None
        self._next_frame_time = None

    async def _enqueue_pcm(self, pcm_audio: bytes) -> None:
        if self._pcm_queue is None:
            self._pre_start_pcm.append(pcm_audio)
            return
        await self._pcm_queue.put(pcm_audio)

    async def _handle_interrupt(self) -> None:
        if self._pcm_queue is not None:
            while not self._pcm_queue.empty():
                try:
                    self._pcm_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._mulaw_pending.clear()
        self._output_ratecv_state = None
        self._next_frame_time = None
        await self._send_clear_message_to_twilio()

    async def _playback_loop(self) -> None:
        """Serialize agent audio and pace frames at real-time telephony rate."""
        queue = self._pcm_queue
        if queue is None:
            return

        loop = asyncio.get_running_loop()
        self._next_frame_time = loop.time()

        try:
            while True:
                if await self._drain_pcm_queue(queue):
                    await self._flush_mulaw_pending()
                    break

                if len(self._mulaw_pending) >= TWILIO_CHUNK_SIZE:
                    chunk = bytes(self._mulaw_pending[:TWILIO_CHUNK_SIZE])
                    del self._mulaw_pending[:TWILIO_CHUNK_SIZE]
                    await self._send_mulaw_frame(chunk)
                    self._next_frame_time = (self._next_frame_time or loop.time()) + TWILIO_FRAME_INTERVAL_SEC
                    delay = self._next_frame_time - loop.time()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    else:
                        self._next_frame_time = loop.time()
                    continue

                if self._mulaw_pending:
                    await self._flush_mulaw_pending()
                    continue

                pcm_audio = await queue.get()
                if pcm_audio is None:
                    await self._flush_mulaw_pending()
                    break
                self._append_mulaw(pcm_audio)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("Twilio playback loop error: %s", exc)
        finally:
            self._playback_task = None
            self._pcm_queue = None
            self._mulaw_pending.clear()
            self._output_ratecv_state = None
            self._next_frame_time = None

    async def _drain_pcm_queue(self, queue: asyncio.Queue[bytes | None]) -> bool:
        while not queue.empty():
            pcm_audio = queue.get_nowait()
            if pcm_audio is None:
                return True
            self._append_mulaw(pcm_audio)
        return False

    def _append_mulaw(self, pcm_audio: bytes) -> None:
        mulaw_audio, self._output_ratecv_state = elevenlabs_pcm_to_twilio_mulaw(
            pcm_audio,
            self._output_ratecv_state,
        )
        if mulaw_audio:
            self._mulaw_pending.extend(mulaw_audio)

    async def _flush_mulaw_pending(self) -> None:
        if not self._mulaw_pending:
            return

        padded = bytes(self._mulaw_pending)
        self._mulaw_pending.clear()
        remainder = len(padded) % TWILIO_CHUNK_SIZE
        if remainder:
            padded += bytes([MULAW_SILENCE_BYTE]) * (TWILIO_CHUNK_SIZE - remainder)

        for index in range(0, len(padded), TWILIO_CHUNK_SIZE):
            await self._send_mulaw_frame(padded[index : index + TWILIO_CHUNK_SIZE])
            await asyncio.sleep(TWILIO_FRAME_INTERVAL_SEC)

    async def _send_mulaw_frame(self, mulaw_chunk: bytes) -> None:
        if not self.stream_sid or not mulaw_chunk or not self._websocket_connected():
            return

        if len(mulaw_chunk) < TWILIO_CHUNK_SIZE:
            mulaw_chunk = mulaw_chunk + bytes([MULAW_SILENCE_BYTE]) * (
                TWILIO_CHUNK_SIZE - len(mulaw_chunk)
            )

        payload = base64.b64encode(mulaw_chunk).decode("ascii")
        message = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {
                "track": "outbound",
                "payload": payload,
            },
        }
        try:
            await self._websocket.send_text(json.dumps(message))
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
