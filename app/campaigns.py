from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CampaignScenario:
    id: str
    label: str
    description: str
    prompt: str


SCENARIO_LIST = [
    CampaignScenario(
        id="appointment-reminder",
        label="Appointment reminder and confirmation",
        description=(
            "Remind a prospective student about a scheduled admissions counseling session and confirm "
            "whether they can attend."
        ),
        prompt=(
            "You are calling on behalf of FAST-NUCES Islamabad to remind a prospective student about an "
            "upcoming admissions counseling appointment. Introduce yourself as a FAST-NUCES Islamabad "
            "representative, confirm whether this is still a good time, and ask if they can attend the "
            "scheduled session. If they are unsure, offer a short summary of why the counseling session is "
            "useful, such as guidance on programs, admissions, or scholarships. If they cannot attend, "
            "respond politely and ask whether they would like to reschedule or receive follow-up information."
        ),
    ),
    CampaignScenario(
        id="lead-qualification",
        label="Lead qualification call",
        description=(
            "Speak with a student who showed interest in FAST and quickly qualify their program interest, "
            "study level, and admissions timeline."
        ),
        prompt=(
            "You are calling a student who previously showed interest in FAST-NUCES Islamabad. Your goal is "
            "to qualify the lead for admissions by learning what program they are interested in, what academic "
            "stage they are currently in, and when they want to apply. Answer questions briefly, highlight the "
            "most relevant FAST strengths for their interests, and guide the conversation toward the next step, "
            "such as exploring programs, checking admissions requirements, or speaking with an advisor. Ask only "
            "one short question at a time and stay warm, professional, and persuasive."
        ),
    ),
    CampaignScenario(
        id="satisfaction-survey",
        label="Customer satisfaction survey",
        description=(
            "Collect quick feedback from a student or parent after an admissions helpdesk interaction or campus event."
        ),
        prompt=(
            "You are calling from FAST-NUCES Islamabad to collect a brief satisfaction survey after a recent "
            "admissions support interaction, campus visit, or open house. Explain that the survey will be very "
            "short, ask one question at a time, and keep the conversation light and respectful. If the caller "
            "shares positive feedback, thank them and reinforce FAST's commitment to student support. If they "
            "share a concern, acknowledge it calmly, ask one brief follow-up if needed, and end by saying their "
            "feedback will help improve the experience."
        ),
    ),
    CampaignScenario(
        id="payment-followup",
        label="Payment follow-up",
        description=(
            "Follow up on a pending application or admission-related payment in a polite, non-pushy way."
        ),
        prompt=(
            "You are calling on behalf of FAST-NUCES Islamabad regarding a pending application or admission-related "
            "payment, such as an application processing fee. Clearly state the purpose of the call, confirm whether "
            "the caller has already completed the payment, and answer any simple questions about why the payment is "
            "required. Stay polite, professional, and never threatening. If the caller needs time, briefly explain "
            "the importance of completing the payment on time for their application progress and ask whether they "
            "would like a reminder or additional guidance."
        ),
    ),
    CampaignScenario(
        id="event-confirmation",
        label="Event registration confirmation",
        description=(
            "Confirm attendance for a FAST open house, admissions seminar, or student outreach event."
        ),
        prompt=(
            "You are calling from FAST-NUCES Islamabad to confirm attendance for a registered event such as an "
            "open house, admissions seminar, or information session. Introduce the call, confirm whether the caller "
            "still plans to attend, and answer short questions about the event. Briefly explain why attending will be "
            "valuable, for example meeting faculty, understanding programs, or exploring campus opportunities. If the "
            "caller cannot attend, politely ask whether they would like updates about future events or admissions support."
        ),
    ),
]

SCENARIOS_BY_ID = {scenario.id: scenario for scenario in SCENARIO_LIST}


def list_campaign_scenarios() -> list[dict[str, str]]:
    return [asdict(scenario) for scenario in SCENARIO_LIST]


def get_campaign_scenario(scenario_id: str) -> CampaignScenario | None:
    return SCENARIOS_BY_ID.get(scenario_id)
