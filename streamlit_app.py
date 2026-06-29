from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import streamlit as st


DEFAULT_BACKEND_BASE_URL = ""


def load_secret(name: str) -> str | None:
    try:
        value = st.secrets[name]
    except Exception:
        return None
    return str(value).strip() or None


def default_backend_base_url() -> str:
    return (
        load_secret("BACKEND_BASE_URL")
        or os.getenv("BACKEND_BASE_URL")
        or DEFAULT_BACKEND_BASE_URL
    ).rstrip("/")


def is_local_backend_url(value: str) -> bool:
    if not value:
        return False

    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return hostname in {"127.0.0.1", "localhost", "0.0.0.0"}


def api_request(method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    backend_base_url = st.session_state["backend_base_url"].rstrip("/")
    url = f"{backend_base_url}{path}"

    with httpx.Client(timeout=45.0) as client:
        response = client.request(method, url, json=payload)

    if response.is_error:
        detail = response.text
        try:
            response_json = response.json()
            if isinstance(response_json, dict):
                detail = str(response_json.get("detail") or response_json)
        except Exception:
            pass
        raise RuntimeError(detail)

    return response.json()


def get_scenarios() -> dict[str, list[dict[str, str]]]:
    payload = api_request("GET", "/api/campaign-scenarios")
    groups = payload.get("groups") or {}
    scenarios = payload.get("scenarios") or []
    if not groups.get("inbound") and not groups.get("outbound"):
        inbound = [scenario for scenario in scenarios if scenario.get("direction") == "inbound"]
        outbound = [scenario for scenario in scenarios if scenario.get("direction") == "outbound"]
        groups = {"inbound": inbound, "outbound": outbound}
    return groups


def get_health() -> dict[str, Any]:
    return api_request("GET", "/api/health")


def get_calls() -> list[dict[str, Any]]:
    return api_request("GET", "/api/calls").get("calls", [])


def find_scenario(scenarios: list[dict[str, str]], scenario_id: str) -> dict[str, str] | None:
    return next((scenario for scenario in scenarios if scenario.get("id") == scenario_id), None)


def prompt_preview(scenarios: list[dict[str, str]], scenario_id: str, custom_prompt: str) -> str:
    if custom_prompt.strip():
        return "Custom prompt override is active for this request."

    if not scenario_id:
        return "Using the backend default prompt from test_system_prompt.txt."

    scenario = find_scenario(scenarios, scenario_id)
    if scenario is None:
        return "Selected scenario details are unavailable."

    return f"{scenario['description']}\n\nPrompt:\n{scenario['prompt']}"


def format_timestamp(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %d, %Y %I:%M %p")
    except ValueError:
        return value


st.set_page_config(
    page_title="Medory Call Center",
    page_icon="phone",
    layout="wide",
)

st.title("Medory Call Center")
st.caption(
    "Deploy this Streamlit app as the operator dashboard. Keep the Twilio webhook and media-stream backend on the FastAPI service."
)

if "backend_base_url" not in st.session_state:
    st.session_state["backend_base_url"] = default_backend_base_url()

with st.sidebar:
    st.header("Connection")
    st.session_state["backend_base_url"] = st.text_input(
        "FastAPI backend URL",
        value=st.session_state["backend_base_url"],
        help="Example: https://your-backend.example.com",
    ).strip()
    if st.button("Refresh data", use_container_width=True):
        st.rerun()

    st.info(
        "Twilio must point to the FastAPI backend URL, not the Streamlit app URL. "
        "Use this dashboard for browser testing, outbound call control, and call monitoring."
    )

if not st.session_state["backend_base_url"]:
    st.error(
        "No backend URL is configured. Set BACKEND_BASE_URL in Streamlit secrets or enter your public FastAPI backend URL in the sidebar."
    )
    st.code('BACKEND_BASE_URL = "https://your-backend-domain.example.com"', language="toml")
    st.stop()

if is_local_backend_url(st.session_state["backend_base_url"]):
    st.warning(
        "This dashboard is pointing to a local backend URL. That only works on your own machine. "
        "On Streamlit Cloud, localhost points to the Streamlit container itself, not your FastAPI backend."
    )

health: dict[str, Any] | None = None
scenario_groups: dict[str, list[dict[str, str]]] = {"inbound": [], "outbound": []}
calls: list[dict[str, Any]] = []

try:
    health = get_health()
    scenario_groups = get_scenarios()
    calls = get_calls()
except Exception as exc:
    st.error(f"Unable to reach the backend at {st.session_state['backend_base_url']}: {exc}")
    if is_local_backend_url(st.session_state["backend_base_url"]):
        st.info(
            "Fix: deploy the FastAPI backend on a public host such as Render, Railway, Fly.io, or your own VM, "
            "then set BACKEND_BASE_URL in Streamlit Cloud secrets to that public backend URL."
        )
        st.code('BACKEND_BASE_URL = "https://your-backend-domain.example.com"', language="toml")
    st.stop()

all_scenarios = scenario_groups.get("inbound", []) + scenario_groups.get("outbound", [])
outbound_scenarios = scenario_groups.get("outbound", [])
inbound_scenarios = scenario_groups.get("inbound", [])

status_columns = st.columns(4)
status_columns[0].metric("Agent", str(health.get("agent") or "Unknown"))
status_columns[1].metric("ElevenLabs", "Ready" if health["providers"]["elevenlabs"] else "Missing")
status_columns[2].metric("OpenAI", "Ready" if health["providers"]["openai"] else "Missing")
status_columns[3].metric("Twilio", "Ready" if health["providers"]["twilio"] else "Missing")

if health.get("warnings"):
    for warning in health["warnings"]:
        st.warning(str(warning))

tabs = st.tabs(["Outbound call", "Browser test", "Recent calls", "System"])

with tabs[0]:
    st.subheader("Launch an outbound call")
    with st.form("outbound-call-form"):
        to_number = st.text_input("Destination phone number", placeholder="+1 202 555 0118")
        scenario_options = {"Default outbound scenario": ""}
        scenario_options.update({scenario["label"]: scenario["id"] for scenario in outbound_scenarios})
        scenario_label = st.selectbox("Outbound scenario", list(scenario_options.keys()))
        custom_prompt = st.text_area(
            "Custom prompt override",
            placeholder="Optional custom prompt. If you enter one, it overrides the selected scenario.",
            height=180,
        )
        st.caption(prompt_preview(all_scenarios, scenario_options[scenario_label], custom_prompt))
        submit_outbound = st.form_submit_button("Place outbound call")

    if submit_outbound:
        try:
            result = api_request(
                "POST",
                "/api/calls/outbound",
                payload={
                    "to_number": to_number.strip(),
                    "scenario_id": scenario_options[scenario_label] or None,
                    "system_prompt": custom_prompt.strip() or None,
                },
            )
            st.success(
                f"Call {result['call_sid']} queued with status {result['status']} using {result['prompt_source']}."
            )
        except Exception as exc:
            st.error(f"Outbound call failed: {exc}")

with tabs[1]:
    st.subheader("Test the prompt in-browser")
    with st.form("browser-test-form"):
        message = st.text_area(
            "Test message",
            placeholder="Ask the agent something like a real caller would.",
            height=120,
        )
        chat_direction = st.selectbox("Call direction", ["Inbound", "Outbound"], key="chat-direction")
        chat_scenario_list = inbound_scenarios if chat_direction == "Inbound" else outbound_scenarios
        chat_scenario_options = {"Default scenario": ""}
        chat_scenario_options.update({scenario["label"]: scenario["id"] for scenario in chat_scenario_list})
        chat_scenario_label = st.selectbox("Scenario", list(chat_scenario_options.keys()), key="chat-scenario")
        chat_custom_prompt = st.text_area(
            "Custom prompt override",
            placeholder="Optional custom prompt. If you enter one, it overrides the selected scenario.",
            height=180,
            key="chat-prompt",
        )
        st.caption(prompt_preview(all_scenarios, chat_scenario_options[chat_scenario_label], chat_custom_prompt))
        submit_test = st.form_submit_button("Run browser test")

    if submit_test:
        try:
            result = api_request(
                "POST",
                "/api/chat/test",
                payload={
                    "message": message.strip(),
                    "scenario_id": chat_scenario_options[chat_scenario_label] or None,
                    "system_prompt": chat_custom_prompt.strip() or None,
                },
            )
            st.success("Browser test completed.")
            st.write(result["answer"])
        except Exception as exc:
            st.error(f"Browser test failed: {exc}")

with tabs[2]:
    st.subheader("Recent calls")
    if not calls:
        st.info("No calls have been placed yet.")
    for call in calls[:10]:
        title = f"{call.get('from_number') or 'Unknown'} -> {call.get('to_number') or 'Unknown'} | {call.get('status', 'unknown')}"
        with st.expander(title, expanded=False):
            st.write(f"SID: {call.get('sid', 'unknown')}")
            st.write(f"Direction: {call.get('direction', 'unknown')}")
            intake = call.get("patient_intake")
            if intake:
                st.markdown("**Patient intake**")
                intake_fields = [
                    ("Status", intake.get("intake_status")),
                    ("Name", intake.get("full_name")),
                    ("DOB", intake.get("date_of_birth")),
                    ("Phone", intake.get("phone_number")),
                    ("Reason for visit", intake.get("reason_for_visit")),
                    ("Symptoms", intake.get("symptoms")),
                    ("Allergies", intake.get("allergies")),
                    ("Medications", intake.get("current_medications")),
                    ("Insurance", intake.get("insurance_provider")),
                    ("Member ID", intake.get("insurance_member_id")),
                ]
                for label, value in intake_fields:
                    if value:
                        st.write(f"{label}: {value}")
            for event in reversed(call.get("events", [])[-12:]):
                st.markdown(
                    f"**{event.get('type', 'status').upper()}**  \\n+{event.get('text', '')}  \\n+{format_timestamp(event.get('timestamp'))}"
                )

with tabs[3]:
    st.subheader("System")
    st.write(f"Phone number: {health.get('phone_number') or 'Not configured'}")
    st.write(f"Public base URL: {health.get('public_base_url')}")
    st.write(f"Voice webhook: {health.get('voice_webhook')}")
    st.write(f"Media stream: {health.get('media_stream')}")
    st.write("Built-in scenarios:")
    st.markdown("**Inbound**")
    for scenario in inbound_scenarios:
        st.markdown(f"- **{scenario['label']}**: {scenario['description']}")
    st.markdown("**Outbound**")
    for scenario in outbound_scenarios:
        st.markdown(f"- **{scenario['label']}**: {scenario['description']}")
