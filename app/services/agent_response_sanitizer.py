from __future__ import annotations

import re

# Internal monologue markers the model must never speak (also used to clean leaked output).
_REASONING_LEAK_MARKERS = (
    "The user ",
    "The caller ",
    "The next step",
    "According to the",
    "according to the",
    "Appointment booking",
    "I have summarized",
    "Now I need to",
    "I need to ask",
    "the user confirmed",
    "the user stated",
    "the user provided",
    "for a new patient,",
    "for a returning patient,",
    "as per the",
    "based on the",
    "following the",
    "internal note",
    "scratchpad",
)

_LEAK_PATTERN = re.compile(
    r"(?:The user |The caller |The next step|According to |according to the |"
    r"I have summarized|Now I need to|I need to ask|for a new patient,|"
    r'according to the "[^"]+" section).*$',
    re.IGNORECASE | re.DOTALL,
)

_BRACKET_TAG_PATTERN = re.compile(r"\[[^\]]+\]|\*[^*]+\*")


def sanitize_agent_spoken_text(text: str) -> str:
    """Keep only patient-facing speech; strip leaked reasoning or stage directions."""
    cleaned = text.strip()
    if not cleaned:
        return ""

    cleaned = _BRACKET_TAG_PATTERN.sub("", cleaned).strip()

    for marker in _REASONING_LEAK_MARKERS:
        index = cleaned.find(marker)
        if index > 0:
            cleaned = cleaned[:index].strip()
            break

    cleaned = _LEAK_PATTERN.sub("", cleaned).strip()

    if ". " in cleaned:
        parts = cleaned.split(". ")
        safe_parts: list[str] = []
        for part in parts:
            fragment = part.strip()
            if not fragment:
                continue
            lower = fragment.lower()
            if any(lower.startswith(marker.strip().lower()) for marker in _REASONING_LEAK_MARKERS):
                break
            safe_parts.append(fragment)
        if safe_parts:
            cleaned = ". ".join(safe_parts)
            if cleaned and not cleaned.endswith((".", "?", "!")):
                cleaned += "."

    return cleaned.strip()


SPOKEN_OUTPUT_APPENDIX = """

## ABSOLUTE RULE — PATIENT-HEARING OUTPUT ONLY
Your response is converted directly to voice. The patient hears EVERY word you output.

FORBIDDEN — never output any of the following:
- Planning or reasoning ("The user stated...", "The next step is...", "According to the Appointment booking section...")
- References to these instructions, sections, steps, scenarios, or prompts
- Describing what you will do next instead of doing it
- Third-person narration about the caller ("The user provided their phone number")
- Bracket tags like [happy], stage directions, or bullet lists

CORRECT example:
"Thank you. What is the reason for your visit?"

WRONG example (never do this):
"Thank you. What is the reason for your visit? The user provided their phone number. The next step is to ask about allergies."

If you need to decide what to ask next, decide silently. Speak only the short question or confirmation the patient should hear.
"""
