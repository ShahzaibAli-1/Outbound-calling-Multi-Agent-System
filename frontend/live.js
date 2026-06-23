import { AgentAudioPlayer, MicCapture, pcmBufferToBase64 } from "./audio-player.js";

const params = new URLSearchParams(window.location.search);
const callSid = params.get("call_sid");
const scenarioId = params.get("scenario_id") || "";
const autostart = params.get("autostart") === "1";

const state = {
    call: null,
    demoActive: false,
    socket: null,
    mic: null,
    player: new AgentAudioPlayer(),
    seconds: 0,
    timer: null,
    pollTimer: null,
    renderedEvents: 0,
};

const elements = {
    liveTag: document.querySelector("#liveTag"),
    callerLine: document.querySelector("#callerLine"),
    callTimer: document.querySelector("#callTimer"),
    statusLine: document.querySelector("#statusLine"),
    audioHint: document.querySelector("#audioHint"),
    transcriptArea: document.querySelector("#transcriptArea"),
    intakeFields: document.querySelector("#intakeFields"),
    endCallBtn: document.querySelector("#endCallBtn"),
    deletePatientBtn: document.querySelector("#deletePatientBtn"),
    enableAudioBtn: document.querySelector("#enableAudioBtn"),
    metaLine: document.querySelector("#metaLine"),
    errorBanner: document.querySelector("#errorBanner"),
};

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

function formatTime(totalSeconds) {
    const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
    const seconds = String(totalSeconds % 60).padStart(2, "0");
    return `${minutes}:${seconds}`;
}

function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

function avatarColor(seed) {
    const palette = ["#B3463F", "#5C7FB8", "#8C6526", "#3F7D5C", "#0B1220"];
    let hash = 0;
    for (let i = 0; i < seed.length; i += 1) hash = (hash + seed.charCodeAt(i)) % palette.length;
    return palette[hash];
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
    });
    const raw = await response.text();
    if (!response.ok) {
        try {
            const parsed = JSON.parse(raw);
            if (typeof parsed.detail === "string") throw new Error(parsed.detail);
        } catch (error) {
            if (error instanceof Error && error.message !== raw) throw error;
        }
        throw new Error(raw || `Request failed: ${response.status}`);
    }
    return raw ? JSON.parse(raw) : {};
}

function wsUrl() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const query = new URLSearchParams({ call_sid: callSid });
    if (scenarioId) query.set("scenario_id", scenarioId);
    return `${protocol}://${window.location.host}/ws/demo-call?${query.toString()}`;
}

function isDemoCall() {
    return callSid?.startsWith("demo_");
}

function callTitle(call) {
    if (call?.patient_intake?.full_name) return call.patient_intake.full_name;
    if (call?.to_number) return call.to_number;
    if (call?.call_type === "demo") return "Demo intake call";
    return call?.sid || "Unknown caller";
}

function setStatus(text) {
    elements.statusLine.textContent = text;
}

function parseServerError(text) {
    if (!text) return "Unknown error";
    const match = String(text).match(/'message': '([^']+)'/);
    if (match) return match[1];
    if (text.includes("document_not_found") || text.includes("agent not found")) {
        return "ElevenLabs agent not found for this API key. Update ELEVENLABS_AGENT_ID in .env.";
    }
    return String(text).replace(/^Demo call error:\s*/i, "").slice(0, 280);
}

function showError(message) {
    const detail = parseServerError(message);
    elements.errorBanner.textContent = detail;
    elements.errorBanner.classList.remove("hidden");
    elements.liveTag.textContent = "Call failed";
    elements.callerLine.innerHTML = `Demo intake call <span>error</span>`;
    setStatus(detail);
}

function hideError() {
    elements.errorBanner.classList.add("hidden");
    elements.errorBanner.textContent = "";
}

function startTimer() {
    if (state.timer) return;
    state.timer = window.setInterval(() => {
        state.seconds += 1;
        elements.callTimer.textContent = formatTime(state.seconds);
    }, 1000);
}

function stopTimer() {
    if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
    }
}

