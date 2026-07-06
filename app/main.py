from __future__ import annotations

import json
import logging
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.campaigns import get_campaign_scenario, list_campaign_scenario_groups, list_campaign_scenarios
from app.config import BASE_DIR, compose_voice_prompt, detect_ngrok_public_url, get_settings
from app.models import ChatRequest, ChatResponse, DemoCallRequest, OutboundCallRequest, PatientIntakeRecord
from app.services.demo_session import DemoCallSession, new_demo_call_sid
from app.services.elevenlabs_agent import (
    build_conversation_config,
    build_elevenlabs_client,
    format_elevenlabs_error,
    sync_medory_agent_profile,
    verify_elevenlabs_agent,
)
from app.services.call_session import CallSession
from app.services.openai_service import OpenAIResponder
from app.services.patient_intake_service import PatientIntakeExtractor
from app.store import CallStore


settings = get_settings()
app = FastAPI(title="Medory Call Center", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = BASE_DIR / "frontend"
store = CallStore()
responder = OpenAIResponder(settings) if settings.openai_api_key else None
intake_extractor = PatientIntakeExtractor(settings) if settings.openai_api_key else None
elevenlabs_client = build_elevenlabs_client(settings)
agent_sync_status = sync_medory_agent_profile(settings, elevenlabs_client)
twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
call_sessions: dict[str, CallSession] = {}
demo_sessions: dict[str, DemoCallSession] = {}
prompt_overrides: dict[str, str] = {}
greeting_overrides: dict[str, str] = {}

if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

logger = logging.getLogger(__name__)


def configure_twilio_phone_webhooks(client: Client) -> dict[str, object]:
    """Keep the Twilio number voice + status webhooks aligned with PUBLIC_BASE_URL."""
    voice_url = settings.voice_webhook_url
    status_url = settings.status_callback_url
    numbers = client.incoming_phone_numbers.list(phone_number=settings.twilio_phone_number)
    updated = 0
    for number in numbers:
        number.update(
            voice_url=voice_url,
            voice_method="POST",
            status_callback=status_url,
            status_callback_method="POST",
        )
        updated += 1
    return {
        "phone_number": settings.twilio_phone_number,
        "voice_url": voice_url,
        "status_callback": status_url,
        "updated_numbers": updated,
    }


twilio_webhook_status = configure_twilio_phone_webhooks(twilio_client)
logger.info("PUBLIC_BASE_URL=%s", settings.public_base_url)
logger.info("Twilio voice webhook=%s", settings.voice_webhook_url)


def seed_patient_name(call_sid: str) -> None:
    if not settings.hardcoded_patient_name:
        return
    store.upsert_patient_intake(
        PatientIntakeRecord(
            call_sid=call_sid,
            full_name=settings.hardcoded_patient_name,
            intake_status="in_progress",
        )
    )


def get_runtime_warnings() -> list[str]:
    warnings: list[str] = []
    if not settings.elevenlabs_agent_id:
        warnings.append(
            "ELEVENLABS_AGENT_ID is not set. Create an ElevenLabs Conversational AI agent "
            "and add its ID to .env before placing or receiving calls."
        )
    if not agent_sync_status.get("synced"):
        warnings.append(
            f"ElevenLabs agent sync failed: {agent_sync_status.get('message', 'unknown error')}. "
            "Fix ELEVENLABS_VOICE_ID / ELEVENLABS_AGENT_ID and restart the server."
        )
    if not settings.elevenlabs_override_prompt:
        warnings.append(
            "ElevenLabs prompt override is disabled. Configure the agent system prompt in the "
            "ElevenLabs dashboard (copy from test_system_prompt.txt). Set ELEVENLABS_OVERRIDE_PROMPT=true "
            "only after enabling Prompt override under Agent > Security."
        )
    if not settings.elevenlabs_override_first_message:
        warnings.append(
            "ElevenLabs first_message override is disabled. Set the agent opening line in the "
            "ElevenLabs dashboard (use AGENT_GREETING from .env as reference)."
        )
    if "ngrok-free.app" in settings.public_base_url.lower():
        warnings.append(
            "PUBLIC_BASE_URL is using ngrok-free.app. Twilio media streams rely on a GET-based "
            "WebSocket handshake, and free ngrok warning/interstitial behavior can block that path. "
            "Use a paid ngrok/custom domain or another public HTTPS/WSS tunnel without an interstitial page."
        )
    return warnings


async def check_public_base_url_reachable() -> dict[str, object]:
    """Verify Twilio can reach the configured PUBLIC_BASE_URL voice webhook."""
    base = settings.public_base_url.rstrip("/")
    voice_url = settings.voice_webhook_url
    result: dict[str, object] = {
        "public_base_url": base,
        "voice_webhook": voice_url,
        "media_stream": settings.media_stream_url,
        "reachable": False,
        "voice_webhook_ok": False,
        "message": "Public URL check not run.",
    }
    try:
        headers = {"ngrok-skip-browser-warning": "true"}
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            health_response = await client.get(f"{base}/api/health", headers=headers)
            result["reachable"] = health_response.status_code == 200
            voice_response = await client.post(
                voice_url,
                data={
                    "CallSid": "CA_healthcheck",
                    "From": settings.twilio_phone_number,
                    "To": "+10000000000",
                    "Direction": "outbound-api",
                },
                headers=headers,
            )
            body = voice_response.text
            result["voice_webhook_ok"] = (
                voice_response.status_code == 200
                and "<Response>" in body
                and "<Stream" in body
            )
            result["voice_webhook_status"] = voice_response.status_code
            if result["reachable"] and result["voice_webhook_ok"]:
                result["message"] = "Public URL and Twilio voice webhook are reachable."
            elif not result["reachable"]:
                result["message"] = (
                    "PUBLIC_BASE_URL is not reachable. Start ngrok (or your tunnel), update .env, "
                    "and restart the server."
                )
            else:
                result["message"] = (
                    f"Voice webhook returned HTTP {voice_response.status_code}. "
                    "Twilio will play 'Application error' when the call is answered."
                )
    except Exception as exc:
        result["message"] = (
            f"Cannot reach PUBLIC_BASE_URL ({base}). Start your tunnel and update .env: {exc}"
        )
    return result


def is_inbound_twilio_direction(direction: str) -> bool:
    return "inbound" in direction.lower()


def prepare_inbound_call(
    *,
    call_sid: str,
    scenario_id: str | None = None,
) -> tuple[str | None, str, str]:
    """Resolve prompt, greeting, and scenario for an incoming Twilio call."""
    selected_scenario = scenario_id or settings.default_inbound_scenario_id
    scenario = get_campaign_scenario(selected_scenario)
    if scenario is None:
        selected_scenario = settings.default_inbound_scenario_id
        scenario = get_campaign_scenario(selected_scenario)

    if scenario is not None:
        resolved_prompt = compose_voice_prompt(scenario.prompt)
        prompt_source = f"inbound scenario '{scenario.label}'"
    else:
        resolved_prompt = settings.agent_system_prompt
        prompt_source = "default inbound prompt"

    greeting = settings.resolved_inbound_greeting()
    prompt_overrides[call_sid] = resolved_prompt
    greeting_overrides[call_sid] = greeting

    sync_medory_agent_profile(
        settings,
        elevenlabs_client,
        system_prompt=resolved_prompt,
        first_message=greeting,
    )
    return selected_scenario, resolved_prompt, prompt_source


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
        intake_extractor=intake_extractor,
        call_sid=call_sid,
        from_number=from_number,
        to_number=to_number,
        direction=direction,
        system_prompt=prompt_overrides.get(call_sid),
        first_message=greeting_overrides.get(call_sid),
        twilio_client=twilio_client,
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

    return settings.agent_system_prompt, "default prompt"


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


@app.get("/live")
async def serve_live() -> Response:
    live_path = frontend_dir / "live.html"
    if not live_path.exists():
        raise HTTPException(status_code=404, detail="Live call page not found.")
    return FileResponse(live_path)


@app.get("/api/calls/{call_sid}")
async def get_call(call_sid: str) -> dict[str, object]:
    call = store.get(call_sid)
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call not found: {call_sid}")
    return {"call": call.model_dump(mode="json")}


@app.delete("/api/calls/{call_sid}")
@app.post("/api/calls/{call_sid}/delete")
async def delete_call(call_sid: str) -> dict[str, str]:
    demo_sessions.pop(call_sid, None)
    prompt_overrides.pop(call_sid, None)
    greeting_overrides.pop(call_sid, None)
    call_sessions.pop(call_sid, None)
    if not store.delete_call(call_sid):
        raise HTTPException(status_code=404, detail=f"Call not found: {call_sid}")
    return {"ok": "true", "deleted": call_sid}


@app.get("/api/health")
async def healthcheck() -> dict[str, object]:
    agent_status = verify_elevenlabs_agent(settings, elevenlabs_client)
    public_url_status = await check_public_base_url_reachable()
    warnings = get_runtime_warnings()
    if not public_url_status.get("voice_webhook_ok"):
        warnings.append(str(public_url_status.get("message")))
    return {
        "ok": True,
        "agent": settings.agent_name,
        "staff_name": settings.staff_name,
        "platform_name": "Medory",
        "phone_number": settings.twilio_phone_number,
        "inbound_number": settings.twilio_phone_number,
        "default_inbound_scenario_id": settings.default_inbound_scenario_id,
        "inbound_setup": {
            "dial": settings.twilio_phone_number,
            "voice_webhook": settings.voice_webhook_url,
            "note": "Call the Twilio number while the server and tunnel are running.",
        },
        "public_base_url": settings.public_base_url,
        "voice_webhook": settings.voice_webhook_url,
        "media_stream": settings.media_stream_url,
        "public_url_status": public_url_status,
        "twilio_webhooks": twilio_webhook_status,
        "elevenlabs_agent": agent_status,
        "elevenlabs_agent_sync": agent_sync_status,
        "providers": {
            "elevenlabs": bool(settings.elevenlabs_api_key and agent_status["valid"]),
            "openai": bool(settings.openai_api_key),
            "twilio": bool(settings.twilio_account_sid and settings.twilio_auth_token),
        },
        "warnings": warnings,
    }


@app.get("/api/campaign-scenarios")
async def get_campaign_scenarios(direction: str | None = None) -> dict[str, object]:
    normalized = direction.lower() if direction else None
    if normalized and normalized not in {"inbound", "outbound"}:
        raise HTTPException(status_code=400, detail="direction must be 'inbound' or 'outbound'")
    return {
        "scenarios": list_campaign_scenarios(normalized),  # type: ignore[arg-type]
        "groups": list_campaign_scenario_groups(),
    }


@app.post("/api/chat/test", response_model=ChatResponse)
async def test_chat(payload: ChatRequest) -> ChatResponse:
    if responder is None:
        raise HTTPException(
            status_code=503,
            detail="Browser chat test requires OPENAI_API_KEY. Live phone calls use the ElevenLabs agent.",
        )

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


@app.get("/api/dashboard/stats")
async def dashboard_stats() -> dict[str, object]:
    return store.dashboard_stats()


@app.post("/api/demo-calls/start")
async def start_demo_call(payload: DemoCallRequest) -> dict[str, str]:
    agent_status = verify_elevenlabs_agent(settings, elevenlabs_client)
    if not agent_status["valid"]:
        raise HTTPException(status_code=400, detail=str(agent_status["message"]))

    resolved_prompt, prompt_source = resolve_prompt_selection(
        system_prompt=payload.system_prompt,
        scenario_id=payload.scenario_id,
    )
    call_sid = new_demo_call_sid()
    sync_medory_agent_profile(
        settings,
        elevenlabs_client,
        system_prompt=resolved_prompt or settings.agent_system_prompt,
        first_message=settings.agent_greeting,
    )
    store.ensure_call(
        call_sid,
        direction="demo",
        call_type="demo",
        scenario_id=payload.scenario_id,
    )
    store.add_event(call_sid, "system", f"Demo call created using {prompt_source}.")
    seed_patient_name(call_sid)
    if resolved_prompt:
        prompt_overrides[call_sid] = resolved_prompt
    return {"call_sid": call_sid, "prompt_source": prompt_source}


@app.get("/api/calls")
async def list_calls() -> dict[str, object]:
    return {"calls": [call.model_dump(mode="json") for call in store.list_calls()]}


@app.get("/api/patient-intakes")
async def list_patient_intakes() -> dict[str, object]:
    return {
        "patient_intakes": [
            intake.model_dump(mode="json") for intake in store.list_patient_intakes()
        ]
    }


@app.get("/api/patient-intakes/{call_sid}")
async def get_patient_intake(call_sid: str) -> dict[str, object]:
    intake = store.get_patient_intake(call_sid)
    if intake is None:
        raise HTTPException(status_code=404, detail=f"No patient intake found for call: {call_sid}")
    return {"patient_intake": intake.model_dump(mode="json")}


@app.post("/api/calls/outbound")
async def create_outbound_call(payload: OutboundCallRequest) -> dict[str, str]:
    resolved_prompt, prompt_source = resolve_prompt_selection(
        system_prompt=payload.system_prompt,
        scenario_id=payload.scenario_id,
    )
    sync_medory_agent_profile(
        settings,
        elevenlabs_client,
        system_prompt=resolved_prompt or settings.agent_system_prompt,
        first_message=settings.agent_greeting,
    )

    if settings.elevenlabs_agent_phone_number_id:
        initiation_data = build_conversation_config(
            settings,
            system_prompt=resolved_prompt or settings.agent_system_prompt,
            first_message=settings.agent_greeting,
        )
        client_data = None
        if initiation_data is not None:
            client_data = {
                "conversation_config_override": initiation_data.conversation_config_override,
            }

        try:
            result = await anyio.to_thread.run_sync(
                lambda: elevenlabs_client.conversational_ai.twilio.outbound_call(
                    agent_id=settings.elevenlabs_agent_id,
                    agent_phone_number_id=settings.elevenlabs_agent_phone_number_id,
                    to_number=payload.to_number,
                    conversation_initiation_client_data=client_data,
                )
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        call_sid = getattr(result, "call_sid", None) or "unknown"
        store.ensure_call(
            str(call_sid),
            from_number=settings.twilio_phone_number,
            to_number=payload.to_number,
            direction="outbound",
            call_type="phone",
            scenario_id=payload.scenario_id,
        )
        store.update_status(str(call_sid), "queued")
        store.add_event(str(call_sid), "status", f"ElevenLabs outbound call queued for {payload.to_number}.")
        store.add_event(str(call_sid), "system", f"Prompt source: {prompt_source}.")
        seed_patient_name(str(call_sid))
        return {
            "call_sid": str(call_sid),
            "status": "queued",
            "prompt_source": prompt_source,
        }

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

    get_or_create_session(
        call_sid=call_sid,
        from_number=settings.twilio_phone_number,
        to_number=payload.to_number,
        direction="outbound-api",
    )

    store.ensure_call(
        call_sid,
        from_number=settings.twilio_phone_number,
        to_number=payload.to_number,
        direction="outbound",
        scenario_id=payload.scenario_id,
    )
    store.update_status(call_sid, getattr(call, "status", "queued"))
    store.add_event(call_sid, "status", f"Outbound call queued for {payload.to_number}.")
    store.add_event(call_sid, "system", f"Prompt source: {prompt_source}.")
    seed_patient_name(call_sid)

    return {
        "call_sid": call_sid,
        "status": getattr(call, "status", "queued"),
        "prompt_source": prompt_source,
    }


@app.api_route("/api/twilio/voice", methods=["GET", "POST"])
@app.api_route("/twilio/voice", methods=["GET", "POST"])
async def twilio_voice_webhook(request: Request) -> Response:
    try:
        if request.method == "GET":
            form = dict(request.query_params)
        else:
            form = dict(await request.form())

        call_sid = str(form.get("CallSid", "")).strip()
        from_number = form.get("From")
        to_number = form.get("To")
        direction = str(form.get("Direction", "inbound")).strip() or "inbound"

        if not call_sid:
            logger.error("Twilio voice webhook missing CallSid.")
            response = VoiceResponse()
            response.say("Sorry, this line is temporarily unavailable. Please try again later.")
            return Response(content=str(response), media_type="application/xml", status_code=200)

        inbound_call = is_inbound_twilio_direction(direction)
        scenario_id: str | None = None
        prompt_source = "default prompt"
        if inbound_call:
            scenario_id, _, prompt_source = prepare_inbound_call(call_sid=call_sid)

        get_or_create_session(
            call_sid=call_sid,
            from_number=str(from_number) if from_number else None,
            to_number=str(to_number) if to_number else None,
            direction=direction,
        )
        store.ensure_call(
            call_sid,
            from_number=str(from_number) if from_number else None,
            to_number=str(to_number) if to_number else None,
            direction="inbound" if inbound_call else direction,
            call_type="phone",
            scenario_id=scenario_id,
        )
        if inbound_call:
            store.add_event(call_sid, "system", f"Inbound call configured with {prompt_source}.")
        store.add_event(call_sid, "status", "Twilio voice webhook requested.")
        seed_patient_name(call_sid)

        stream_url = f"{settings.media_stream_url}?call_sid={call_sid}"
        response = VoiceResponse()
        connect = Connect()
        stream = connect.stream(url=stream_url)
        stream.parameter(name="call_sid", value=call_sid)
        response.append(connect)
        store.add_event(call_sid, "system", f"Returning TwiML media stream to {stream_url}")
        return Response(content=str(response), media_type="application/xml")
    except Exception as exc:
        logger.exception("Twilio voice webhook failed: %s", exc)
        response = VoiceResponse()
        response.say("Sorry, an application error occurred. Please try again later.")
        return Response(content=str(response), media_type="application/xml", status_code=200)


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
            greeting_overrides.pop(call_sid, None)
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
                custom_params = start_payload.get("customParameters") or {}
                call_sid = custom_params.get("call_sid") or call_sid
                session = call_sessions.get(call_sid)
                if session is None:
                    session = get_or_create_session(
                        call_sid=call_sid,
                        from_number=None,
                        to_number=None,
                        direction="outbound-api",
                    )
                await session.attach(websocket)
                await session.handle_twilio_message(payload)

            elif session is not None:
                await session.handle_twilio_message(payload)
                if event_type == "stop":
                    call_sessions.pop(session.call_sid, None)
                    prompt_overrides.pop(session.call_sid, None)
                    greeting_overrides.pop(session.call_sid, None)
                    break

    except WebSocketDisconnect:
        if session is not None:
            await session.close(status="disconnected")
            call_sessions.pop(session.call_sid, None)
            prompt_overrides.pop(session.call_sid, None)
            greeting_overrides.pop(session.call_sid, None)
    except Exception as exc:
        call_sid = session.call_sid if session is not None else query_call_sid
        if call_sid:
            store.add_event(call_sid, "error", f"Twilio media stream error: {exc}")
        if session is not None:
            await session.close(status="error")
            call_sessions.pop(session.call_sid, None)
            prompt_overrides.pop(session.call_sid, None)
            greeting_overrides.pop(session.call_sid, None)


@app.websocket("/ws/demo-call")
async def demo_call_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    call_sid = websocket.query_params.get("call_sid") or new_demo_call_sid()
    scenario_id = websocket.query_params.get("scenario_id") or None
    session = demo_sessions.get(call_sid)

    if session is None:
        resolved_prompt, _ = resolve_prompt_selection(
            system_prompt=prompt_overrides.get(call_sid),
            scenario_id=scenario_id,
        )
        session = DemoCallSession(
            settings=settings,
            call_store=store,
            intake_extractor=intake_extractor,
            call_sid=call_sid,
            system_prompt=resolved_prompt or settings.agent_system_prompt,
            scenario_id=scenario_id,
        )
        demo_sessions[call_sid] = session

    await session.attach(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            payload = json.loads(message)
            await session.handle_message(payload)
            if payload.get("type") == "stop":
                break
    except WebSocketDisconnect:
        await session.close(status="disconnected")
    except Exception as exc:
        message = format_elevenlabs_error(exc)
        store.add_event(call_sid, "error", message)
        await session.close(status="error")
        try:
            await websocket.send_text(
                json.dumps({"type": "status", "status": "error", "detail": message})
            )
        except Exception:
            pass
    finally:
        demo_sessions.pop(call_sid, None)
        prompt_overrides.pop(call_sid, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)