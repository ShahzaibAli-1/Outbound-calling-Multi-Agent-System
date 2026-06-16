const elements = {
    phoneNumber: document.querySelector("#phoneNumber"),
    voiceWebhookShort: document.querySelector("#voiceWebhookShort"),
    publicBaseUrl: document.querySelector("#publicBaseUrl"),
    voiceWebhook: document.querySelector("#voiceWebhook"),
    mediaStream: document.querySelector("#mediaStream"),
    providerOpenAI: document.querySelector("#providerOpenAI"),
    providerDeepgram: document.querySelector("#providerDeepgram"),
    providerTwilio: document.querySelector("#providerTwilio"),
    callForm: document.querySelector("#callForm"),
    callFeedback: document.querySelector("#callFeedback"),
    callScenario: document.querySelector("#callScenario"),
    callScenarioPreview: document.querySelector("#callScenarioPreview"),
    callPrompt: document.querySelector("#callPrompt"),
    toNumber: document.querySelector("#toNumber"),
    chatForm: document.querySelector("#chatForm"),
    chatMessage: document.querySelector("#chatMessage"),
    chatScenario: document.querySelector("#chatScenario"),
    chatScenarioPreview: document.querySelector("#chatScenarioPreview"),
    chatPrompt: document.querySelector("#chatPrompt"),
    chatAnswer: document.querySelector("#chatAnswer"),
    chatFeedback: document.querySelector("#chatFeedback"),
    callsList: document.querySelector("#callsList"),
    refreshButton: document.querySelector("#refreshButton"),
};

const state = {
    refreshTimer: null,
    scenarios: [],
};

function getScenarioById(scenarioId) {
    return state.scenarios.find((scenario) => scenario.id === scenarioId) || null;
}

function populateScenarioSelect(selectNode, defaultLabel) {
    selectNode.innerHTML = [
        `<option value="">${escapeHtml(defaultLabel)}</option>`,
        ...state.scenarios.map(
            (scenario) => `<option value="${escapeHtml(scenario.id)}">${escapeHtml(scenario.label)}</option>`
        ),
    ].join("");
}

function renderScenarioPreview(targetNode, scenarioId, customPrompt) {
    if (customPrompt) {
        targetNode.textContent = "A custom prompt is entered, so it will override the selected scenario for this request.";
        return;
    }

    if (!scenarioId) {
        targetNode.textContent = "Using the default medical intake prompt from test_system_prompt.txt.";
        return;
    }

    const scenario = getScenarioById(scenarioId);
    if (!scenario) {
        targetNode.textContent = "Scenario details are not available right now.";
        return;
    }

    targetNode.textContent = `${scenario.description}\n\nPrompt: ${scenario.prompt}`;
}

function setProviderStatus(node, ready) {
    node.textContent = ready ? "Ready" : "Missing";
    node.className = ready ? "status-ready" : "status-missing";
}

function truncateMiddle(value, maxLength = 44) {
    if (!value || value.length <= maxLength) {
        return value || "Not available";
    }

    const sliceLength = Math.floor((maxLength - 3) / 2);
    return `${value.slice(0, sliceLength)}...${value.slice(-sliceLength)}`;
}