function renderHeader(call) {
    const live = call?.status === "connected" || state.demoActive;
    elements.liveTag.innerHTML = live
        ? '<span class="rec-dot"></span>Live · ' + (call?.call_type === "demo" ? "Demo" : call?.direction || "Call")
        : "Call ended";
    elements.callerLine.innerHTML = `${escapeHtml(callTitle(call))} <span>${escapeHtml(call?.scenario_id || call?.status || "")}</span>`;
}

function renderIntake(call) {
    const intake = call?.patient_intake;
    if (!intake) {
        elements.intakeFields.innerHTML = '<div class="empty-state" style="font-size:12px;color:var(--text-faint);font-style:italic;">No intake captured yet.</div>';
        return;
    }
    const fields = [
        ["Status", intake.intake_status],
        ["Name", intake.full_name],
        ["DOB", intake.date_of_birth],
        ["Phone", intake.phone_number],
        ["Reason", intake.reason_for_visit],
        ["Symptoms", intake.symptoms],
        ["Allergies", intake.allergies],
        ["Medications", intake.current_medications],
        ["Insurance", intake.insurance_provider],
    ].filter(([, value]) => value);
    if (!fields.length) {
        elements.intakeFields.innerHTML = '<div class="empty-state" style="font-size:12px;color:var(--text-faint);font-style:italic;">Intake in progress…</div>';
        return;
    }
    elements.intakeFields.innerHTML = fields.map(([label, value]) => `
        <div class="intake-row"><strong>${escapeHtml(label)}</strong>${escapeHtml(value)}</div>
    `).join("");
}

function renderTranscript(call) {
    const events = (call?.events || []).filter((event) => ["user", "assistant"].includes(event.type));
    if (!events.length) return;

    if (state.renderedEvents === 0) {
        elements.transcriptArea.innerHTML = "";
    }

    for (let index = state.renderedEvents; index < events.length; index += 1) {
        const event = events[index];
        const isAgent = event.type === "assistant";
        const who = isAgent ? "Medory" : "Patient";
        const avatar = isAgent ? "M" : initials(callTitle(call));
        const bg = isAgent ? "#0B1220" : avatarColor(callTitle(call));
        const timeLabel = formatTime(Math.max(0, state.seconds - (events.length - index) * 3));
        elements.transcriptArea.insertAdjacentHTML("beforeend", `
            <div class="msg ${isAgent ? "right" : ""}">
                <div class="msg-avatar" style="background:${bg};">${escapeHtml(avatar)}</div>
                <div class="msg-content">
                    <div class="msg-who">${escapeHtml(who)}${isAgent ? "" : ` · ${escapeHtml(timeLabel)}`}</div>
                    <div class="msg-bubble">${escapeHtml(event.text)}</div>
                </div>
            </div>
        `);
    }
    state.renderedEvents = events.length;
    elements.transcriptArea.scrollTop = elements.transcriptArea.scrollHeight;
    elements.metaLine.textContent = `${events.length} messages captured · encrypted · audit logged`;
}

async function refreshCall() {
    if (!callSid) return null;
    const data = await fetchJson(`/api/calls/${encodeURIComponent(callSid)}`);
    state.call = data.call;
    renderHeader(state.call);
    renderTranscript(state.call);
    renderIntake(state.call);
    const errorEvent = (state.call.events || []).find((event) => event.type === "error");
    if (state.call.status === "error" && errorEvent) {
        showError(errorEvent.text);
    }
    return state.call;
}

async function enableAudio() {
    await state.player.ensureReady();
    elements.audioHint.classList.remove("hidden");
    elements.enableAudioBtn.classList.add("hidden");
    setStatus("Audio enabled — you should hear the agent speak.");
}

