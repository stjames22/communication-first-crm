from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session, selectinload

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


def resolve_contact(
    db: Session,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> CrmContact:
    normalized_phone = normalize_phone(phone)
    normalized_email = normalize_email(email)
    clean_name = normalize_name(name)
    if not normalized_phone and not normalized_email and not clean_name:
        raise ValueError("phone, email, or name is required")

    contact = None
    if normalized_phone:
        contact = db.query(CrmContact).filter(CrmContact.mobile_phone == normalized_phone).first()
    if contact is None and normalized_email:
        contact = db.query(CrmContact).filter(func.lower(CrmContact.email) == normalized_email).first()
    if contact is None and clean_name:
        contact = db.query(CrmContact).filter(func.lower(CrmContact.display_name) == clean_name.lower()).first()

    if contact:
        if clean_name and contact.display_name == contact.mobile_phone:
            contact.display_name = clean_name
        if normalized_email and not contact.email:
            contact.email = normalized_email
        if normalized_phone and str(contact.mobile_phone or "").startswith(("email:", "name:")):
            contact.mobile_phone = normalized_phone
        return contact

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
    return contact


def get_or_create_conversation(db: Session, contact_id: str) -> CrmConversation:
    conversation = (
        db.query(CrmConversation)
        .filter(CrmConversation.contact_id == contact_id, CrmConversation.channel_type == "sms")
        .order_by(desc(CrmConversation.created_at))
        .first()
    )
    if conversation:
        return conversation

    conversation = CrmConversation(contact_id=contact_id, channel_type="sms", status="open")
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

    contact = resolve_contact(db, phone, name=name, email=email)
    conversation = get_or_create_conversation(db, contact.id)
    created_at = datetime.utcnow()
    channel_value = str(channel or "sms").strip().lower() or "sms"
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
        metadata={"channel": channel_value},
    )
    return {"contact": contact, "message": crm_message}


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


