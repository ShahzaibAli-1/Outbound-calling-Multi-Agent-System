from __future__ import annotations

import json
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.campaigns import get_campaign_scenario, list_campaign_scenarios
from app.config import BASE_DIR, compose_voice_prompt, get_settings
from app.models import ChatRequest, ChatResponse, OutboundCallRequest
from app.services.call_session import CallSession
from app.services.openai_service import OpenAIResponder
from app.store import CallStore


settings = get_settings()
app = FastAPI(title="AI Voice Agent", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = BASE_DIR / "frontend"
store = CallStore()
responder = OpenAIResponder(settings)
twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
call_sessions: dict[str, CallSession] = {}
prompt_overrides: dict[str, str] = {}

if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


def get_runtime_warnings() -> list[str]:
    warnings: list[str] = []
    if "ngrok-free.app" in settings.public_base_url.lower():
        warnings.append(
            "PUBLIC_BASE_URL is using ngrok-free.app. Twilio media streams rely on a GET-based "
            "WebSocket handshake, and free ngrok warning/interstitial behavior can block that path. "
            "Use a paid ngrok/custom domain or another public HTTPS/WSS tunnel without an interstitial page."
        )
    return warnings


def get_or_create_session(
    *,
    call_sid: str,
    from_number: str | None,
    to_number: str | None,
    direction: str,
) -> CallSession:
    session = call_sessions.get(call_sid)
    if session is not None:
        return session

    session = CallSession(
        settings=settings,
        call_store=store,
        responder=responder,
        call_sid=call_sid,
        from_number=from_number,
        to_number=to_number,
        direction=direction,
        system_prompt=prompt_overrides.get(call_sid),
    )
    call_sessions[call_sid] = session
    return session


def resolve_prompt_selection(
    *,
    system_prompt: str | None,
    scenario_id: str | None,
) -> tuple[str | None, str]:
    if system_prompt and system_prompt.strip():
        return compose_voice_prompt(system_prompt), "custom prompt"

    if scenario_id:
        scenario = get_campaign_scenario(scenario_id)
        if scenario is None:
            raise HTTPException(status_code=400, detail=f"Unknown campaign scenario: {scenario_id}")
        return compose_voice_prompt(scenario.prompt), f"scenario '{scenario.label}'"

    return None, "default prompt"


@app.get("/")
async def serve_index() -> Response:
    index_path = frontend_dir / "index.html"
    if not index_path.exists():
        return JSONResponse(
            {
                "message": "Frontend not generated yet.",
                "health": "/api/health",
            }
        )
    return FileResponse(index_path)


@app.get("/api/health")
async def healthcheck() -> dict[str, object]:
    return {
        "ok": True,
        "agent": settings.agent_name,
        "phone_number": settings.twilio_phone_number,
        "public_base_url": settings.public_base_url,
        "voice_webhook": settings.voice_webhook_url,
        "media_stream": settings.media_stream_url,
        "providers": {
            "openai": bool(settings.openai_api_key),
            "deepgram": bool(settings.deepgram_api_key),
            "twilio": bool(settings.twilio_account_sid and settings.twilio_auth_token),
        },
        "warnings": get_runtime_warnings(),
    }


@app.get("/api/campaign-scenarios")
async def get_campaign_scenarios() -> dict[str, object]:
    return {"scenarios": list_campaign_scenarios()}


@app.post("/api/chat/test", response_model=ChatResponse)
async def test_chat(payload: ChatRequest) -> ChatResponse:
    resolved_prompt, _prompt_source = resolve_prompt_selection(
        system_prompt=payload.system_prompt,
        scenario_id=payload.scenario_id,
    )
    answer = await responder.generate_reply(
        history=[],
        user_text=payload.message,
        system_prompt=resolved_prompt,
    )
    return ChatResponse(answer=answer)


@app.get("/api/calls")
async def list_calls() -> dict[str, object]:
    return {"calls": [call.model_dump(mode="json") for call in store.list_calls()]}


@app.post("/api/calls/outbound")
async def create_outbound_call(payload: OutboundCallRequest) -> dict[str, str]:
    resolved_prompt, prompt_source = resolve_prompt_selection(
        system_prompt=payload.system_prompt,
        scenario_id=payload.scenario_id,
    )

    def _create_call() -> object:
        return twilio_client.calls.create(
            to=payload.to_number,
            from_=settings.twilio_phone_number,
            url=settings.voice_webhook_url,
            method="POST",
            status_callback=settings.status_callback_url,
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )

    try:
        call = await anyio.to_thread.run_sync(_create_call)
    except TwilioRestException as exc:
        detail = exc.msg or "Twilio rejected the outbound call request."
        status_code = 400 if 400 <= exc.status < 500 else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc

    call_sid = getattr(call, "sid")

    if resolved_prompt:
        prompt_overrides[call_sid] = resolved_prompt

    store.ensure_call(
        call_sid,
        from_number=settings.twilio_phone_number,
        to_number=payload.to_number,
        direction="outbound",
    )
    store.update_status(call_sid, getattr(call, "status", "queued"))
    store.add_event(call_sid, "status", f"Outbound call queued for {payload.to_number}.")
    store.add_event(call_sid, "system", f"Prompt source: {prompt_source}.")

    return {
        "call_sid": call_sid,
        "status": getattr(call, "status", "queued"),
        "prompt_source": prompt_source,
    }


@app.api_route("/api/twilio/voice", methods=["GET", "POST"])
@app.api_route("/twilio/voice", methods=["GET", "POST"])
async def twilio_voice_webhook(request: Request) -> Response:
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    from_number = form.get("From")
    to_number = form.get("To")
    direction = str(form.get("Direction", "inbound"))

    get_or_create_session(
        call_sid=call_sid,
        from_number=str(from_number) if from_number else None,
        to_number=str(to_number) if to_number else None,
        direction=direction,
    )
    if call_sid:
        store.add_event(call_sid, "status", "Twilio voice webhook requested.")

    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"{settings.media_stream_url}?call_sid={call_sid}")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@app.post("/api/twilio/status")