async function connectDemoCall() {
    if (!isDemoCall()) return;
    if (state.call?.status === "error") {
        showError(state.call.events?.find((event) => event.type === "error")?.text || "This demo call already failed. Start a new demo call from the dashboard.");
        return;
    }

    hideError();
    state.demoActive = true;
    setStatus("Connecting to voice agent…");
    startTimer();
    renderHeader({ sid: callSid, call_type: "demo", status: "connected", scenario_id: scenarioId });

    await enableAudio();

    const socket = new WebSocket(wsUrl());
    state.socket = socket;

    socket.onopen = async () => {
        setStatus("Agent connected — speak into your microphone.");
        socket.send(JSON.stringify({ type: "start" }));

        state.mic = new MicCapture((pcmBuffer) => {
            if (!state.demoActive || socket.readyState !== WebSocket.OPEN) return;
            socket.send(JSON.stringify({ type: "audio", payload: pcmBufferToBase64(pcmBuffer) }));
        });

        try {
            await state.mic.start();
        } catch (error) {
            setStatus(`Microphone error: ${error.message}`);
        }
    };

    socket.onmessage = async (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type === "transcript" && payload.role !== "system") {
            await refreshCall();
        } else if (payload.type === "status" && payload.status === "connected") {
            await refreshCall();
        } else if (payload.type === "agent_audio") {
            await state.player.playBase64Pcm(payload.payload, payload.sample_rate || 16000);
            elements.audioHint.classList.remove("hidden");
        } else if (payload.type === "status") {
            if (payload.status === "error") {
                showError(payload.detail || payload.status);
            } else {
                setStatus(payload.detail || payload.status);
            }
        } else if (payload.type === "clear_audio") {
            state.player.clear();
        }
    };

    socket.onclose = async () => {
        state.demoActive = false;
        state.mic?.stop();
        state.mic = null;
        state.player.clear();
        stopTimer();
        await refreshCall();
        if (state.call?.status !== "error") {
            setStatus("Call ended.");
            renderHeader({ ...state.call, status: "completed" });
        }
    };

    socket.onerror = () => {
        setStatus("Connection error — verify ElevenLabs API key and agent ID.");
    };
}

async function endDemoCall(sendStop = true) {
    state.demoActive = false;
    if (sendStop && state.socket?.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify({ type: "stop" }));
        state.socket.close();
    }
    state.socket = null;
    state.mic?.stop();
    state.mic = null;
    state.player.clear();
    stopTimer();
    setStatus("Call ended.");
    renderHeader({ ...state.call, status: "completed" });
    await refreshCall();
}

function startPhonePolling() {
    state.pollTimer = window.setInterval(() => {
        refreshCall().catch(console.error);
    }, 2000);
}

async function bootstrap() {
    if (!callSid) {
        setStatus("Missing call_sid in URL.");
        elements.endCallBtn.disabled = true;
        return;
    }

    elements.enableAudioBtn.addEventListener("click", () => enableAudio().catch(console.error));
    elements.endCallBtn.addEventListener("click", () => {
        if (isDemoCall()) endDemoCall().catch(console.error);
        else window.location.href = "/";
    });
    elements.deletePatientBtn?.addEventListener("click", async () => {
        if (!callSid) return;
        const label = callTitle(state.call);
        if (!window.confirm(`Delete patient record for "${label}"? This cannot be undone.`)) return;
        if (state.demoActive) await endDemoCall();
        await deleteCallRequest(callSid);
        window.location.href = "/";
    });

    async function deleteCallRequest(sid) {
        const encoded = encodeURIComponent(sid);
        try {
            return await fetchJson(`/api/calls/${encoded}`, { method: "DELETE" });
        } catch (error) {
            const message = String(error.message || "");
            if (!message.includes("Method Not Allowed") && !message.includes("405")) {
                throw error;
            }
            return await fetchJson(`/api/calls/${encoded}/delete`, { method: "POST" });
        }
    }

    try {
        await refreshCall();
    } catch (error) {
        setStatus(`Call not found: ${error.message}`);
    }

    if (isDemoCall() && autostart) {
        await connectDemoCall();
        return;
    }

    if (isDemoCall()) {
        setStatus("Press Enable audio, then this page will connect when you start speaking from the dashboard.");
        elements.enableAudioBtn.classList.remove("hidden");
        await enableAudio();
        await connectDemoCall();
        return;
    }

    setStatus("Monitoring phone call transcript (updates every 2 seconds).");
    elements.enableAudioBtn.classList.add("hidden");
    if (state.call?.status === "connected") startTimer();
    startPhonePolling();
}

bootstrap().catch((error) => {
    setStatus(`Failed to load live call: ${error.message}`);
});

window.addEventListener("beforeunload", () => {
    if (state.demoActive) endDemoCall().catch(() => {});
});
