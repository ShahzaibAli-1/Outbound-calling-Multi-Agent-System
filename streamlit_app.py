from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
import streamlit as st


DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:3000"


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


def get_scenarios() -> list[dict[str, str]]:
    return api_request("GET", "/api/campaign-scenarios").get("scenarios", [])


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
    page_title="AI Voice Agent Control Panel",
    page_icon="phone",
    layout="wide",
)

st.title("AI Voice Agent Control Panel")
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

health: dict[str, Any] | None = None
scenarios: list[dict[str, str]] = []
calls: list[dict[str, Any]] = []

try:
    health = get_health()
    scenarios = get_scenarios()
    calls = get_calls()
except Exception as exc:
    st.error(f"Unable to reach the backend at {st.session_state['backend_base_url']}: {exc}")
    st.stop()

status_columns = st.columns(4)
status_columns[0].metric("Agent", str(health.get("agent") or "Unknown"))
status_columns[1].metric("OpenAI", "Ready" if health["providers"]["openai"] else "Missing")
status_columns[2].metric("Deepgram", "Ready" if health["providers"]["deepgram"] else "Missing")
status_columns[3].metric("Twilio", "Ready" if health["providers"]["twilio"] else "Missing")

if health.get("warnings"):
    for warning in health["warnings"]:
        st.warning(str(warning))

tabs = st.tabs(["Outbound call", "Browser test", "Recent calls", "System"])

with tabs[0]:
    st.subheader("Launch an outbound call")
    with st.form("outbound-call-form"):
        to_number = st.text_input("Destination phone number", placeholder="+1 202 555 0118")
        scenario_options = {"Default FAST prompt": ""}
        scenario_options.update({scenario["label"]: scenario["id"] for scenario in scenarios})
        scenario_label = st.selectbox("Campaign scenario", list(scenario_options.keys()))
        custom_prompt = st.text_area(
            "Custom prompt override",
            placeholder="Optional custom prompt. If you enter one, it overrides the selected scenario.",
            height=180,
        )
        st.caption(prompt_preview(scenarios, scenario_options[scenario_label], custom_prompt))
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
        chat_scenario_options = {"Default FAST prompt": ""}
        chat_scenario_options.update({scenario["label"]: scenario["id"] for scenario in scenarios})
        chat_scenario_label = st.selectbox("Campaign scenario", list(chat_scenario_options.keys()), key="chat-scenario")
        chat_custom_prompt = st.text_area(
            "Custom prompt override",
            placeholder="Optional custom prompt. If you enter one, it overrides the selected scenario.",
            height=180,
            key="chat-prompt",
        )
        st.caption(prompt_preview(scenarios, chat_scenario_options[chat_scenario_label], chat_custom_prompt))
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
    for scenario in scenarios:
        st.markdown(f"- **{scenario['label']}**: {scenario['description']}")