def start_quote_from_contact(db: Session, contact_id: str) -> Optional[dict[str, Any]]:
    contact = db.query(CrmContact).filter(CrmContact.id == contact_id).first()
    if not contact:
        return None
    create_activity(
        db,
        contact_id=contact.id,
        related_type="quote",
        activity_type="quote.started",
        title="Quote started",
        body="Quote handoff started from contact.",
    )
    return {
        "status": "ready",
        "contact_id": contact.id,
        "quote_url": f"/staff-estimator?contact_id={contact.id}",
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
    return {
        "id": activity.id,
        "contact_id": activity.contact_id,
        "related_type": activity.related_type,
        "related_id": activity.related_id,
        "activity_type": activity.activity_type,
        "title": activity.title,
        "body": activity.body,
        "actor_user": activity.actor_user,
        "metadata": safe_json(activity.metadata_json),
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
        result.append(
            {
                **serialize_conversation_summary(conversation),
                "display_name": conversation.contact.display_name,
                "mobile_phone": conversation.contact.mobile_phone,
                "email": conversation.contact.email,
                "last_message_body": last_message.body if last_message else None,
                "last_message_direction": last_message.direction if last_message else None,
            }
        )
    return result


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
    return {
        "metrics": {
            "unreadTexts": int(unread_texts),
            "missedCalls": missed_calls,
            "newLeads": new_leads,
            "quotesAwaitingFollowUp": quote_followups,
            "tasksDueToday": tasks_due,
        },
        "recentActivity": [serialize_activity(row) for row in activity],
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
    if db.query(CrmContact).count():
        return {"ok": True, "seeded": False}

    kyle = CrmContact(
        display_name="Kyle Bennett",
        mobile_phone="+15035550141",
        email="kyle@example.com",
        status="lead",
        source="website",
        assigned_user="Sales",
    )
    avery = CrmContact(
        display_name="Avery Cole",
        mobile_phone="+15035550142",
        email="avery@example.com",
        status="quoted",
        source="referral",
        assigned_user="Sales",
    )
    db.add_all([kyle, avery])
    db.flush()

    kyle_site = CrmServiceSite(
        contact_id=kyle.id,
        label="Home",
        address_line_1="2217 SE Alder St",
        city="Portland",
        state="OR",
        zip="97214",
        delivery_zone="Central",
    )
    avery_site = CrmServiceSite(
        contact_id=avery.id,
        label="Backyard Project",
        address_line_1="6110 N Omaha Ave",
        city="Portland",
        state="OR",
        zip="97217",
        delivery_zone="North",
    )
    db.add_all([kyle_site, avery_site])
    db.flush()

    conversation = CrmConversation(
        contact_id=kyle.id,
        channel_type="sms",
        status="open",
        unread_count=1,
        assigned_user="Sales",
        last_message_at=datetime.utcnow(),
    )
    db.add(conversation)
    db.flush()
    db.add_all(
        [
            CrmMessage(
                conversation_id=conversation.id,
                contact_id=kyle.id,
                direction="inbound",
                channel="sms",
                body="Can BarkBoys quote mulch and cleanup for my front beds?",
                delivery_status="received",
            ),
            CrmCall(
                contact_id=kyle.id,
                conversation_id=conversation.id,
                direction="inbound",
                status="missed",
                from_number=kyle.mobile_phone,
                to_number="+15035550000",
                duration_seconds=0,
            ),
        ]
    )

    quote = CrmQuote(
        contact_id=avery.id,
        service_site_id=avery_site.id,
        quote_number="CRM-BB-0001",
        title="BarkBoys backyard refresh",
        status="sent",
        subtotal=Decimal("1845.00"),
        delivery_total=Decimal("95.00"),
        tax_total=Decimal("0.00"),
        grand_total=Decimal("1940.00"),
        sent_at=datetime.utcnow() - timedelta(hours=2),
    )
    db.add(quote)
    db.flush()
    version = CrmQuoteVersion(
        quote_id=quote.id,
        version_number=1,
        pricing_snapshot_json=json.dumps({"source": "communication_crm_seed"}),
        subtotal=quote.subtotal,
        delivery_total=quote.delivery_total,
        tax_total=quote.tax_total,
        grand_total=quote.grand_total,
    )
    db.add(version)
    db.flush()
    quote.current_version_id = version.id
    db.add_all(
        [
            CrmQuoteLineItem(
                quote_version_id=version.id,
                item_type="service",
                name="Mulch installation",
                quantity=8,
                unit="yard",
                unit_price=145,
                total_price=1160,
                sort_order=1,
                source_reference="barkboys-compatible",
            ),
            CrmTask(
                contact_id=kyle.id,
                assigned_user="Sales",
                title="Reply with quote draft including edging option",
                due_at=datetime.utcnow() + timedelta(hours=2),
                priority="high",
            ),
            CrmExternalLink(
                internal_type="quote",
                internal_id=quote.id,
                external_system="barkboys",
                external_id="future-barkboys-quote-id",
                metadata_json=json.dumps({"note": "Safe bridge link; no BarkBoys quote table mutation."}),
            ),
        ]
    )

    create_activity(
        db,
        contact_id=kyle.id,
        related_type="message",
        activity_type="message.inbound",
        title="Inbound text received",
        body="Can BarkBoys quote mulch and cleanup for my front beds?",
    )
    create_activity(
        db,
        contact_id=kyle.id,
        related_type="call",
        activity_type="call.missed",
        title="Missed call",
        body="Missed inbound call from Kyle Bennett.",
    )
    create_activity(
        db,
        contact_id=avery.id,
        related_type="quote",
        related_id=quote.id,
        activity_type="quote.sent",
        title="Quote sent",
        body="CRM-BB-0001 was sent from the isolated CRM module.",
    )
    db.commit()
    return {"ok": True, "seeded": True}
