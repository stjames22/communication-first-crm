from __future__ import annotations


def generate_first_message(context: str | None = None) -> str:
    base_ack = "Got it — I’ve received your message."
    base_reassure = "I’ll keep this quick and focused so you’re not wasting time."

    if context == "support":
        direction = "Tell me what issue you're running into."
    elif context == "sales":
        direction = "What are you looking to get set up or handled?"
    elif context == "followup":
        direction = "What do you need to move this forward?"
    else:
        direction = "Tell me what you need help with right now."

    return f"{base_ack}\n\n{base_reassure}\n\n{direction}"
