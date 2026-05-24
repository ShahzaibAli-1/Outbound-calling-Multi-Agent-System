from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from app.config import Settings


TranscriptHandler = Callable[[str], Awaitable[None]]


class DeepgramLiveTranscriber:
    def __init__(self, settings: Settings, on_transcript: TranscriptHandler) -> None:
        self._settings = settings
        self._on_transcript = on_transcript
        self._connection = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._last_final = ""

    async def start(self) -> None:
        query = urlencode(
            {
                "encoding": "mulaw",
                "sample_rate": 8000,
                "channels": 1,
                "interim_results": "false",
                "smart_format": "true",
                "endpointing": 300,
                "model": self._settings.deepgram_stt_model,
            }
        )
        self._connection = await ws_connect(
            f"wss://api.deepgram.com/v1/listen?{query}",
            additional_headers={"Authorization": f"Token {self._settings.deepgram_api_key}"},
            max_size=None,
        )
        self._receiver_task = asyncio.create_task(self._receive_messages())

    async def send_audio(self, audio_chunk: bytes) -> None:
        if self._connection is None:
            return
        await self._connection.send(audio_chunk)

    async def _receive_messages(self) -> None:
        if self._connection is None:
            return

        try:
            async for raw_message in self._connection:
                if not isinstance(raw_message, str):
                    continue

                payload = json.loads(raw_message)
                if payload.get("type") != "Results":
                    continue

                transcript = (
                    payload.get("channel", {})
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                    .strip()
                )
                if transcript and payload.get("is_final") and transcript != self._last_final:
                    self._last_final = transcript
                    await self._on_transcript(transcript)
        except ConnectionClosed:
            return

    async def close(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.send(json.dumps({"type": "CloseStream"}))
            except ConnectionClosed:
                pass
            await self._connection.close()

        if self._receiver_task is not None:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass


class DeepgramSpeechSynthesizer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url="https://api.deepgram.com",
            timeout=httpx.Timeout(60.0),
            headers={"Authorization": f"Token {settings.deepgram_api_key}"},
        )

    async def synthesize(self, text: str) -> bytes:
        response = await self._client.post(
            "/v1/speak",
            params={
                "model": self._settings.deepgram_tts_model,
                "encoding": "mulaw",
                "sample_rate": 8000,
                "container": "none",
            },
            json={"text": text},
        )
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()
