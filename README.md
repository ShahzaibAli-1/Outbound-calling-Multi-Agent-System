# Medical Voice Intake Agent

A Python voice agent for healthcare clinics that handles patient intake over the phone using **ElevenLabs Conversational AI**, **Twilio**, and optional **OpenAI** for browser testing and intake extraction.

Live phone calls are handled by a full ElevenLabs agent (speech recognition, reasoning, and voice in one pipeline). Twilio provides telephony and media streaming. OpenAI is optional and used only for the browser test bench and structured patient intake extraction from call transcripts.

## What is included

- FastAPI backend for health checks, browser testing, outbound dialing, Twilio webhooks, and the media WebSocket.
- ElevenLabs Conversational AI agent bridged to Twilio `mulaw/8000` media streams.
- Structured patient intake extraction stored per call (name, DOB, symptoms, allergies, insurance, and more).
- Built-in medical campaign scenarios including new patient intake, appointment intake, and symptom screening.
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
    call_session.py
    elevenlabs_agent.py
    twilio_audio_interface.py
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
3. Create an [ElevenLabs Conversational AI agent](https://elevenlabs.io/docs/eleven-agents/quickstart) for Medory Call Center / patient intake (see **ElevenLabs agent setup** below).
4. Copy your agent ID into `.env` as `ELEVENLABS_AGENT_ID`.
5. Make sure `.env` contains your ElevenLabs, Twilio, and public base URL values.
6. Start the FastAPI server.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Then open `http://localhost:3000`.

On Windows, prefer running Uvicorn without `--reload`. The reloader can fail with socket errors like `WinError 10013` or `WinError 10048` even when the app itself starts correctly without reload.

## ElevenLabs agent setup (required for live calls)

The error `1008 policy violation: Override for field 'prompt' is not allowed` means the app tried to send a prompt override, but your agent security settings block it. Use **one** of the two setups below.

### Recommended setup (dashboard config — works immediately)

Configure the agent fully in ElevenLabs and keep overrides **off** in `.env`:

```env
ELEVENLABS_OVERRIDE_PROMPT=false
ELEVENLABS_OVERRIDE_FIRST_MESSAGE=false
```

In the [ElevenLabs Agents dashboard](https://elevenlabs.io/app/conversational-ai):

1. **Create or open your agent** and copy its ID into `.env` as `ELEVENLABS_AGENT_ID`.

2. **Agent tab → System prompt**  
   Paste the contents of `test_system_prompt.txt` from this repo (Medory medical intake instructions).

3. **Agent tab → First message**  
   Set the opening line, for example:  
   `Hello, this is Medory Call Center. I can help with patient intake and appointment details. May I start with your full name?`

4. **Agent tab → Voice**  
   Pick a clear, professional voice for phone intake.

5. **Agent tab → Language**  
   Set to **English**.

6. **Advanced / Audio**  
   Default **PCM 16 kHz** in ElevenLabs is fine. This app converts automatically between Twilio **μ-law 8 kHz** and ElevenLabs **PCM 16 kHz**.

7. **Security tab**  
   Leave **Prompt override** and **First message override** disabled (default).  
   The app will connect using your dashboard prompt only — no policy violation.

8. **API key**  
   Create an API key at [ElevenLabs API settings](https://elevenlabs.io/app/settings/api-keys) and set `ELEVENLABS_API_KEY` in `.env`.

### Advanced setup (per-call prompts from this app)

Only use this if you need campaign scenarios or custom prompts from the dashboard UI on each call:

1. In ElevenLabs → your agent → **Security**, enable:
   - **Prompt override**
   - **First message override** (optional)

2. In `.env`:
   ```env
   ELEVENLABS_OVERRIDE_PROMPT=true
   ELEVENLABS_OVERRIDE_FIRST_MESSAGE=true
   ```

3. Restart the FastAPI server.

If either override is enabled in `.env` but not allowed in ElevenLabs Security, the call will fail with error `1008`.

## ElevenLabs + Twilio configuration

### Option A: Custom media-stream bridge (current app default)

- Point your Twilio incoming voice webhook to `/api/twilio/voice` on your public URL.
- The app bridges Twilio audio to your ElevenLabs agent over WebSocket.
- Outbound calls are started through this app's `/api/calls/outbound` endpoint.

### Option B: ElevenLabs native Twilio integration (optional)

- Import your Twilio number in the [ElevenLabs dashboard](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration).
- Set `ELEVENLABS_AGENT_PHONE_NUMBER_ID` in `.env`.
- Outbound calls can then be placed directly through ElevenLabs while inbound can be routed natively.

## Environment variables

Copy from `.env.example` if you want a clean template. The application reads the following keys from `.env`:

- `PORT`
- `PUBLIC_BASE_URL`
- `ELEVENLABS_API_KEY` (required for live calls)
- `ELEVENLABS_AGENT_ID` (required for live calls)
- `ELEVENLABS_AGENT_PHONE_NUMBER_ID` (optional, for native ElevenLabs outbound)
- `OPENAI_API_KEY` (optional, for browser tests and patient intake extraction)
- `OPENAI_MODEL`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `AGENT_NAME`
- `AGENT_GREETING`
- `AGENT_SYSTEM_PROMPT`

## Main routes

- `GET /` serves the dashboard.
- `GET /api/health` returns provider readiness and public endpoints.
- `GET /api/campaign-scenarios` returns the built-in medical intake and clinic campaign prompt catalog.
- `POST /api/chat/test` runs a browser-side prompt test through OpenAI (optional).
- `POST /api/calls/outbound` starts an outbound Twilio call.
- `GET /api/calls` returns recent call activity with attached patient intake records when available.
- `GET /api/patient-intakes` returns structured patient intake records captured from calls.
- `GET /api/patient-intakes/{call_sid}` returns the intake record for a specific call.
- `POST /api/twilio/voice` returns TwiML that connects the live media stream.
- `POST /api/twilio/status` stores Twilio call status updates.
- `WS /ws/twilio-media` accepts the Twilio bidirectional media stream.

## Notes

- Keep real secrets in `.env` only. Do not hardcode provider keys into Python or frontend files.
- The current implementation stores call logs and patient intake records in memory. Restarting the server clears the activity feed.
- Patient intake is extracted automatically from ElevenLabs call transcripts when `OPENAI_API_KEY` is configured.
- Twilio must be able to reach your app over a public HTTPS URL.
- For outbound calls and browser tests, a custom prompt overrides any selected campaign scenario. If both are blank, the app falls back to `test_system_prompt.txt`.
- On Twilio trial accounts, outbound calls only work to verified destination numbers.

## Fix "Application error occurred" on outbound calls

Twilio says **Application error occurred** when your phone answers but Twilio **cannot fetch valid TwiML** from your voice webhook (`PUBLIC_BASE_URL/api/twilio/voice`). The call often ends in a few seconds.

### Step-by-step fix

1. **Start the FastAPI server** (leave it running):
   ```powershell
   python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
   ```

2. **Start a public HTTPS tunnel** to port 3000 (in a second terminal):
   ```powershell
   ngrok http 3000
   ```
   Copy the **https** URL (e.g. `https://abc123.ngrok-free.app`).

3. **Update `.env`** with the new tunnel URL:
   ```env
   PUBLIC_BASE_URL=https://abc123.ngrok-free.app
   ```

4. **Restart the FastAPI server** so it reloads `.env` (required after any `.env` change).

5. **Verify the webhook** — open in a browser or run:
   ```powershell
   curl.exe -X POST "https://YOUR-NGROK-URL.ngrok-free.app/api/twilio/voice" -d "CallSid=test&From=%2B1&To=%2B1&Direction=outbound-api"
   ```
   You should get XML containing `<Response>` and `<Stream url="wss://...`.

6. **Check `/api/health`** — `public_url_status.voice_webhook_ok` must be `true`. If `false`, your tunnel URL is wrong or ngrok is not running.

7. **Twilio trial account** — verify the destination number (`+923...`) under [Twilio Verified Caller IDs](https://console.twilio.com/us1/develop/phone-numbers/manage/verified). Trial accounts can only call verified numbers.

8. **Enable Pakistan geo permissions** (if calling `+92`) in Twilio Console → Voice → Settings → Geo permissions.

9. **Place the outbound call again** from the dashboard.

### Common causes

| Symptom | Cause | Fix |
|--------|--------|-----|
| Error right when you pick up | Stale `PUBLIC_BASE_URL` / ngrok not running | Update `.env`, restart server |
| 4-second call then hangup | Webhook unreachable | Same as above |
| Call never connects | Unverified number on trial | Verify number in Twilio |
| No agent audio after connect | ElevenLabs agent/voice misconfigured | Check `/api/health` warnings |

Do **not** use an old ngrok URL after restarting ngrok — the subdomain changes each time on the free plan unless you use a reserved domain.