function escapeHtml(value) {
    return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function formatTimestamp(value) {
    if (!value) {
        return "";
    }

    return new Intl.DateTimeFormat([], {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(new Date(value));
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
        ...options,
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `Request failed with ${response.status}`);
    }

    return response.json();
}

async function loadHealth() {
    const health = await fetchJson("/api/health");

    elements.phoneNumber.textContent = health.phone_number || "Not configured";
    elements.voiceWebhookShort.textContent = truncateMiddle(health.voice_webhook);
    elements.publicBaseUrl.textContent = health.public_base_url;
    elements.voiceWebhook.textContent = health.voice_webhook;
    elements.mediaStream.textContent = health.media_stream;

    setProviderStatus(elements.providerOpenAI, health.providers.openai);
    setProviderStatus(elements.providerDeepgram, health.providers.deepgram);
    setProviderStatus(elements.providerTwilio, health.providers.twilio);
}

async function loadCampaignScenarios() {
    const data = await fetchJson("/api/campaign-scenarios");
    state.scenarios = data.scenarios || [];

    populateScenarioSelect(elements.callScenario, "Default medical intake prompt");
    populateScenarioSelect(elements.chatScenario, "Default medical intake prompt");

    renderScenarioPreview(
        elements.callScenarioPreview,
        elements.callScenario.value,
        elements.callPrompt.value.trim()
    );
    renderScenarioPreview(
        elements.chatScenarioPreview,
        elements.chatScenario.value,
        elements.chatPrompt.value.trim()
    );
}

function renderCalls(calls) {
    if (!calls.length) {
        elements.callsList.innerHTML = '<div class="empty-state">No calls have been placed yet.</div>';
        return;
    }

    elements.callsList.innerHTML = calls
        .slice(0, 8)
        .map((call) => {
            const direction = escapeHtml(call.direction || "unknown");
            const status = escapeHtml(call.status || "unknown");
            const fromNumber = escapeHtml(call.from_number || "Unknown");
            const toNumber = escapeHtml(call.to_number || "Unknown");
            const events = (call.events || [])
                .slice(-8)
                .reverse()
                .map(
                    (event) => `
                        <div class="event-row">
                            <div class="event-type">${escapeHtml(event.type)}</div>
                            <div class="event-text">${escapeHtml(event.text)}</div>
                            <div class="event-time">${formatTimestamp(event.timestamp)}</div>
                        </div>
                    `
                )
                .join("");

            const intake = call.patient_intake;
            const intakeSummary = intake
                ? `
                    <div class="intake-summary">
                        <div class="response-label">Patient intake (${escapeHtml(intake.intake_status || "unknown")})</div>
                        <div class="intake-grid">
                            ${intake.full_name ? `<div><strong>Name:</strong> ${escapeHtml(intake.full_name)}</div>` : ""}
                            ${intake.date_of_birth ? `<div><strong>DOB:</strong> ${escapeHtml(intake.date_of_birth)}</div>` : ""}
                            ${intake.phone_number ? `<div><strong>Phone:</strong> ${escapeHtml(intake.phone_number)}</div>` : ""}
                            ${intake.reason_for_visit ? `<div><strong>Reason:</strong> ${escapeHtml(intake.reason_for_visit)}</div>` : ""}
                            ${intake.allergies ? `<div><strong>Allergies:</strong> ${escapeHtml(intake.allergies)}</div>` : ""}
                            ${intake.current_medications ? `<div><strong>Medications:</strong> ${escapeHtml(intake.current_medications)}</div>` : ""}
                        </div>
                    </div>
                `
                : "";

            return `
                <article class="call-card">
                    <div class="call-head">
                        <div>
                            <strong>${fromNumber} -> ${toNumber}</strong>
                            <div class="call-meta">${direction} | SID ${escapeHtml(call.sid)}</div>
                        </div>
                        <div class="call-badge">${status}</div>
                    </div>
                    ${intakeSummary}
                    <div class="events">${events || '<div class="empty-state">No transcript events yet.</div>'}</div>
                </article>
            `;
        })
        .join("");
}

async function loadCalls() {
    const data = await fetchJson("/api/calls");
    renderCalls(data.calls || []);
}

async function refreshAll() {
    await Promise.all([loadHealth(), loadCalls()]);
}

async function handleCallSubmit(event) {
    event.preventDefault();
    elements.callFeedback.textContent = "Queueing outbound call...";

    const customPrompt = elements.callPrompt.value.trim();
    const payload = {
        to_number: elements.toNumber.value.trim(),
        system_prompt: customPrompt || null,
        scenario_id: elements.callScenario.value || null,
    };

    try {
        const data = await fetchJson("/api/calls/outbound", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        elements.callFeedback.textContent = `Call ${data.call_sid} queued with status ${data.status} using ${data.prompt_source}.`;
        elements.callForm.reset();
        renderScenarioPreview(elements.callScenarioPreview, "", "");
        await loadCalls();
    } catch (error) {
        elements.callFeedback.textContent = `Call failed: ${error.message}`;
    }
}

async function handleChatSubmit(event) {
    event.preventDefault();
    elements.chatFeedback.textContent = "Generating response...";
    elements.chatAnswer.textContent = "Working...";

    const customPrompt = elements.chatPrompt.value.trim();
    try {
        const data = await fetchJson("/api/chat/test", {
            method: "POST",
            body: JSON.stringify({
                message: elements.chatMessage.value.trim(),
                system_prompt: customPrompt || null,
                scenario_id: elements.chatScenario.value || null,
            }),
        });

        elements.chatAnswer.textContent = data.answer;
        elements.chatFeedback.textContent = "Browser test completed.";
    } catch (error) {
        elements.chatAnswer.textContent = "Unable to generate a response.";
        elements.chatFeedback.textContent = `Browser test failed: ${error.message}`;
    }
}

function startAutoRefresh() {
    state.refreshTimer = window.setInterval(() => {
        loadCalls().catch((error) => {
            elements.callFeedback.textContent = `Auto refresh issue: ${error.message}`;
        });
    }, 5000);
}

async function bootstrap() {
    elements.callForm.addEventListener("submit", handleCallSubmit);
    elements.chatForm.addEventListener("submit", handleChatSubmit);
    elements.callScenario.addEventListener("change", () => {
        renderScenarioPreview(
            elements.callScenarioPreview,
            elements.callScenario.value,
            elements.callPrompt.value.trim()
        );
    });
    elements.chatScenario.addEventListener("change", () => {
        renderScenarioPreview(
            elements.chatScenarioPreview,
            elements.chatScenario.value,
            elements.chatPrompt.value.trim()
        );
    });
    elements.callPrompt.addEventListener("input", () => {
        renderScenarioPreview(
            elements.callScenarioPreview,
            elements.callScenario.value,
            elements.callPrompt.value.trim()
        );
    });
    elements.chatPrompt.addEventListener("input", () => {
        renderScenarioPreview(
            elements.chatScenarioPreview,
            elements.chatScenario.value,
            elements.chatPrompt.value.trim()
        );
    });
    elements.refreshButton.addEventListener("click", () => {
        refreshAll().catch((error) => {
            elements.callFeedback.textContent = `Refresh failed: ${error.message}`;
        });
    });

    try {
        await loadCampaignScenarios();
        await refreshAll();
        startAutoRefresh();
    } catch (error) {
        elements.callFeedback.textContent = `Initial load failed: ${error.message}`;
    }
}

bootstrap();
