# AI Voice Agent

A Python voice agent that answers phone calls with OpenAI, Deepgram, and Twilio. The backend handles Twilio voice webhooks and media streams, Deepgram provides live speech-to-text and text-to-speech, and OpenAI generates the spoken reply. A browser dashboard is included for outbound calling, prompt testing, and call monitoring.

## What is included

- FastAPI backend for health checks, browser testing, outbound dialing, Twilio webhooks, and the media WebSocket.
- Deepgram live transcription bridge for Twilio `mulaw/8000` audio.
- Deepgram TTS playback back into the live Twilio call.
- OpenAI-backed reply generation tuned for short spoken answers.
- Built-in outbound campaign scenarios with editable custom prompt overrides.
- Static frontend dashboard served directly by the Python app.
- Streamlit operator dashboard for Streamlit Cloud deployment.
- `.env.example` to document the required environment variables without exposing secrets.

## Project structure

```text
app/
  config.py
  main.py
  models.py
  store.py
  services/
frontend/
  index.html
  styles.css
  app.js
requirements.txt
README.md
```

## Setup

1. Create a virtual environment.
2. Install dependencies.
3. Make sure `.env` contains your OpenAI, Deepgram, Twilio, and public base URL values.
4. Start the FastAPI server.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Then open `http://localhost:3000`.

On Windows, prefer running Uvicorn without `--reload`. The reloader can fail with socket errors like `WinError 10013` or `WinError 10048` even when the app itself starts correctly without reload.

## Streamlit deployment

Use Streamlit for the operator dashboard only. The Twilio webhook, call control, and media-stream backend must still run on the FastAPI app because Streamlit Cloud does not expose arbitrary webhook and WebSocket routes for Twilio.

1. Deploy the FastAPI backend somewhere that supports HTTP and WSS routes, such as Render, Railway, Fly.io, or your own VM.
2. Set the backend environment variables there:
  - `PUBLIC_BASE_URL=https://your-backend-domain.example.com`
  - `OPENAI_API_KEY`
  - `DEEPGRAM_API_KEY`
  - `TWILIO_ACCOUNT_SID`
  - `TWILIO_AUTH_TOKEN`
  - `TWILIO_PHONE_NUMBER`
3. Deploy this repository to Streamlit Cloud.
4. In Streamlit Cloud secrets, set:
  - `BACKEND_BASE_URL = "https://your-backend-domain.example.com"`
5. Use `streamlit_app.py` as the app entrypoint.
6. Point Twilio voice webhooks to the backend URL, not the Streamlit URL.

If your deployed Streamlit app shows a connection error to `http://127.0.0.1:3000`, that means `BACKEND_BASE_URL` was not configured. On Streamlit Cloud, `127.0.0.1` points to the Streamlit container itself, not your FastAPI backend.

Example Streamlit secret:

```toml
BACKEND_BASE_URL = "https://your-backend-domain.example.com"
```

## Twilio configuration

- Point your Twilio incoming voice webhook to `/api/twilio/voice` on your public URL.
- If you are testing locally, expose the app with a tunnel such as `ngrok http 3000` and copy that HTTPS URL into `PUBLIC_BASE_URL`.
- Outbound calls use the same webhook automatically through the API.

## Environment variables

Copy from `.env.example` if you want a clean template. The application reads the following keys from `.env`:

- `PORT`
- `PUBLIC_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `DEEPGRAM_API_KEY`
- `DEEPGRAM_STT_MODEL`
- `DEEPGRAM_TTS_MODEL`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `AGENT_NAME`
- `AGENT_GREETING`
- `AGENT_SYSTEM_PROMPT`

## Main routes

- `GET /` serves the dashboard.
- `GET /api/health` returns provider readiness and public endpoints.
- `GET /api/campaign-scenarios` returns the built-in outbound campaign prompt catalog.
- `POST /api/chat/test` runs a browser-side prompt test through OpenAI.
- `POST /api/calls/outbound` starts an outbound Twilio call.
- `GET /api/calls` returns recent call activity.
- `POST /api/twilio/voice` returns TwiML that connects the live media stream.
- `POST /api/twilio/status` stores Twilio call status updates.
- `WS /ws/twilio-media` accepts the Twilio bidirectional media stream.

## Notes

- Keep real secrets in `.env` only. Do not hardcode provider keys into Python or frontend files.
- The current implementation stores call logs in memory. Restarting the server clears the activity feed.
- Twilio must be able to reach your app over a public HTTPS URL.
- For outbound calls and browser tests, a custom prompt overrides any selected campaign scenario. If both are blank, the app falls back to `test_system_prompt.txt`.
- On Twilio trial accounts, outbound calls only work to verified destination numbers.
- Streamlit Cloud hosts the operator dashboard, but the Twilio webhook and media-stream routes must stay on the FastAPI backend.