@app.post("/twilio/status")
async def twilio_status_webhook(request: Request) -> dict[str, str]:
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    call_status = str(form.get("CallStatus", "unknown"))
    if call_sid:
        store.update_status(call_sid, call_status)
        store.add_event(call_sid, "status", f"Twilio status changed to {call_status}.")
        if call_status in {"completed", "busy", "failed", "no-answer", "canceled"}:
            prompt_overrides.pop(call_sid, None)
    return {"ok": "true"}


@app.websocket("/ws/twilio-media")
async def twilio_media_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    query_call_sid = websocket.query_params.get("call_sid") or ""
    session = call_sessions.get(query_call_sid)
    if query_call_sid:
        store.ensure_call(query_call_sid)
        store.add_event(query_call_sid, "status", "Twilio media WebSocket handshake accepted.")

    try:
        while True:
            message = await websocket.receive_text()
            payload = json.loads(message)
            event_type = payload.get("event")

            if event_type == "connected" and query_call_sid:
                store.add_event(query_call_sid, "status", "Twilio sent media stream connected event.")

            if event_type == "start":
                start_payload = payload.get("start", {})
                call_sid = start_payload.get("callSid") or query_call_sid
                session = get_or_create_session(
                    call_sid=call_sid,
                    from_number=None,
                    to_number=None,
                    direction="inbound",
                )
                await session.attach(websocket)
                await session.start(start_payload.get("streamSid", ""))

            elif event_type == "media" and session is not None:
                media_payload = payload.get("media", {}).get("payload")
                if media_payload:
                    await session.ingest_audio(media_payload)

            elif event_type == "stop" and session is not None:
                await session.close(status="completed")
                call_sessions.pop(session.call_sid, None)
                break

    except WebSocketDisconnect:
        if session is not None:
            await session.close(status="disconnected")
            call_sessions.pop(session.call_sid, None)
    except Exception as exc:
        call_sid = session.call_sid if session is not None else query_call_sid
        if call_sid:
            store.add_event(call_sid, "error", f"Twilio media stream error: {exc}")
        if session is not None:
            await session.close(status="error")
            call_sessions.pop(session.call_sid, None)
        raise


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)