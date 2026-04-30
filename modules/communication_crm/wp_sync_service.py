from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from . import crm_service, lead_monitor_service
from .models import CrmActivity, CrmContact, CrmLeadSignal, CrmWebsiteEvent

ALLOWED_EVENT_TYPES = {"visit", "easy_link_click", "lead_form_view", "lead_form_submit"}


def ingest_website_event(db: Session, payload: dict[str, Any], event_type: Optional[str] = None) -> dict[str, Any]:
    clean_type = str(event_type or payload.get("event_type") or "visit").strip().lower()
    if clean_type not in ALLOWED_EVENT_TYPES:
        raise ValueError("event_type must be visit, easy_link_click, lead_form_view, or lead_form_submit")

    event = CrmWebsiteEvent(
        event_type=clean_type,
        source_system=clean_text(payload.get("source_system"), "wordpress")[:80],
        page_url=optional_text(payload.get("page_url"), 512),
        page_title=optional_text(payload.get("page_title"), 255),
        referrer=optional_text(payload.get("referrer"), 512),
        link_key=optional_text(payload.get("link_key"), 120),
        link_label=optional_text(payload.get("link_label"), 190),
        campaign=optional_text(payload.get("campaign"), 120),
        destination_url=optional_text(payload.get("destination_url"), 512),
        visitor_id_hash=hash_visitor(payload.get("visitor_id_hash") or payload.get("visitor_id")),
        metadata_json=json.dumps(clean_metadata(payload.get("metadata"))),
    )
    db.add(event)
    db.flush()
    return serialize_website_event(event)


def ingest_wordpress_lead(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    name = clean_text(payload.get("name"), "Website Lead")
    email = optional_text(payload.get("email"), 256)
    phone = optional_text(payload.get("phone"), 64)
    source = clean_text(payload.get("source"), "wordpress")
    message = clean_text(payload.get("message") or payload.get("body"), "")
    page_url = optional_text(payload.get("page_url"), 512)
    source_url = optional_text(payload.get("source_url") or page_url, 512)

    if not message:
        message = f"Website lead submitted from {source}."

    result = crm_service.store_inbound_message(
        db,
        phone=phone,
        name=name,
        email=email,
        message=message,
        channel="website",
    )
    contact: CrmContact = result["contact"]
    contact.source = source[:80]
    if contact.status in {"new", "lead"}:
        contact.status = "lead"

    event = CrmWebsiteEvent(
        event_type="lead_form_submit",
        source_system="wordpress",
        page_url=page_url,
        page_title=optional_text(payload.get("page_title"), 255),
        referrer=optional_text(payload.get("referrer"), 512),
        campaign=optional_text(payload.get("campaign"), 120),
        visitor_id_hash=hash_visitor(payload.get("visitor_id_hash") or payload.get("visitor_id")),
        metadata_json=json.dumps(clean_metadata(payload.get("metadata"))),
    )
    db.add(event)
    db.flush()

    crm_service.create_activity(
        db,
        contact_id=contact.id,
        related_type="wordpress",
        related_id=event.id,
        activity_type="wordpress.lead_synced",
        title="WordPress lead synced",
        body=message,
        actor_user="wordpress",
        metadata={
            "source": source,
            "page_url": page_url,
            "source_url": source_url,
            "event_id": event.id,
            "matched_existing_contact": result["resolution"]["matched_existing_contact"],
            "match_type": result["resolution"]["match_type"],
            "priority": result["resolution"]["priority"],
            "priority_score": result["resolution"]["priority_score"],
        },
    )

    lead_signal = maybe_create_lead_signal(db, contact.id, message, source_url, payload)

    return {
        "status": "synced",
        "contact": crm_service.serialize_contact(contact),
        "contact_id": contact.id,
        "message_id": result["message"].id,
        "event": serialize_website_event(event),
        "lead_signal": lead_signal,
        "matched_existing_contact": result["resolution"]["matched_existing_contact"],
        "match_type": result["resolution"]["match_type"],
    }


def maybe_create_lead_signal(
    db: Session,
    contact_id: str,
    message: str,
    source_url: Optional[str],
    payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    if payload.get("create_lead_signal", True) is False:
        return None

    analysis_payload = {
        "source_type": "other",
        "source_url": source_url,
        "area_location": payload.get("area_location") or payload.get("location") or "",
        "raw_text": message,
    }
    try:
        lead_signal = lead_monitor_service.create_lead_signal(
            db,
            {
                **analysis_payload,
                "status": "new",
                "analysis": lead_monitor_service.analyze_lead_signal(analysis_payload),
            },
        )
    except ValueError:
        return None

    lead = db.get(CrmLeadSignal, lead_signal["id"])
    if lead:
        lead.attached_contact_id = contact_id
    return lead_signal


def wordpress_dashboard_summary(db: Session) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=30)
    visits = db.query(CrmWebsiteEvent).filter(CrmWebsiteEvent.event_type == "visit", CrmWebsiteEvent.created_at >= since).count()
    clicks = db.query(CrmWebsiteEvent).filter(CrmWebsiteEvent.event_type == "easy_link_click", CrmWebsiteEvent.created_at >= since).count()
    leads = db.query(CrmWebsiteEvent).filter(CrmWebsiteEvent.event_type == "lead_form_submit", CrmWebsiteEvent.created_at >= since).count()
    open_contacts = db.query(CrmContact).filter(CrmContact.status.in_(["lead", "new_lead", "follow_up", "quoted"])).count()

    top_links = (
        db.query(
            CrmWebsiteEvent.link_key,
            func.max(CrmWebsiteEvent.link_label).label("label"),
            func.max(CrmWebsiteEvent.campaign).label("campaign"),
            func.count(CrmWebsiteEvent.id).label("clicks"),
        )
        .filter(CrmWebsiteEvent.event_type == "easy_link_click", CrmWebsiteEvent.created_at >= since)
        .group_by(CrmWebsiteEvent.link_key)
        .order_by(desc("clicks"))
        .limit(8)
        .all()
    )
    recent_activity = db.query(CrmActivity).order_by(desc(CrmActivity.created_at)).limit(8).all()

    return {
        "metrics": {
            "visits30Days": visits,
            "trackedClicks30Days": clicks,
            "wordpressLeads30Days": leads,
            "openContacts": open_contacts,
        },
        "topLinks": [
            {
                "link_key": row.link_key,
                "label": row.label or row.link_key or "Unknown link",
                "campaign": row.campaign or "",
                "clicks": int(row.clicks or 0),
            }
            for row in top_links
        ],
        "recentActivity": [crm_service.serialize_activity(row) for row in recent_activity],
    }


def serialize_website_event(event: CrmWebsiteEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "source_system": event.source_system,
        "page_url": event.page_url,
        "page_title": event.page_title,
        "referrer": event.referrer,
        "link_key": event.link_key,
        "link_label": event.link_label,
        "campaign": event.campaign,
        "destination_url": event.destination_url,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def clean_text(value: Any, default: str = "") -> str:
    text = " ".join(str(value or default).split())
    return text or default


def optional_text(value: Any, limit: int) -> Optional[str]:
    text = clean_text(value)
    return text[:limit] if text else None


def hash_visitor(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    if len(text) >= 48 and all(char in "0123456789abcdefABCDEF" for char in text[:48]):
        return text[:128]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = clean_text(key)[:80]
        if not clean_key:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            clean[clean_key] = item
        else:
            clean[clean_key] = str(item)[:500]
    return clean
