from __future__ import annotations

import audioop

ELEVENLABS_SAMPLE_RATE = 16000
TWILIO_SAMPLE_RATE = 8000
SAMPLE_WIDTH = 2


def twilio_mulaw_to_elevenlabs_pcm(
    mulaw_audio: bytes,
    state: tuple[int, ...] | None = None,
) -> tuple[bytes, tuple[int, ...] | None]:
    if not mulaw_audio:
        return b"", state

    pcm_8k = audioop.ulaw2lin(mulaw_audio, SAMPLE_WIDTH)
    pcm_16k, new_state = audioop.ratecv(
        pcm_8k,
        SAMPLE_WIDTH,
        1,
        TWILIO_SAMPLE_RATE,
        ELEVENLABS_SAMPLE_RATE,
        state,
    )
    return pcm_16k, new_state


def elevenlabs_pcm_to_twilio_mulaw(
    pcm_audio: bytes,
    state: tuple[int, ...] | None = None,
) -> tuple[bytes, tuple[int, ...] | None]:
    if not pcm_audio:
        return b"", state

    pcm_8k, new_state = audioop.ratecv(
        pcm_audio,
        SAMPLE_WIDTH,
        1,
        ELEVENLABS_SAMPLE_RATE,
        TWILIO_SAMPLE_RATE,
        state,
    )
    mulaw_audio = audioop.lin2ulaw(pcm_8k, SAMPLE_WIDTH)
    return mulaw_audio, new_state
