from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.settings import runtime_openai_api_key

from . import crm_service
from .models import CrmConversation, CrmMessage

LOW_RISK_TERMS = {
    "quote",
    "estimate",
    "price",
    "pricing",
    "cost",
    "schedule",
    "appointment",
    "available",
    "address",
    "delivery",
    "mulch",
    "soil",
    "compost",
    "service",
    "text",
}

RISK_TERMS = {
    "accident",
    "angry",
    "attorney",
    "cancel",
    "claim",
    "complaint",
    "coverage denied",
    "death",
    "emergency",
    "fraud",
    "hurt",
    "injured",
    "lawsuit",
    "legal",
    "medical",
    "payment",
    "policy",
    "refund",
    "sue",
}


def handle_inbound_message(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    channel = str(payload.get("channel") or "sms").strip().lower()
    if channel != "sms":
        raise ValueError("channel must be sms")

    from_number = str(payload.get("from") or payload.get("phone") or "").strip()
    body = str(payload.get("message") or "").strip()
    if not from_number:
        raise ValueError("from is required")
    if not body:
        raise ValueError("message is required")

    inbound = crm_service.store_inbound_message(
        db,
        phone=from_number,
        message=body,
        channel=channel,
        auto_first_message_enabled=False,
    )
    contact = inbound["contact"]
    conversation = db.query(CrmConversation).filter(
        CrmConversation.contact_id == contact.id,
        CrmConversation.channel_type == channel,
    ).order_by(CrmConversation.created_at.desc()).first()
    if not conversation:
        raise ValueError("conversation could not be created")

    summary = build_context_summary(body, conversation.status)
    store_context_summary(db, conversation, summary)

    risk = classify_message_risk(body)
    auto_reply = None
    if risk["risk"] == "low":
        reply = generate_reply(body, summary)
        auto_reply = attach_auto_reply(db, conversation, reply, risk)
        conversation.status = "auto-replied"

    return {
        "status": "received",
        "contact_id": contact.id,
        "conversation_id": conversation.id,
        "message_id": inbound["message"].id,
        "risk": risk,
        "auto_replied": bool(auto_reply),
        "auto_reply_message_id": auto_reply.id if auto_reply else None,
        "summary": summary,
    }


def classify_message_risk(message: str) -> dict[str, Any]:
    text = normalize(message)
    matched_risk = sorted(term for term in RISK_TERMS if term in text)
    if matched_risk:
        return {"risk": "review", "reason": "sensitive_or_high_stakes", "matched_terms": matched_risk}
    matched_low = sorted(term for term in LOW_RISK_TERMS if term in text)
    if matched_low:
        return {"risk": "low", "reason": "basic_service_or_scheduling", "matched_terms": matched_low}
    if len(text.split()) <= 4:
        return {"risk": "review", "reason": "too_little_context", "matched_terms": []}
    return {"risk": "review", "reason": "needs_staff_review", "matched_terms": []}


def build_context_summary(message: str, status: str) -> dict[str, str]:
    text = normalize(message)
    service = detect_service(text)
    intent = detect_intent(text)
    return {
        "intent": intent,
        "service": service,
        "status": "new lead" if status in {"open", "new"} else status,
        "next_action": next_action_for(intent, service),
    }


def store_context_summary(db: Session, conversation: CrmConversation, summary: dict[str, str]) -> None:
    crm_service.create_activity(
        db,
        contact_id=conversation.contact_id,
        related_type="conversation",
        related_id=conversation.id,
        activity_type="conversation.summary",
        title="Conversation summary updated",
        body=summary.get("next_action"),
        actor_user="ai-front-desk",
        metadata=summary,
    )


def attach_auto_reply(
    db: Session,
    conversation: CrmConversation,
    reply: str,
    risk: dict[str, Any],
) -> CrmMessage:
    message = CrmMessage(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        direction="outbound",
        channel=conversation.channel_type,
        body=reply,
        delivery_status="auto_replied",
        sent_by_user="ai-front-desk",
        created_at=datetime.utcnow() + timedelta(milliseconds=1),
    )
    db.add(message)
    db.flush()
    conversation.last_message_at = message.created_at
    conversation.unread_count = 0
    crm_service.create_activity(
        db,
        contact_id=conversation.contact_id,
        related_type="message",
        related_id=message.id,
        activity_type="message.auto_replied",
        title="AI front desk auto-replied",
        body=reply,
        actor_user="ai-front-desk",
        metadata={"risk": risk, "provider": "openai_or_template"},
    )
    return message


def generate_reply(message: str, summary: dict[str, str]) -> str:
    openai_reply = generate_openai_reply(message, summary)
    if openai_reply:
        return openai_reply
    return template_reply(summary)


def generate_openai_reply(message: str, summary: dict[str, str]) -> Optional[str]:
    api_key = runtime_openai_api_key()
    if not api_key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = os.getenv("GS_OPENAI_FRONT_DESK_MODEL", "gpt-4.1-mini")
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You write short, safe SMS replies for a business front desk. "
                        "Do not make promises, discuss policy/legal/medical/financial decisions, or ask for sensitive information. "
                        "Ask only one practical next question."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"message": message, "summary": summary}),
                },
            ],
            max_output_tokens=90,
        )
        text = str(getattr(response, "output_text", "") or "").strip()
        return clean_reply(text)
    except Exception:
        return None


def clean_reply(value: str) -> Optional[str]:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    text = re.sub(r"^['\"]|['\"]$", "", text)
    return text[:320]


def template_reply(summary: dict[str, str]) -> str:
    next_action = summary.get("next_action") or "send the best next detail"
    return f"Thanks for reaching out. I can help with that. To get this moving, please {next_action}."


def detect_service(text: str) -> str:
    if "mulch" in text:
        return "mulch delivery"
    if "soil" in text:
        return "soil delivery"
    if "compost" in text:
        return "compost delivery"
    if "insurance" in text:
        return "insurance"
    return "general service"


def detect_intent(text: str) -> str:
    if any(term in text for term in ["quote", "estimate", "price", "pricing", "cost"]):
        return "quote request"
    if any(term in text for term in ["schedule", "appointment", "available"]):
        return "scheduling"
    if "address" in text:
        return "address follow-up"
    return "general question"


def next_action_for(intent: str, service: str) -> str:
    if intent == "quote request":
        if service in {"mulch delivery", "soil delivery", "compost delivery"}:
            return "collect address"
        return "confirm what they need quoted"
    if intent == "scheduling":
        return "confirm preferred time"
    if intent == "address follow-up":
        return "review address"
    return "review and reply"


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())
