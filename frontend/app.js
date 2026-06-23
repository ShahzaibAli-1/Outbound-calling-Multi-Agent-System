const state = {
    scenarios: [],
    calls: [],
    selectedCallSid: null,
    sidebarFilter: "all",
    refreshTimer: null,
};

const elements = {
    greetingTitle: document.querySelector("#greetingTitle"),
    staffName: document.querySelector("#staffName"),
    subline: document.querySelector("#subline"),
    globalSearch: document.querySelector("#globalSearch"),
    refreshButton: document.querySelector("#refreshButton"),
    queueDate: document.querySelector("#queueDate"),
    queueSummary: document.querySelector("#queueSummary"),
    sidebarCalls: document.querySelector("#sidebarCalls"),
    outcomesList: document.querySelector("#outcomesList"),
    scenariosList: document.querySelector("#scenariosList"),
    liveTranscript: document.querySelector("#liveTranscript"),
    liveIntake: document.querySelector("#liveIntake"),
    liveIntakeFields: document.querySelector("#liveIntakeFields"),
    liveStatusPill: document.querySelector("#liveStatusPill"),
    liveTimer: document.querySelector("#liveTimer"),
    liveCallTitle: document.querySelector("#liveCallTitle"),
    liveCallType: document.querySelector("#liveCallType"),
    liveCallReason: document.querySelector("#liveCallReason"),
    liveCallsPill: document.querySelector("#liveCallsPill"),
    metricCalls: document.querySelector("#metricCalls"),
    metricDemo: document.querySelector("#metricDemo"),
    metricComplete: document.querySelector("#metricComplete"),
    metricProgress: document.querySelector("#metricProgress"),
    demoDialog: document.querySelector("#demoDialog"),
    phoneDialog: document.querySelector("#phoneDialog"),
    settingsDialog: document.querySelector("#settingsDialog"),
    openDemoBtn: document.querySelector("#openDemoBtn"),
    openPhoneBtn: document.querySelector("#openPhoneBtn"),
    openSettingsBtn: document.querySelector("#openSettingsBtn"),
    openSettingsBtn2: document.querySelector("#openSettingsBtn2"),
    openDemoBtn2: document.querySelector("#openDemoBtn2"),
    openPhoneBtn2: document.querySelector("#openPhoneBtn2"),
    openLiveBtn: document.querySelector("#openLiveBtn"),
    deleteCallBtn: document.querySelector("#deleteCallBtn"),
    startDemoBtn: document.querySelector("#startDemoBtn"),
    endDemoBtn: document.querySelector("#endDemoBtn"),
    demoStatus: document.querySelector("#demoStatus"),
    demoScenario: document.querySelector("#demoScenario"),
    demoTranscript: document.querySelector("#demoTranscript"),
    callForm: document.querySelector("#callForm"),
    callScenario: document.querySelector("#callScenario"),
    callPrompt: document.querySelector("#callPrompt"),
    toNumber: document.querySelector("#toNumber"),
    callFeedback: document.querySelector("#callFeedback"),
    providerElevenLabs: document.querySelector("#providerElevenLabs"),
    providerOpenAI: document.querySelector("#providerOpenAI"),
    providerTwilio: document.querySelector("#providerTwilio"),
    publicBaseUrl: document.querySelector("#publicBaseUrl"),
    voiceWebhook: document.querySelector("#voiceWebhook"),
    phoneNumber: document.querySelector("#phoneNumber"),
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

function formatTimestamp(value) {
    if (!value) return "";
    return new Intl.DateTimeFormat([], { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatQueueDate() {
    return new Intl.DateTimeFormat([], { weekday: "short", day: "numeric", month: "short" }).format(new Date());
}

function greeting() {
    const hour = new Date().getHours();
    if (hour < 12) return "Good morning";
    if (hour < 17) return "Good afternoon";
    return "Good evening";
}

function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

function avatarColor(seed) {
    const palette = ["#B3463F", "#5C7FB8", "#8C6526", "#3F7D5C", "#6B7690", "#2E5A93"];
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
            const detail = parsed.detail;
            if (typeof detail === "string") throw new Error(detail);
            if (Array.isArray(detail)) throw new Error(detail.map((item) => item.msg || item).join(", "));
        } catch (error) {
            if (error instanceof Error && error.message && error.message !== raw) throw error;
        }
        throw new Error(raw || `Request failed: ${response.status}`);
    }
    return raw ? JSON.parse(raw) : {};
}

function setProviderStatus(node, ready) {
    if (!node) return;
    node.textContent = ready ? "Ready" : "Missing";
    node.className = ready ? "status-ready" : "status-missing";
}

function populateScenarioSelect(selectNode, defaultLabel) {
    if (!selectNode) return;
    selectNode.innerHTML = [
        `<option value="">${escapeHtml(defaultLabel)}</option>`,
        ...state.scenarios.map((scenario) => `<option value="${escapeHtml(scenario.id)}">${escapeHtml(scenario.label)}</option>`),
    ].join("");
}

function getScenarioLabel(scenarioId) {
    return state.scenarios.find((item) => item.id === scenarioId)?.label || "Medical intake";
}

function callTitle(call) {
    const intake = call.patient_intake;
    if (intake?.full_name) return intake.full_name;
    if (call.to_number) return call.to_number;
    if (call.call_type === "demo") return "Demo intake call";
    return call.sid;
}

function callSubtitle(call) {
    const intake = call.patient_intake;
    if (intake?.reason_for_visit) return intake.reason_for_visit;
    const lastUser = [...(call.events || [])].reverse().find((event) => event.type === "user");
    if (lastUser) return lastUser.text;
    return getScenarioLabel(call.scenario_id);
}

function callTag(call) {
    if (call.status === "connected") return { cls: "live", label: "Live" };
    if (call.call_type === "demo") return { cls: "done", label: "Demo" };
    if (["completed", "disconnected"].includes(call.status)) return { cls: "done", label: "Done" };
    if (["queued", "ringing", "initiated"].includes(call.status)) return { cls: "queued", label: call.status };
    return { cls: "esc", label: call.status };
}

function filterCalls(calls) {
    const query = elements.globalSearch?.value.trim().toLowerCase() || "";
    return calls.filter((call) => {
        const matchesFilter =
            state.sidebarFilter === "all" ||
            (state.sidebarFilter === "live" && call.status === "connected") ||
            (state.sidebarFilter === "demo" && call.call_type === "demo") ||
            (state.sidebarFilter === "phone" && call.call_type === "phone");
        if (!matchesFilter) return false;
        const haystack = [
            call.sid,
            call.to_number,
            call.from_number,
            call.patient_intake?.full_name,
            call.patient_intake?.phone_number,
            getScenarioLabel(call.scenario_id),
        ].filter(Boolean).join(" ").toLowerCase();
        return !query || haystack.includes(query);
    });
}

function renderSidebarCalls() {
    const filtered = filterCalls(state.calls);
    const live = state.calls.filter((call) => call.status === "connected").length;
    const done = state.calls.filter((call) => ["completed", "disconnected"].includes(call.status)).length;
    elements.queueSummary.innerHTML = `<b>${state.calls.length} total</b> · ${done} completed · ${live} live · ${state.calls.filter((c) => c.call_type === "demo").length} demo`;
    elements.liveCallsPill.textContent = `${live} live`;

    if (!filtered.length) {
        elements.sidebarCalls.innerHTML = '<div class="empty-state">No matching calls.</div>';
        return;
    }

    const liveCalls = filtered.filter((call) => call.status === "connected");
    const otherCalls = filtered.filter((call) => call.status !== "connected");
    const sections = [];

    if (liveCalls.length) {
        sections.push('<div class="qgroup-label">Live now</div>');
        sections.push(...liveCalls.map((call) => renderQueueItem(call, true)));
    }
    if (otherCalls.length) {
        sections.push('<div class="qgroup-label">Earlier today</div>');
        sections.push(...otherCalls.map((call) => renderQueueItem(call, false)));
    }
    elements.sidebarCalls.innerHTML = sections.join("");
    elements.sidebarCalls.querySelectorAll("[data-call-sid]").forEach((node) => {
        node.addEventListener("click", () => selectCall(node.dataset.callSid));
    });
    elements.sidebarCalls.querySelectorAll("[data-delete-call-sid]").forEach((node) => {
        node.addEventListener("click", (event) => {
            event.stopPropagation();
            deleteCall(node.dataset.deleteCallSid).catch(console.error);
        });
    });
}

function renderQueueItem(call, isLive) {
    const title = callTitle(call);
    const tag = callTag(call);
    const direction = call.call_type === "demo" ? "Demo" : call.direction === "outbound" ? "Outbound" : "Inbound";
    return `
        <div class="qitem-row">
            <button class="qitem ${isLive ? "now" : ""} ${call.sid === state.selectedCallSid ? "sel" : ""}" data-call-sid="${escapeHtml(call.sid)}" type="button">
                <div class="qavatar" style="background:${avatarColor(title)};">${escapeHtml(initials(title))}</div>
                <div class="qinfo">
                    <div class="qname">${escapeHtml(title)}</div>
                    <div class="qmeta">${escapeHtml(direction)} · ${escapeHtml(callSubtitle(call))}</div>
                </div>
                <div class="qtag ${tag.cls}">${escapeHtml(tag.label)}</div>
            </button>
            <button class="qdelete" type="button" data-delete-call-sid="${escapeHtml(call.sid)}" title="Delete patient record" aria-label="Delete patient record">×</button>
        </div>
    `;
}

async function deleteCallRequest(callSid) {
    const encoded = encodeURIComponent(callSid);
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

async function deleteCall(callSid) {
    if (!callSid) return;
    const call = state.calls.find((item) => item.sid === callSid);
    const label = call ? callTitle(call) : callSid;
    if (!window.confirm(`Delete patient record for "${label}"? This cannot be undone.`)) {
        return;
    }
    await deleteCallRequest(callSid);
    if (state.selectedCallSid === callSid) {
        state.selectedCallSid = null;
    }
    await loadDashboard();
}

function outcomePill(call) {
    const status = call.patient_intake?.intake_status;
    if (status === "complete") return { cls: "resolved", label: "Resolved" };
    if (call.status === "connected") return { cls: "live", label: "Live" };
    if (call.call_type === "demo") return { cls: "booked", label: "Demo" };
    return { cls: "handoff", label: call.status };
}

function renderOutcomes() {
    const completed = state.calls.filter((call) => ["completed", "disconnected", "connected"].includes(call.status) || call.events?.length > 1);
    if (!completed.length) {
        elements.outcomesList.innerHTML = '<div class="empty-state">Completed calls will appear here.</div>';
        return;
    }
    elements.outcomesList.innerHTML = completed.slice(0, 6).map((call) => {
        const pill = outcomePill(call);
        return `
            <div class="row-item">
                <div>
                    <div class="row-name">${escapeHtml(callTitle(call))}</div>
                    <div class="row-sub">${escapeHtml(callSubtitle(call))} · ${escapeHtml(formatTimestamp(call.updated_at))}</div>
                </div>
                <div class="row-right"><span class="pill-out ${pill.cls}">${escapeHtml(pill.label)}</span></div>
            </div>
        `;
    }).join("");
}

function renderScenariosList() {
    if (!state.scenarios.length) {
        elements.scenariosList.innerHTML = '<div class="empty-state">No scenarios loaded.</div>';
        return;
    }
    elements.scenariosList.innerHTML = state.scenarios.slice(0, 6).map((scenario) => `
        <div class="row-item">
            <div>
                <div class="row-name">${escapeHtml(scenario.label)}</div>
                <div class="row-sub">${escapeHtml(scenario.description)}</div>
            </div>
            <div class="row-right">Intake</div>
        </div>
    `).join("");
}

function renderTranscript(target, call) {
    if (!target) return;
    if (!call) {
        target.innerHTML = '<div class="empty-state">Agent and patient transcript will appear here during a call.</div>';
        return;
    }
    const lines = (call.events || []).filter((event) => ["user", "assistant"].includes(event.type));
    if (!lines.length) {
        target.innerHTML = '<div class="empty-state">Waiting for conversation…</div>';
        return;
    }
    const last = lines[lines.length - 1];
    const roleLabel = last.type === "user" ? "Patient" : "Agent";
    target.innerHTML = `
        <span class="who">${escapeHtml(roleLabel)}:</span>
        "${escapeHtml(last.text)}"
        <div style="margin-top:10px;max-height:160px;overflow-y:auto;">
            ${lines.map((event) => `
                <div class="transcript-line ${escapeHtml(event.type)}">
                    <span class="role">${escapeHtml(event.type === "user" ? "Patient" : "Agent")}</span>
                    <p>${escapeHtml(event.text)}</p>
                </div>
            `).join("")}
        </div>
    `;
    target.scrollTop = target.scrollHeight;
}

function renderIntake(call) {
    const intake = call?.patient_intake;
    if (!intake || (!intake.full_name && !intake.reason_for_visit && intake.intake_status === "not_started")) {
        elements.liveIntake?.classList.add("hidden");
        return;
    }
    elements.liveIntake?.classList.remove("hidden");
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
        ["Member ID", intake.insurance_member_id],
    ].filter(([, value]) => value);
    elements.liveIntakeFields.innerHTML = fields.map(([label, value]) => `
        <div><strong>${escapeHtml(label)}</strong>${escapeHtml(value)}</div>
    `).join("");
}

function updateLiveHeader(call) {
    if (!call) {
        elements.liveCallTitle.textContent = "No active call";
        elements.liveCallType.textContent = "—";
        elements.liveCallReason.textContent = "Start a demo call or place an outbound call to begin.";
        elements.liveStatusPill.innerHTML = "Idle";
        return;
    }
    const isLive = call.status === "connected" || state.demoActive;
    elements.liveCallTitle.textContent = callTitle(call);
    elements.liveCallType.textContent = getScenarioLabel(call.scenario_id);
    elements.liveCallReason.textContent = callSubtitle(call);
    elements.liveStatusPill.innerHTML = isLive
        ? '<span class="pulse"></span>Live · ' + (call.call_type === "demo" ? "demo" : call.direction)
        : escapeHtml((call.call_type || "call").toUpperCase());
}

function selectCall(callSid) {
    state.selectedCallSid = callSid;
    const call = state.calls.find((item) => item.sid === callSid);
    renderSidebarCalls();
    renderTranscript(elements.liveTranscript, call);
    renderIntake(call);
    updateLiveHeader(call);
}

function appendTranscriptLine(target, role, text, mirrorLive = true) {
    if (!text || !target) return;
    const empty = target.querySelector(".empty-state");
    if (empty) empty.remove();
    const roleLabel = role === "user" ? "Patient" : role === "assistant" ? "Agent" : "System";
    target.insertAdjacentHTML("beforeend", `
        <div class="transcript-line ${escapeHtml(role)}">
            <span class="role">${escapeHtml(roleLabel)}</span>
            <p>${escapeHtml(text)}</p>
        </div>
    `);
    target.scrollTop = target.scrollHeight;
    if (mirrorLive && target !== elements.liveTranscript) {
        const call = state.calls.find((item) => item.sid === state.selectedCallSid) || { events: [] };
        const synthetic = {
            ...call,
            events: [...(call.events || []), { type: role, text }],
        };
        renderTranscript(elements.liveTranscript, synthetic);
        updateLiveHeader(synthetic);
    }
}

async function loadHealth() {
    const health = await fetchJson("/api/health");
    const name = health.staff_name || "Shahzaib";
    elements.staffName.textContent = name;
    elements.greetingTitle.innerHTML = `${greeting()}, <b>${escapeHtml(name)}.</b>`;
    elements.subline.textContent = `${formatQueueDate()} · Medory handles patient intake, follow-up, and front-desk calls.`;
    elements.publicBaseUrl.textContent = health.public_base_url;
    elements.voiceWebhook.textContent = health.voice_webhook;
    elements.phoneNumber.textContent = health.phone_number || "Not configured";
    setProviderStatus(elements.providerElevenLabs, health.providers?.elevenlabs);
    setProviderStatus(elements.providerOpenAI, health.providers?.openai);
    setProviderStatus(elements.providerTwilio, health.providers?.twilio);
}

async function loadScenarios() {
    const data = await fetchJson("/api/campaign-scenarios");
    state.scenarios = data.scenarios || [];
    populateScenarioSelect(elements.demoScenario, "Default medical intake");
    populateScenarioSelect(elements.callScenario, "Default medical intake");
    renderScenariosList();
}

async function loadDashboard() {
    const [statsData, callsData] = await Promise.all([
        fetchJson("/api/dashboard/stats"),
        fetchJson("/api/calls"),
    ]);
    state.calls = callsData.calls || [];
    elements.metricCalls.textContent = statsData.total_calls_today ?? 0;
    elements.metricDemo.textContent = statsData.demo_calls_today ?? 0;
    elements.metricComplete.textContent = statsData.intakes_complete ?? 0;
    elements.metricProgress.textContent = statsData.intakes_in_progress ?? 0;
    elements.queueDate.textContent = formatQueueDate();
    renderSidebarCalls();
    renderOutcomes();
    if (state.selectedCallSid && state.calls.some((call) => call.sid === state.selectedCallSid)) {
        selectCall(state.selectedCallSid);
    } else if (state.calls[0]) {
        selectCall(state.calls[0].sid);
    } else {
        updateLiveHeader(null);
    }
}

function livePageUrl(callSid, scenarioId = "", autostart = false) {
    const params = new URLSearchParams({ call_sid: callSid });
    if (scenarioId) params.set("scenario_id", scenarioId);
    if (autostart) params.set("autostart", "1");
    return `/live?${params.toString()}`;
}

function openLiveTranscript(callSid, autostart = false) {
    const call = state.calls.find((item) => item.sid === callSid);
    window.location.href = livePageUrl(callSid, call?.scenario_id || "", autostart);
}

async function startDemoCall() {
    const scenarioId = elements.demoScenario.value || "";
    elements.demoStatus.textContent = "Creating demo call…";
    const startData = await fetchJson("/api/demo-calls/start", {
        method: "POST",
        body: JSON.stringify({ scenario_id: scenarioId || null }),
    });
    elements.demoDialog.close();
    window.location.href = livePageUrl(startData.call_sid, scenarioId, true);
}

async function handlePhoneSubmit(event) {
    event.preventDefault();
    const toNumber = elements.toNumber.value.trim();
    if (!toNumber.startsWith("+")) {
        elements.callFeedback.textContent = "Use E.164 format starting with + (e.g. +12025550108).";
        return;
    }
    elements.callFeedback.textContent = "Queueing outbound call…";
    try {
        const data = await fetchJson("/api/calls/outbound", {
            method: "POST",
            body: JSON.stringify({
                to_number: toNumber,
                scenario_id: elements.callScenario.value || null,
                system_prompt: elements.callPrompt.value.trim() || null,
            }),
        });
        elements.callFeedback.textContent = `Call ${data.call_sid} queued (${data.prompt_source}).`;
        state.selectedCallSid = data.call_sid;
        await loadDashboard();
        setTimeout(() => {
            elements.phoneDialog.close();
            window.location.href = livePageUrl(data.call_sid);
        }, 900);
    } catch (error) {
        elements.callFeedback.textContent = error.message;
    }
}

function openDialog(dialog) {
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
}

function bindDialogs() {
    const openDemo = () => openDialog(elements.demoDialog);
    const openPhone = () => openDialog(elements.phoneDialog);
    const openSettings = () => openDialog(elements.settingsDialog);

    elements.openDemoBtn?.addEventListener("click", openDemo);
    elements.openDemoBtn2?.addEventListener("click", openDemo);
    elements.openPhoneBtn?.addEventListener("click", openPhone);
    elements.openPhoneBtn2?.addEventListener("click", openPhone);
    elements.openSettingsBtn?.addEventListener("click", openSettings);
    elements.openSettingsBtn2?.addEventListener("click", openSettings);
    elements.openLiveBtn?.addEventListener("click", () => {
        if (state.selectedCallSid) openLiveTranscript(state.selectedCallSid, false);
    });
    elements.deleteCallBtn?.addEventListener("click", () => {
        if (state.selectedCallSid) deleteCall(state.selectedCallSid).catch(console.error);
    });

    document.querySelectorAll("[data-close-demo]").forEach((node) => node.addEventListener("click", () => elements.demoDialog.close()));
    document.querySelectorAll("[data-close-phone]").forEach((node) => node.addEventListener("click", () => elements.phoneDialog.close()));
    document.querySelectorAll("[data-close-settings]").forEach((node) => node.addEventListener("click", () => elements.settingsDialog.close()));

    elements.startDemoBtn?.addEventListener("click", () => startDemoCall().catch((error) => {
        elements.demoStatus.textContent = error.message;
    }));
    elements.callForm?.addEventListener("submit", handlePhoneSubmit);
    elements.refreshButton?.addEventListener("click", () => loadDashboard().catch(console.error));
    elements.globalSearch?.addEventListener("input", renderSidebarCalls);

    document.querySelectorAll(".qtab").forEach((tab) => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".qtab").forEach((node) => node.classList.remove("active"));
            tab.classList.add("active");
            state.sidebarFilter = tab.dataset.filter || "all";
            renderSidebarCalls();
        });
    });
}

async function bootstrap() {
    elements.queueDate.textContent = formatQueueDate();
    bindDialogs();
    await loadScenarios();
    await loadHealth();
    await loadDashboard();
    state.refreshTimer = window.setInterval(() => {
        loadDashboard().catch(console.error);
    }, 5000);
}

bootstrap().catch((error) => {
    if (elements.liveTranscript) {
        elements.liveTranscript.innerHTML = `<div class="empty-state">Initial load failed: ${escapeHtml(error.message)}</div>`;
    }
});
