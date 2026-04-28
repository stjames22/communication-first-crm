from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session, selectinload

from .first_message import generate_first_message
from .models import (
    CrmActivity,
    CrmCall,
    CrmContact,
    CrmConversation,
    CrmExternalLink,
    CrmMessage,
    CrmQuote,
    CrmQuoteLineItem,
    CrmQuoteVersion,
    CrmServiceSite,
    CrmTask,
)


def money(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def row_id(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def normalize_email(email: Optional[str]) -> Optional[str]:
    clean_email = str(email or "").strip().lower()
    return clean_email or None


def normalize_name(name: Optional[str]) -> Optional[str]:
    clean_name = re.sub(r"\s+", " ", str(name or "").strip())
    return clean_name or None


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    raw_phone = str(phone or "").strip()
    if not raw_phone:
        return None
    digits = re.sub(r"\D+", "", raw_phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw_phone.startswith("+") and digits:
        return f"+{digits}"
    if digits:
        return digits
    return None


def _name_similarity(left: Optional[str], right: Optional[str]) -> float:
    clean_left = normalize_name(left)
    clean_right = normalize_name(right)
    if not clean_left or not clean_right:
        return 0.0
    return SequenceMatcher(None, clean_left.lower(), clean_right.lower()).ratio()


def _find_duplicate_candidates(
    db: Session,
    *,
    contact: Optional[CrmContact],
    normalized_phone: Optional[str],
    normalized_email: Optional[str],
    clean_name: Optional[str],
) -> list[CrmContact]:
    candidates: list[CrmContact] = []
    seen: set[str] = set()

    if clean_name:
        possible_names = db.query(CrmContact).limit(300).all()
        for row in possible_names:
            if contact and row.id == contact.id:
                continue
            if row.id in seen:
                continue
            if _name_similarity(clean_name, row.display_name) >= 0.86:
                candidates.append(row)
                seen.add(row.id)

    if normalized_email:
        local_part = normalized_email.split("@", 1)[0]
        if local_part:
            for row in db.query(CrmContact).filter(CrmContact.email.isnot(None)).limit(300).all():
                if contact and row.id == contact.id:
                    continue
                if row.id in seen:
                    continue
                row_local = str(row.email or "").lower().split("@", 1)[0]
                if row_local and SequenceMatcher(None, local_part, row_local).ratio() >= 0.9:
                    candidates.append(row)
                    seen.add(row.id)

    if normalized_phone:
        last_seven = re.sub(r"\D+", "", normalized_phone)[-7:]
        if last_seven:
            for row in db.query(CrmContact).limit(300).all():
                if contact and row.id == contact.id:
                    continue
                if row.id in seen:
                    continue
                row_digits = re.sub(r"\D+", "", str(row.mobile_phone or ""))
                if row_digits.endswith(last_seven):
                    candidates.append(row)
                    seen.add(row.id)

    return candidates[:5]


def resolve_contact_details(
    db: Session,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    normalized_email = normalize_email(email)
    clean_name = normalize_name(name)
    if not normalized_phone and not normalized_email and not clean_name:
        raise ValueError("phone, email, or name is required")

    contact = None
    match_type = "created"
    if normalized_phone:
        contact = db.query(CrmContact).filter(CrmContact.mobile_phone == normalized_phone).first()
        if contact:
            match_type = "phone"
    if contact is None and normalized_email:
        contact = db.query(CrmContact).filter(func.lower(CrmContact.email) == normalized_email).first()
        if contact:
            match_type = "email"
    if contact is None and clean_name:
        exact_name = db.query(CrmContact).filter(func.lower(CrmContact.display_name) == clean_name.lower()).first()
        if exact_name:
            contact = exact_name
            match_type = "name"
        else:
            for row in db.query(CrmContact).limit(300).all():
                if _name_similarity(clean_name, row.display_name) >= 0.92:
                    contact = row
                    match_type = "fuzzy_name"
                    break

    if contact:
        if clean_name and (contact.display_name == contact.mobile_phone or contact.display_name.startswith(("email:", "name:"))):
            contact.display_name = clean_name
        if normalized_email and not contact.email:
            contact.email = normalized_email
        if normalized_phone and contact.mobile_phone != normalized_phone:
            phone_owner = db.query(CrmContact).filter(CrmContact.mobile_phone == normalized_phone).first()
            if not phone_owner or phone_owner.id == contact.id:
                contact.mobile_phone = normalized_phone
    else:
        display_name = clean_name or normalized_email or normalized_phone
        if normalized_phone:
            phone_value = normalized_phone
        elif normalized_email:
            phone_value = f"email:{normalized_email}"
        else:
            phone_value = f"name:{uuid.uuid4()}"
        contact = CrmContact(
            display_name=display_name,
            mobile_phone=phone_value,
            email=normalized_email,
            status="lead",
            source="inbound",
        )
        db.add(contact)
        db.flush()

    duplicate_candidates = _find_duplicate_candidates(
        db,
        contact=contact,
        normalized_phone=normalized_phone,
        normalized_email=normalized_email,
        clean_name=clean_name,
    )
    warnings = []
    if duplicate_candidates:
        warnings.append("possible_duplicate")
    priority = priority_for_match(match_type)

    return {
        "contact": contact,
        "match_type": match_type,
        "matched_existing_contact": match_type != "created",
        "priority": priority["priority"],
        "priority_score": priority["priority_score"],
        "normalized": {
            "phone": normalized_phone,
            "email": normalized_email,
            "name": clean_name,
        },
        "duplicate_warning": bool(duplicate_candidates),
        "warnings": warnings,
        "duplicate_candidates": [serialize_contact(candidate) for candidate in duplicate_candidates],
    }


def priority_for_match(match_type: Optional[str]) -> dict[str, Any]:
    if match_type in {"phone", "provider_message_id", "provider_call_id"}:
        return {"priority": "existing_contact", "priority_score": 90}
    if match_type in {"email", "name", "fuzzy_name"}:
        return {"priority": "matched_contact", "priority_score": 75}
    return {"priority": "new_contact", "priority_score": 50}


def resolve_contact(
    db: Session,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> CrmContact:
    return resolve_contact_details(db, phone=phone, name=name, email=email)["contact"]


def get_or_create_conversation(db: Session, contact_id: str, channel: str = "sms") -> CrmConversation:
    channel_value = str(channel or "sms").strip().lower() or "sms"
    conversation = (
        db.query(CrmConversation)
        .filter(CrmConversation.contact_id == contact_id, CrmConversation.channel_type == channel_value[:32])
        .order_by(desc(CrmConversation.created_at))
        .first()
    )
    if conversation:
        return conversation

    conversation = CrmConversation(contact_id=contact_id, channel_type=channel_value[:32], status="open")
    db.add(conversation)
    db.flush()
    return conversation


def serialize_core_message(message: CrmMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "contact_id": message.contact_id,
        "message": message.body,
        "direction": message.direction,
        "timestamp": message.created_at.isoformat() if message.created_at else None,
    }


def count_messages(db: Session, contact_id: str) -> int:
    return db.query(CrmMessage).filter(CrmMessage.contact_id == contact_id).count()


def is_first_interaction(db: Session, contact_id: str) -> bool:
    return count_messages(db, contact_id) == 1


def has_auto_first_message(db: Session, contact_id: str) -> bool:
    return (
        db.query(CrmActivity)
        .filter(
            CrmActivity.contact_id == contact_id,
            CrmActivity.activity_type == "message.outbound",
            CrmActivity.metadata_json.like('%"auto_first_message": true%'),
        )
        .first()
        is not None
    )


def latest_inbound_message(db: Session, contact_id: str) -> Optional[CrmMessage]:
    return (
        db.query(CrmMessage)
        .filter(CrmMessage.contact_id == contact_id, CrmMessage.direction == "inbound")
        .order_by(desc(CrmMessage.created_at), desc(CrmMessage.id))
        .first()
    )


def build_account_summary(
    db: Session,
    contact_id: str,
    match_type: Optional[str] = None,
    priority: Optional[str] = None,
    priority_score: Optional[int] = None,
) -> dict[str, Any]:
    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return {}
    resolved_match_type = match_type or "created"
    priority_info = priority_for_match(resolved_match_type)
    resolved_priority = priority or priority_info["priority"]
    resolved_score = priority_score if priority_score is not None else priority_info["priority_score"]
    latest_activity = (
        db.query(CrmActivity)
        .filter(CrmActivity.contact_id == contact_id)
        .order_by(desc(CrmActivity.created_at), desc(CrmActivity.id))
        .first()
    )
    inbound = latest_inbound_message(db, contact_id)
    open_followups = (
        db.query(CrmTask)
        .filter(CrmTask.contact_id == contact_id, CrmTask.status != "completed")
        .order_by(CrmTask.due_at, desc(CrmTask.created_at))
        .limit(3)
        .all()
    )
    quote_activity = (
        db.query(CrmActivity)
        .filter(CrmActivity.contact_id == contact_id, CrmActivity.activity_type.like("quote.%"))
        .order_by(desc(CrmActivity.created_at))
        .limit(3)
        .all()
    )
    notes = (
        db.query(CrmActivity)
        .filter(CrmActivity.contact_id == contact_id, CrmActivity.activity_type == "note.added")
        .order_by(desc(CrmActivity.created_at))
        .limit(3)
        .all()
    )
    assistant = assistant_suggestions(db, contact_id) or {}
    flags = assistant.get("flags") or []
    latest_message = inbound.body if inbound else ""
    followup_title = open_followups[0].title if open_followups else None
    last_contact = latest_activity.created_at.strftime("%b %-d") if latest_activity and latest_activity.created_at else "none"
    parts = [
        "Existing customer." if resolved_priority in {"existing_contact", "matched_contact"} else "New contact.",
        f"Last contact: {last_contact}.",
    ]
    if followup_title:
        parts.append(f"Open follow-up: {followup_title}.")
    if latest_message:
        parts.append(f"Latest message: {latest_message[:120]}.")
    if quote_activity:
        parts.append(f"Proposal activity: {quote_activity[0].title}.")
    return {
        "contact_id": contact.id,
        "contact_name": contact.display_name,
        "phone": contact.mobile_phone,
        "email": contact.email,
        "match_type": resolved_match_type,
        "priority": resolved_priority,
        "priority_score": resolved_score,
        "last_interaction_date": latest_activity.created_at.isoformat() if latest_activity and latest_activity.created_at else None,
        "latest_inbound_message": latest_message or None,
        "open_followups": [serialize_task(task) for task in open_followups],
        "proposal_activity": [serialize_activity(activity) for activity in quote_activity],
        "unresolved_notes": [serialize_activity(note) for note in notes],
        "urgency_flags": flags,
        "summary": " ".join(parts),
        "recommended_next_action": "Review timeline and draft a response.",
    }


def first_message_context(message: str, channel: Optional[str] = None) -> Optional[str]:
    text = f"{message or ''} {channel or ''}".lower()
    if any(term in text for term in ["issue", "problem", "broken", "not working", "support", "help"]):
        return "support"
    if any(term in text for term in ["quote", "proposal", "price", "setup", "buy", "sales"]):
        return "sales"
    if any(term in text for term in ["follow up", "following up", "checking in", "move forward"]):
        return "followup"
    return None


def store_auto_first_message(
    db: Session,
    *,
    contact_id: str,
    conversation_id: str,
    channel: str,
    context: Optional[str] = None,
) -> Optional[CrmMessage]:
    if has_auto_first_message(db, contact_id):
        return None

    body = generate_first_message(context)
    created_at = datetime.utcnow()
    crm_message = CrmMessage(
        conversation_id=conversation_id,
        contact_id=contact_id,
        direction="outbound",
        channel=channel[:32],
        body=body,
        delivery_status="system_generated",
        sent_by_user="system",
        created_at=created_at,
    )
    db.add(crm_message)
    db.flush()
    create_activity(
        db,
        contact_id=contact_id,
        related_type="message",
        related_id=crm_message.id,
        activity_type="message.outbound",
        title="Auto first message",
        body=body,
        actor_user="system",
        metadata={
            "channel": channel,
            "context": context,
            "system_generated": True,
            "auto_first_message": True,
        },
    )
    return crm_message


def store_inbound_message(
    db: Session,
    phone: Optional[str],
    message: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    channel: Optional[str] = None,
) -> dict[str, Any]:
    body = str(message or "").strip()
    if not body:
        raise ValueError("message is required")

    resolution = resolve_contact_details(db, phone, name=name, email=email)
    contact = resolution["contact"]
    created_at = datetime.utcnow()
    channel_value = str(channel or "sms").strip().lower() or "sms"
    conversation = get_or_create_conversation(db, contact.id, channel=channel_value)
    if resolution["priority"] == "existing_contact":
        conversation.status = "priority"
    crm_message = CrmMessage(
        conversation_id=conversation.id,
        contact_id=contact.id,
        direction="inbound",
        channel=channel_value[:32],
        body=body,
        delivery_status="received",
        created_at=created_at,
    )
    db.add(crm_message)
    db.flush()
    conversation.last_message_at = crm_message.created_at or created_at
    conversation.unread_count = (conversation.unread_count or 0) + 1
    create_activity(
        db,
        contact_id=contact.id,
        related_type="message",
        related_id=crm_message.id,
        activity_type="message.inbound",
        title="Inbound message received",
        body=body,
        metadata={
            "channel": channel_value,
            "match_type": resolution["match_type"],
            "duplicate_warning": resolution["duplicate_warning"],
            "matched_existing_contact": resolution["matched_existing_contact"],
            "priority": resolution["priority"],
            "priority_score": resolution["priority_score"],
        },
    )
    auto_first_message = None
    if is_first_interaction(db, contact.id):
        auto_first_message = store_auto_first_message(
            db,
            contact_id=contact.id,
            conversation_id=conversation.id,
            channel=channel_value,
            context=first_message_context(body, channel_value),
        )
        if auto_first_message:
            conversation.last_message_at = auto_first_message.created_at or datetime.utcnow()
    account_summary = build_account_summary(
        db,
        contact.id,
        match_type=resolution["match_type"],
        priority=resolution["priority"],
        priority_score=resolution["priority_score"],
    )
    return {
        "contact": contact,
        "message": crm_message,
        "resolution": resolution,
        "auto_first_message": auto_first_message,
        "account_summary": account_summary,
    }


def list_contact_messages(db: Session, contact_id: str) -> Optional[list[dict[str, Any]]]:
    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None
    messages = (
        db.query(CrmMessage)
        .filter(CrmMessage.contact_id == contact_id)
        .order_by(CrmMessage.created_at, CrmMessage.id)
        .all()
    )
    return [serialize_core_message(message) for message in messages]


def store_outbound_reply(db: Session, contact_id: str, message: str) -> Optional[CrmMessage]:
    body = str(message or "").strip()
    if not body:
        raise ValueError("message is required")

    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None

    conversation = get_or_create_conversation(db, contact.id)
    created_at = datetime.utcnow()
    crm_message = CrmMessage(
        conversation_id=conversation.id,
        contact_id=contact.id,
        direction="outbound",
        channel="sms",
        body=body,
        delivery_status="mock_sent",
        created_at=created_at,
    )
    db.add(crm_message)
    db.flush()
    conversation.last_message_at = crm_message.created_at or created_at
    conversation.unread_count = 0
    create_activity(
        db,
        contact_id=contact.id,
        related_type="message",
        related_id=crm_message.id,
        activity_type="message.outbound",
        title="Outbound reply logged",
        body=body,
        metadata={"provider": "mock"},
    )
    return crm_message


def normalize_provider_message(payload: dict[str, Any], provider: str = "generic") -> dict[str, Any]:
    provider_name = str(provider or payload.get("provider") or "generic").strip().lower() or "generic"
    return {
        "provider": provider_name,
        "provider_message_id": row_id(
            payload.get("provider_message_id")
            or payload.get("message_id")
            or payload.get("MessageSid")
            or payload.get("id")
        ),
        "phone": payload.get("phone") or payload.get("from") or payload.get("From") or payload.get("caller"),
        "to": payload.get("to") or payload.get("To"),
        "name": payload.get("name") or payload.get("customer_name") or payload.get("ProfileName"),
        "email": payload.get("email"),
        "message": payload.get("message") or payload.get("body") or payload.get("Body") or payload.get("text"),
        "channel": payload.get("channel") or "sms",
    }


def store_provider_inbound_message(db: Session, payload: dict[str, Any], provider: str = "generic") -> dict[str, Any]:
    normalized = normalize_provider_message(payload, provider=provider)
    if normalized["provider_message_id"]:
        existing = (
            db.query(CrmMessage)
            .filter(CrmMessage.provider_message_id == normalized["provider_message_id"])
            .first()
        )
        if existing:
            contact = db.query(CrmContact).filter(CrmContact.id == existing.contact_id).first()
            priority = priority_for_match("provider_message_id")
            return {
                "contact": contact,
                "message": existing,
                "provider": normalized["provider"],
                "resolution": {
                    "match_type": "provider_message_id",
                    "matched_existing_contact": True,
                    "priority": priority["priority"],
                    "priority_score": priority["priority_score"],
                    "duplicate_warning": False,
                    "warnings": [],
                },
                "account_summary": build_account_summary(
                    db,
                    existing.contact_id,
                    match_type="provider_message_id",
                    priority=priority["priority"],
                    priority_score=priority["priority_score"],
                ),
            }
    result = store_inbound_message(
        db,
        phone=normalized["phone"],
        name=normalized["name"],
        email=normalized["email"],
        message=str(normalized["message"] or ""),
        channel=normalized["channel"],
    )
    message = result["message"]
    if normalized["provider_message_id"]:
        message.provider_message_id = normalized["provider_message_id"]
    create_activity(
        db,
        contact_id=result["contact"].id,
        related_type="provider_event",
        related_id=message.id,
        activity_type="provider.message_received",
        title="Provider message received",
        body=str(normalized["message"] or "").strip(),
        metadata={
            "provider": normalized["provider"],
            "provider_message_id": normalized["provider_message_id"],
            "channel": normalized["channel"],
        },
    )
    return {**result, "provider": normalized["provider"]}


def normalize_call_event(payload: dict[str, Any], provider: str = "generic") -> dict[str, Any]:
    provider_name = str(provider or payload.get("provider") or "generic").strip().lower() or "generic"
    direction = str(payload.get("direction") or payload.get("Direction") or "inbound").strip().lower()
    status_value = str(payload.get("status") or payload.get("CallStatus") or payload.get("event") or "logged").strip().lower()
    return {
        "provider": provider_name,
        "provider_call_id": row_id(payload.get("provider_call_id") or payload.get("CallSid") or payload.get("id")),
        "phone": payload.get("phone") or payload.get("from") or payload.get("From") or payload.get("caller"),
        "to": payload.get("to") or payload.get("To") or payload.get("business_phone") or "",
        "name": payload.get("name") or payload.get("customer_name"),
        "email": payload.get("email"),
        "direction": direction if direction in {"inbound", "outbound"} else "inbound",
        "status": status_value[:32] or "logged",
        "duration_seconds": payload.get("duration_seconds") or payload.get("Duration"),
        "notes": payload.get("notes") or payload.get("summary"),
    }


def store_call_event(db: Session, payload: dict[str, Any], provider: str = "generic") -> dict[str, Any]:
    normalized = normalize_call_event(payload, provider=provider)
    if normalized["provider_call_id"]:
        existing = (
            db.query(CrmCall)
            .filter(CrmCall.provider_call_id == normalized["provider_call_id"])
            .first()
        )
        if existing:
            contact = db.query(CrmContact).filter(CrmContact.id == existing.contact_id).first()
            priority = priority_for_match("provider_call_id")
            activity = (
                db.query(CrmActivity)
                .filter(CrmActivity.related_type == "call", CrmActivity.related_id == existing.id)
                .order_by(desc(CrmActivity.created_at))
                .first()
            )
            return {
                "contact": contact,
                "call": existing,
                "activity": activity,
                "resolution": {
                    "match_type": "provider_call_id",
                    "matched_existing_contact": True,
                    "priority": priority["priority"],
                    "priority_score": priority["priority_score"],
                    "duplicate_warning": False,
                    "warnings": [],
                },
                "account_summary": build_account_summary(
                    db,
                    existing.contact_id,
                    match_type="provider_call_id",
                    priority=priority["priority"],
                    priority_score=priority["priority_score"],
                ),
            }
    resolution = resolve_contact_details(
        db,
        phone=normalized["phone"],
        name=normalized["name"],
        email=normalized["email"],
    )
    contact = resolution["contact"]
    conversation = get_or_create_conversation(db, contact.id, channel="phone")
    if resolution["priority"] == "existing_contact":
        conversation.status = "priority"
    duration = None
    if normalized["duration_seconds"] not in (None, ""):
        try:
            duration = int(normalized["duration_seconds"])
        except (TypeError, ValueError):
            duration = None
    call = CrmCall(
        contact_id=contact.id,
        conversation_id=conversation.id,
        provider_call_id=normalized["provider_call_id"],
        direction=normalized["direction"],
        status=normalized["status"],
        from_number=normalize_phone(normalized["phone"]) or str(normalized["phone"] or ""),
        to_number=normalize_phone(normalized["to"]) or str(normalized["to"] or ""),
        duration_seconds=duration,
        notes=normalized["notes"],
        started_at=datetime.utcnow(),
    )
    db.add(call)
    db.flush()
    conversation.last_message_at = call.started_at
    if normalized["direction"] == "inbound" and normalized["status"] in {"missed", "no-answer", "ringing"}:
        conversation.unread_count = (conversation.unread_count or 0) + 1
    activity = create_activity(
        db,
        contact_id=contact.id,
        related_type="call",
        related_id=call.id,
        activity_type=f"call.{normalized['status']}",
        title="Call event logged",
        body=normalized["notes"],
        metadata={
            "provider": normalized["provider"],
            "provider_call_id": normalized["provider_call_id"],
            "direction": normalized["direction"],
            "match_type": resolution["match_type"],
            "duplicate_warning": resolution["duplicate_warning"],
            "matched_existing_contact": resolution["matched_existing_contact"],
            "priority": resolution["priority"],
            "priority_score": resolution["priority_score"],
        },
    )
    account_summary = build_account_summary(
        db,
        contact.id,
        match_type=resolution["match_type"],
        priority=resolution["priority"],
        priority_score=resolution["priority_score"],
    )
    return {"contact": contact, "call": call, "activity": activity, "resolution": resolution, "account_summary": account_summary}


def add_contact_note(db: Session, contact_id: str, body: str, actor_user: Optional[str] = None) -> Optional[CrmActivity]:
    note = str(body or "").strip()
    if not note:
        raise ValueError("note is required")

    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None

    return create_activity(
        db,
        contact_id=contact.id,
        related_type="note",
        activity_type="note.added",
        title="Note added",
        body=note,
        actor_user=actor_user,
    )


def assign_follow_up(
    db: Session,
    contact_id: str,
    title: str,
    due_at: Optional[str] = None,
    assigned_user: Optional[str] = None,
    priority: str = "normal",
) -> Optional[CrmTask]:
    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None
    clean_title = str(title or "").strip()
    if not clean_title:
        raise ValueError("title is required")
    due_value = None
    if due_at:
        try:
            due_value = datetime.fromisoformat(str(due_at).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError as exc:
            raise ValueError("due_at must be an ISO date/time") from exc
    task = CrmTask(
        contact_id=contact.id,
        assigned_user=assigned_user,
        title=clean_title,
        due_at=due_value,
        priority=str(priority or "normal").strip().lower()[:32] or "normal",
    )
    db.add(task)
    db.flush()
    create_activity(
        db,
        contact_id=contact.id,
        related_type="follow_up",
        related_id=task.id,
        activity_type="follow_up.assigned",
        title="Follow-up assigned",
        body=clean_title,
        actor_user=assigned_user,
        metadata={"due_at": due_at, "priority": task.priority},
    )
    return task


def mark_contact_resolved(db: Session, contact_id: str, actor_user: Optional[str] = None) -> Optional[dict[str, Any]]:
    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None
    contact.status = "resolved"
    conversations = db.query(CrmConversation).filter(CrmConversation.contact_id == contact.id).all()
    for conversation in conversations:
        conversation.status = "resolved"
        conversation.unread_count = 0
    create_activity(
        db,
        contact_id=contact.id,
        related_type="resolution",
        activity_type="review.resolved",
        title="Conversation marked resolved",
        actor_user=actor_user,
    )
    return {"contact": contact, "conversations_closed": len(conversations)}


def assistant_suggestions(db: Session, contact_id: str) -> Optional[dict[str, Any]]:
    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None
    timeline = list_timeline(db, contact_id)
    recent_text = " ".join(str(item.get("body") or "") for item in timeline[:8]).strip()
    lower_text = recent_text.lower()
    urgent_terms = ["urgent", "asap", "emergency", "today", "missed your call", "confused", "not sure"]
    is_urgent = any(term in lower_text for term in urgent_terms)
    if any(term in lower_text for term in ["proposal", "quote", "estimate", "price"]):
        intent = "proposal_request"
        next_action = "start_proposal_or_follow_up"
        draft = (
            "Thanks for reaching out. I saw your message about the proposal. "
            "I can help without making this a long back-and-forth. "
            "Next, I will confirm the details and send the proposal or the one missing question."
        )
    elif any(term in lower_text for term in ["appointment", "schedule", "call", "available"]):
        intent = "scheduling"
        next_action = "confirm_availability"
        draft = (
            "Thanks, I saw your scheduling request. "
            "We can keep this simple. "
            "Next, send the day and time window that works best and I will confirm availability."
        )
    else:
        intent = "general_service_request"
        next_action = "reply_and_clarify_need"
        draft = (
            "Thanks for the message. I saw what you sent and can help. "
            "This should be quick to sort out. "
            "Next, send a few details about what you need and I will point you to the right next step."
        )
    flags = []
    if is_urgent:
        flags.append("urgent_or_confusing")
    if "?" in recent_text and len(recent_text) < 40:
        flags.append("needs_clarification")
    summary = recent_text[:220] if recent_text else "No recent communication yet."
    return {
        "contact_id": contact.id,
        "intent": intent,
        "summary": summary,
        "draft_reply": draft,
        "suggested_next_action": next_action,
        "flags": flags,
        "needs_human_review": bool(flags),
    }


def create_draft_reply(db: Session, contact_id: str, actor_user: Optional[str] = None) -> Optional[CrmActivity]:
    suggestions = assistant_suggestions(db, contact_id)
    if not suggestions:
        return None
    return create_activity(
        db,
        contact_id=contact_id,
        related_type="review",
        activity_type="assistant.draft_reply",
        title="Draft reply ready for review",
        body=suggestions["draft_reply"],
        actor_user=actor_user,
        metadata={
            "intent": suggestions["intent"],
            "summary": suggestions["summary"],
            "suggested_next_action": suggestions["suggested_next_action"],
            "flags": suggestions["flags"],
            "status": "draft",
        },
    )


def update_review_activity(
    db: Session,
    activity_id: str,
    status_value: str,
    body: Optional[str] = None,
    actor_user: Optional[str] = None,
) -> Optional[CrmActivity]:
    activity = db.query(CrmActivity).filter(CrmActivity.id == activity_id).first()
    if not activity:
        return None
    metadata = safe_json(activity.metadata_json)
    metadata["status"] = str(status_value or "reviewed").strip().lower() or "reviewed"
    if actor_user:
        metadata["reviewed_by"] = actor_user
    activity.metadata_json = json.dumps(metadata)
    if body is not None:
        clean_body = str(body).strip()
        if clean_body:
            activity.body = clean_body
    return activity


def approve_and_send_draft(db: Session, activity_id: str, actor_user: Optional[str] = None) -> Optional[dict[str, Any]]:
    activity = update_review_activity(db, activity_id, "approved", actor_user=actor_user)
    if not activity:
        return None
    message = store_outbound_reply(db, activity.contact_id, activity.body or "")
    if not message:
        return None
    metadata = safe_json(activity.metadata_json)
    metadata["sent_message_id"] = message.id
    activity.metadata_json = json.dumps(metadata)
    return {"activity": activity, "message": message}


def start_quote_from_contact(db: Session, contact_id: str) -> Optional[dict[str, Any]]:
    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None
    create_activity(
        db,
        contact_id=contact.id,
        related_type="quote",
        activity_type="quote.started",
        title="Proposal started",
        body="Proposal handoff started from contact.",
    )
    return {
        "status": "ready",
        "contact_id": contact.id,
        "quote_url": f"/crm/workspace?contact_id={contact.id}&proposal=1",
        "prefill": {
            "customer_name": contact.display_name,
            "phone": contact.mobile_phone if not str(contact.mobile_phone or "").startswith(("email:", "name:")) else None,
            "email": contact.email,
        },
    }


def create_activity(
    db: Session,
    *,
    contact_id: str,
    related_type: str,
    activity_type: str,
    title: str,
    body: Optional[str] = None,
    related_id: Optional[str] = None,
    actor_user: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> CrmActivity:
    activity = CrmActivity(
        contact_id=contact_id,
        related_type=related_type,
        related_id=related_id,
        activity_type=activity_type,
        title=title,
        body=body,
        actor_user=actor_user,
        metadata_json=json.dumps(metadata or {}),
    )
    db.add(activity)
    db.flush()
    return activity


def serialize_contact(contact: CrmContact) -> dict[str, Any]:
    primary_site = contact.sites[0] if contact.sites else None
    latest_quote = contact.quotes[0] if contact.quotes else None
    return {
        "id": contact.id,
        "display_name": contact.display_name,
        "mobile_phone": contact.mobile_phone,
        "email": contact.email,
        "status": contact.status,
        "source": contact.source,
        "assigned_user": contact.assigned_user,
        "primary_site": serialize_site(primary_site) if primary_site else None,
        "latest_quote": serialize_quote_summary(latest_quote) if latest_quote else None,
        "created_at": contact.created_at.isoformat() if contact.created_at else None,
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
    }


def serialize_site(site: CrmServiceSite) -> dict[str, Any]:
    return {
        "id": site.id,
        "label": site.label,
        "address_line_1": site.address_line_1,
        "city": site.city,
        "state": site.state,
        "zip": site.zip,
        "delivery_zone": site.delivery_zone,
        "site_notes": site.site_notes,
    }


def serialize_quote_summary(quote: CrmQuote) -> dict[str, Any]:
    return {
        "id": quote.id,
        "quote_number": quote.quote_number,
        "title": quote.title,
        "status": quote.status,
        "grand_total": money(quote.grand_total),
        "updated_at": quote.updated_at.isoformat() if quote.updated_at else None,
    }


def serialize_activity(activity: CrmActivity) -> dict[str, Any]:
    metadata = safe_json(activity.metadata_json)
    return {
        "id": activity.id,
        "contact_id": activity.contact_id,
        "related_type": activity.related_type,
        "related_id": activity.related_id,
        "activity_type": activity.activity_type,
        "title": activity.title,
        "body": activity.body,
        "actor_user": activity.actor_user,
        "metadata": metadata,
        "system_generated": bool(metadata.get("system_generated")),
        "created_at": activity.created_at.isoformat() if activity.created_at else None,
    }


def safe_json(value: Optional[str]) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def demo_marker_exists(db: Session, marker: str) -> bool:
    return (
        db.query(CrmActivity)
        .filter(CrmActivity.metadata_json.like(f'%"demo_marker": "{marker}"%'))
        .first()
        is not None
    )


def list_contacts(db: Session) -> list[dict[str, Any]]:
    contacts = (
        db.query(CrmContact)
        .options(selectinload(CrmContact.sites), selectinload(CrmContact.quotes))
        .order_by(desc(CrmContact.updated_at))
        .limit(200)
        .all()
    )
    return [serialize_contact(contact) for contact in contacts]


def get_contact_detail(db: Session, contact_id: str) -> Optional[dict[str, Any]]:
    contact = (
        db.query(CrmContact)
        .options(
            selectinload(CrmContact.sites),
            selectinload(CrmContact.conversations),
            selectinload(CrmContact.quotes),
            selectinload(CrmContact.tasks),
        )
        .filter(CrmContact.id == contact_id)
        .first()
    )
    if not contact:
        return None
    return {
        "contact": serialize_contact(contact),
        "sites": [serialize_site(site) for site in contact.sites],
        "conversations": [serialize_conversation_summary(conversation) for conversation in contact.conversations],
        "quotes": [serialize_quote_summary(quote) for quote in contact.quotes],
        "tasks": [serialize_task(task) for task in contact.tasks],
        "account_summary": build_account_summary(db, contact_id),
        "timeline": list_timeline(db, contact_id),
    }


def serialize_conversation_summary(conversation: CrmConversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "contact_id": conversation.contact_id,
        "channel_type": conversation.channel_type,
        "status": conversation.status,
        "unread_count": conversation.unread_count,
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
    }


def serialize_task(task: CrmTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "contact_id": task.contact_id,
        "assigned_user": task.assigned_user,
        "title": task.title,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "status": task.status,
        "priority": task.priority,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


def list_timeline(db: Session, contact_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(CrmActivity)
        .filter(CrmActivity.contact_id == contact_id)
        .order_by(desc(CrmActivity.created_at))
        .limit(100)
        .all()
    )
    return [serialize_activity(row) for row in rows]


def latest_priority_metadata(db: Session, contact_id: str) -> dict[str, Any]:
    activities = (
        db.query(CrmActivity)
        .filter(
            CrmActivity.contact_id == contact_id,
            CrmActivity.metadata_json.like('%"priority_score"%'),
        )
        .order_by(desc(CrmActivity.created_at))
        .limit(20)
        .all()
    )
    metadata_rows = [safe_json(activity.metadata_json) for activity in activities]
    metadata_rows = [metadata for metadata in metadata_rows if metadata.get("priority_score") is not None]
    if metadata_rows:
        return max(metadata_rows, key=lambda metadata: int(metadata.get("priority_score") or 0))
    return priority_for_match(None)


def list_conversations(db: Session) -> list[dict[str, Any]]:
    conversations = (
        db.query(CrmConversation)
        .options(selectinload(CrmConversation.contact), selectinload(CrmConversation.messages))
        .order_by(desc(CrmConversation.last_message_at), desc(CrmConversation.created_at))
        .limit(200)
        .all()
    )
    result = []
    for conversation in conversations:
        messages = sorted(conversation.messages, key=lambda item: item.created_at or datetime.min)
        last_message = messages[-1] if messages else None
        priority_meta = latest_priority_metadata(db, conversation.contact_id)
        summary = build_account_summary(
            db,
            conversation.contact_id,
            match_type=priority_meta.get("match_type"),
            priority=priority_meta.get("priority"),
            priority_score=priority_meta.get("priority_score"),
        )
        result.append(
            {
                **serialize_conversation_summary(conversation),
                "display_name": conversation.contact.display_name,
                "mobile_phone": conversation.contact.mobile_phone,
                "email": conversation.contact.email,
                "last_message_body": last_message.body if last_message else None,
                "last_message_direction": last_message.direction if last_message else None,
                "matched_existing_contact": summary.get("priority") in {"existing_contact", "matched_contact"},
                "match_type": summary.get("match_type"),
                "priority": summary.get("priority"),
                "priority_score": summary.get("priority_score", 50),
                "account_summary": summary,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            int(item.get("priority_score") or 0),
            item.get("last_message_at") or "",
        ),
        reverse=True,
    )


def get_conversation_detail(db: Session, conversation_id: str) -> Optional[dict[str, Any]]:
    conversation = (
        db.query(CrmConversation)
        .options(
            selectinload(CrmConversation.contact).selectinload(CrmContact.sites),
            selectinload(CrmConversation.contact).selectinload(CrmContact.quotes),
            selectinload(CrmConversation.messages),
        )
        .filter(CrmConversation.id == conversation_id)
        .first()
    )
    if not conversation:
        return None
    conversation.unread_count = 0
    return {
        "conversation": serialize_conversation_summary(conversation),
        "contact": serialize_contact(conversation.contact),
        "messages": [
            {
                "id": message.id,
                "direction": message.direction,
                "channel": message.channel,
                "body": message.body,
                "delivery_status": message.delivery_status,
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in sorted(conversation.messages, key=lambda item: item.created_at or datetime.min)
        ],
        "timeline": list_timeline(db, conversation.contact_id),
    }


def send_message(db: Session, conversation_id: str, body: str, actor_user: Optional[str] = None) -> Optional[dict[str, Any]]:
    conversation = db.query(CrmConversation).filter(CrmConversation.id == conversation_id).first()
    if not conversation:
        return None
    message = CrmMessage(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        direction="outbound",
        channel="sms",
        body=body,
        delivery_status="mock_sent",
        sent_by_user=actor_user,
    )
    db.add(message)
    db.flush()
    conversation.last_message_at = message.created_at or datetime.utcnow()
    conversation.unread_count = 0
    create_activity(
        db,
        contact_id=conversation.contact_id,
        related_type="message",
        related_id=message.id,
        activity_type="message.outbound",
        title="Outbound text logged",
        body=body,
        actor_user=actor_user,
        metadata={"provider": "mock"},
    )
    return {"id": message.id, "delivery_status": message.delivery_status}


def dashboard(db: Session) -> dict[str, Any]:
    unread_texts = db.query(func.coalesce(func.sum(CrmConversation.unread_count), 0)).scalar() or 0
    missed_calls = db.query(CrmCall).filter(CrmCall.status.in_(["missed", "no-answer"])).count()
    new_leads = db.query(CrmContact).filter(CrmContact.status.in_(["lead", "new_lead"])).count()
    quote_followups = db.query(CrmQuote).filter(CrmQuote.status.in_(["draft", "sent"])).count()
    tasks_due = db.query(CrmTask).filter(CrmTask.status != "completed").count()
    activity = db.query(CrmActivity).order_by(desc(CrmActivity.created_at)).limit(10).all()
    followups = (
        db.query(CrmTask)
        .filter(CrmTask.status != "completed")
        .order_by(CrmTask.due_at, desc(CrmTask.created_at))
        .limit(5)
        .all()
    )
    quote_activity = (
        db.query(CrmActivity)
        .filter(CrmActivity.activity_type.like("quote.%"))
        .order_by(desc(CrmActivity.created_at))
        .limit(5)
        .all()
    )
    return {
        "metrics": {
            "unreadTexts": int(unread_texts),
            "missedCalls": missed_calls,
            "newLeads": new_leads,
            "quotesAwaitingFollowUp": quote_followups,
            "tasksDueToday": tasks_due,
        },
        "recentActivity": [serialize_activity(row) for row in activity],
        "followUps": [serialize_task(row) for row in followups],
        "quoteActivity": [serialize_activity(row) for row in quote_activity],
    }


def list_quotes(db: Session) -> list[dict[str, Any]]:
    quotes = db.query(CrmQuote).order_by(desc(CrmQuote.updated_at)).limit(200).all()
    return [serialize_quote_summary(quote) for quote in quotes]


def list_calls(db: Session) -> list[dict[str, Any]]:
    calls = db.query(CrmCall).order_by(desc(CrmCall.started_at)).limit(200).all()
    return [
        {
            "id": call.id,
            "contact_id": call.contact_id,
            "direction": call.direction,
            "status": call.status,
            "from_number": call.from_number,
            "to_number": call.to_number,
            "duration_seconds": call.duration_seconds,
            "disposition": call.disposition,
            "notes": call.notes,
            "started_at": call.started_at.isoformat() if call.started_at else None,
        }
        for call in calls
    ]


def list_external_links(db: Session) -> list[dict[str, Any]]:
    links = db.query(CrmExternalLink).order_by(desc(CrmExternalLink.created_at)).limit(200).all()
    return [
        {
            "id": link.id,
            "internal_type": link.internal_type,
            "internal_id": link.internal_id,
            "external_system": link.external_system,
            "external_id": link.external_id,
            "metadata": safe_json(link.metadata_json),
            "created_at": link.created_at.isoformat() if link.created_at else None,
        }
        for link in links
    ]


def seed_demo(db: Session) -> dict[str, Any]:
    now = datetime.utcnow()
    created = 0
    demo_contacts = [
        {
            "marker": "demo-rivera",
            "name": "Maya Rivera",
            "phone": "+15035550161",
            "email": "maya.rivera@example.com",
            "status": "lead",
            "source": "website",
            "site": ("Home", "1842 SE Oak St", "Portland", "OR", "97214", "Central"),
            "messages": [
                ("inbound", "Hi, can you help with a service appointment next week?", 90),
                ("outbound", "Yes. I can start a proposal today. Do you prefer text updates?", 84),
                ("inbound", "Text is best. Afternoon appointments work.", 12),
            ],
            "task": "Send proposal with afternoon scheduling options",
            "note": "Prefers text updates. Afternoon appointments only.",
        },
        {
            "marker": "demo-chen",
            "name": "Noah Chen",
            "phone": "+15035550162",
            "email": "noah.chen@example.com",
            "status": "proposal_sent",
            "source": "referral",
            "site": ("Office", "7309 N Willamette Blvd", "Portland", "OR", "97203", "North"),
            "messages": [
                ("inbound", "Can you resend the proposal for the service plan?", 55),
                ("outbound", "Absolutely. I sent it again and can adjust the scope if needed.", 51),
            ],
            "task": "Follow up on sent proposal",
            "note": "Asked about splitting the work into two phases.",
        },
        {
            "marker": "demo-patel",
            "name": "Priya Patel",
            "phone": "+15035550163",
            "email": "priya.patel@example.com",
            "status": "lead",
            "source": "phone",
            "site": ("Primary site", "420 NE Fremont St", "Portland", "OR", "97212", "Inner NE"),
            "messages": [
                ("inbound", "I missed your call. Looking for a proposal for monthly service.", 35),
            ],
            "task": "Call back and confirm access width",
            "note": "Confirm access details before final proposal.",
        },
    ]

    for demo in demo_contacts:
        contact = resolve_contact(db, phone=demo["phone"], name=demo["name"], email=demo["email"])
        contact.status = demo["status"]
        contact.source = demo["source"]
        contact.assigned_user = "Sales"
        if not contact.sites:
            label, address, city, state, zip_code, zone = demo["site"]
            db.add(
                CrmServiceSite(
                    contact_id=contact.id,
                    label=label,
                    address_line_1=address,
                    city=city,
                    state=state,
                    zip=zip_code,
                    delivery_zone=zone,
                )
            )
            created += 1
        conversation = get_or_create_conversation(db, contact.id)
        conversation.status = "open"
        conversation.assigned_user = "Sales"
        if not demo_marker_exists(db, demo["marker"]):
            for direction, body, minutes_ago in demo["messages"]:
                created_at = now - timedelta(minutes=minutes_ago)
                message = CrmMessage(
                    conversation_id=conversation.id,
                    contact_id=contact.id,
                    direction=direction,
                    channel="sms",
                    body=body,
                    delivery_status="received" if direction == "inbound" else "mock_sent",
                    created_at=created_at,
                )
                db.add(message)
                db.flush()
                conversation.last_message_at = created_at
                conversation.unread_count = max(conversation.unread_count or 0, 1 if direction == "inbound" else 0)
                create_activity(
                    db,
                    contact_id=contact.id,
                    related_type="message",
                    related_id=message.id,
                    activity_type=f"message.{direction}",
                    title="Inbound message" if direction == "inbound" else "Outbound reply",
                    body=body,
                    metadata={"demo_marker": demo["marker"]},
                )
                created += 1
            create_activity(
                db,
                contact_id=contact.id,
                related_type="note",
                activity_type="note.added",
                title="Note added",
                body=demo["note"],
                metadata={"demo_marker": demo["marker"]},
            )
            db.add(
                CrmTask(
                    contact_id=contact.id,
                    assigned_user="Sales",
                    title=demo["task"],
                    due_at=now + timedelta(hours=2 + created),
                    priority="high" if demo["marker"] == "demo-rivera" else "normal",
                )
            )
            created += 2

    quote_contact = resolve_contact(db, phone="+15035550162", name="Noah Chen", email="noah.chen@example.com")
    if not db.query(CrmQuote).filter(CrmQuote.quote_number == "CRM-DEMO-1001").first():
        quote = CrmQuote(
            contact_id=quote_contact.id,
            quote_number="CRM-DEMO-1001",
            title="Service plan proposal",
            status="sent",
            subtotal=Decimal("1845.00"),
            delivery_total=Decimal("0.00"),
            tax_total=Decimal("0.00"),
            grand_total=Decimal("1940.00"),
            sent_at=now - timedelta(hours=2),
        )
        db.add(quote)
        db.flush()
        version = CrmQuoteVersion(
            quote_id=quote.id,
            version_number=1,
            pricing_snapshot_json=json.dumps({"source": "communication_crm_demo"}),
            subtotal=quote.subtotal,
            delivery_total=quote.delivery_total,
            tax_total=quote.tax_total,
            grand_total=quote.grand_total,
        )
        db.add(version)
        db.flush()
        quote.current_version_id = version.id
        db.add(
            CrmQuoteLineItem(
                quote_version_id=version.id,
                item_type="service",
                name="Service plan",
                quantity=1,
                unit="project",
                unit_price=1845,
                total_price=1845,
                sort_order=1,
                source_reference="demo",
            )
        )
        create_activity(
            db,
            contact_id=quote_contact.id,
            related_type="quote",
            related_id=str(quote.id),
            activity_type="quote.sent",
            title="Proposal sent",
            body="CRM-DEMO-1001 was sent for service plan review.",
            metadata={"demo_marker": "demo-quote"},
        )
        created += 4

    db.commit()
    return {"ok": True, "seeded": created > 0, "created": created}
