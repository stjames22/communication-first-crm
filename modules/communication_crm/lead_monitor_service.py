from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlalchemy import desc, or_
from sqlalchemy.orm import Session, selectinload

from . import crm_service
from .models import CrmContact, CrmLeadSignal

SOURCE_TYPES = {"facebook", "facebook_group", "google", "reddit", "other"}
LEAD_STATUSES = {"new", "watching", "contacted", "dismissed"}

KEYWORDS = {
    "insurance": 18,
    "broker": 14,
    "agent": 12,
    "recommend": 10,
    "recommendation": 10,
    "quote": 16,
    "home insurance": 24,
    "auto insurance": 24,
    "car insurance": 22,
    "business insurance": 24,
    "life insurance": 20,
    "moving to": 12,
    "portland": 8,
    "bend": 8,
    "salem": 8,
}

LOCATION_WORDS = ("Portland", "Bend", "Salem")
ZIP_RE = re.compile(r"\b(?:97[0-9]{3})(?:-\d{4})?\b")


def analyze_lead_signal(payload: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(payload.get("raw_text") or payload.get("text") or "").strip()
    if not raw_text:
        raise ValueError("pasted text is required")

    lower = raw_text.lower()
    matched = [keyword for keyword in KEYWORDS if keyword in lower]
    zip_matches = ZIP_RE.findall(raw_text)
    locations = [name for name in LOCATION_WORDS if name.lower() in lower]
    if zip_matches:
        matched.append("zip code")

    lead_type = detect_lead_type(lower)
    urgency = detect_urgency(lower, matched)
    score = min(100, sum(KEYWORDS.get(keyword, 10) for keyword in matched))
    if lead_type != "unknown":
        score += 12
    if urgency == "high":
        score += 15
    elif urgency == "medium":
        score += 7
    score = min(100, score)

    location_detected = first_present(
        str(payload.get("area_location") or "").strip(),
        ", ".join(locations + zip_matches),
    )
    recommended_action = recommended_action_for(score, urgency)
    summary = summarize_intent(raw_text, lead_type, location_detected)

    return {
        "lead_score": score,
        "lead_type": lead_type,
        "urgency": urgency,
        "location_detected": location_detected,
        "intent_summary": summary,
        "suggested_reply": suggested_reply(location_detected),
        "recommended_action": recommended_action,
        "matched_keywords": sorted(set(matched)),
    }


def create_lead_signal(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    source_type = normalize_source_type(payload.get("source_type"))
    raw_text = str(payload.get("raw_text") or "").strip()
    if not raw_text:
        raise ValueError("raw_text is required")
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else analyze_lead_signal(payload)

    lead = CrmLeadSignal(
        source_type=source_type,
        source_url=optional_string(payload.get("source_url")),
        area_location=optional_string(payload.get("area_location")),
        raw_text=raw_text,
        lead_score=int(analysis.get("lead_score") or 0),
        lead_type=str(analysis.get("lead_type") or "unknown"),
        urgency=str(analysis.get("urgency") or "low"),
        location_detected=optional_string(analysis.get("location_detected")),
        intent_summary=str(analysis.get("intent_summary") or ""),
        suggested_reply=str(analysis.get("suggested_reply") or ""),
        recommended_action=str(analysis.get("recommended_action") or "watch"),
        matched_keywords_json=json.dumps(analysis.get("matched_keywords") or []),
        status=str(payload.get("status") or "new"),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return serialize_lead_signal(lead)


def list_lead_signals(db: Session) -> list[dict[str, Any]]:
    rows = db.query(CrmLeadSignal).order_by(desc(CrmLeadSignal.created_at)).limit(200).all()
    return [serialize_lead_signal(row) for row in rows]


def update_lead_signal(db: Session, lead_id: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    lead = db.get(CrmLeadSignal, lead_id)
    if not lead:
        return None
    status = optional_string(payload.get("status"))
    if status:
        if status not in LEAD_STATUSES:
            raise ValueError("status must be new, watching, contacted, or dismissed")
        lead.status = status
    db.commit()
    db.refresh(lead)
    return serialize_lead_signal(lead)


def attach_lead_to_customer(db: Session, lead_id: str, contact_id: str) -> Optional[dict[str, Any]]:
    lead = db.get(CrmLeadSignal, lead_id)
    contact = db.get(CrmContact, contact_id)
    if not lead or not contact:
        return None

    lead.attached_contact_id = contact.id
    lead.status = "contacted" if lead.status == "new" else lead.status
    crm_service.create_activity(
        db,
        contact_id=contact.id,
        related_type="lead_signal",
        related_id=lead.id,
        activity_type="lead_signal.attached",
        title="Lead signal attached",
        body=f"{lead.intent_summary}\n\nSource text: {lead.raw_text}",
        metadata={
            "source_type": lead.source_type,
            "source_url": lead.source_url,
            "lead_score": lead.lead_score,
            "suggested_reply": lead.suggested_reply,
        },
    )
    db.commit()
    db.refresh(lead)
    return {"lead": serialize_lead_signal(lead), "contact": crm_service.serialize_contact(contact)}


def search_customers(db: Session, query: str) -> list[dict[str, Any]]:
    term = str(query or "").strip()
    if not term:
        return []
    like = f"%{term}%"
    rows = (
        db.query(CrmContact)
        .options(selectinload(CrmContact.sites))
        .outerjoin(CrmContact.sites)
        .filter(
            or_(
                CrmContact.display_name.ilike(like),
                CrmContact.mobile_phone.ilike(like),
                CrmContact.email.ilike(like),
                CrmContact.sites.any(),  # keeps relationship available for SQLite query planning
            )
        )
        .limit(20)
        .all()
    )
    filtered = []
    needle = term.lower()
    for contact in rows:
        address_text = " ".join(
            " ".join(filter(None, [site.address_line_1, site.city, site.state, site.zip]))
            for site in contact.sites
        ).lower()
        if (
            needle in str(contact.display_name or "").lower()
            or needle in str(contact.mobile_phone or "").lower()
            or needle in str(contact.email or "").lower()
            or needle in address_text
        ):
            filtered.append(crm_service.serialize_contact(contact))
    return filtered


def serialize_lead_signal(lead: CrmLeadSignal) -> dict[str, Any]:
    try:
        matched_keywords = json.loads(lead.matched_keywords_json or "[]")
    except json.JSONDecodeError:
        matched_keywords = []
    return {
        "id": lead.id,
        "source_type": lead.source_type,
        "source_url": lead.source_url,
        "area_location": lead.area_location,
        "raw_text": lead.raw_text,
        "lead_score": lead.lead_score,
        "lead_type": lead.lead_type,
        "urgency": lead.urgency,
        "location_detected": lead.location_detected,
        "intent_summary": lead.intent_summary,
        "suggested_reply": lead.suggested_reply,
        "recommended_action": lead.recommended_action,
        "matched_keywords": matched_keywords,
        "status": lead.status,
        "attached_contact_id": lead.attached_contact_id,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }


def detect_lead_type(text: str) -> str:
    if "home insurance" in text or "homeowners" in text:
        return "home insurance"
    if "auto insurance" in text or "car insurance" in text:
        return "auto insurance"
    if "business insurance" in text or "commercial insurance" in text:
        return "business insurance"
    if "life insurance" in text:
        return "life insurance"
    if "insurance" in text:
        return "general insurance"
    return "unknown"


def detect_urgency(text: str, matched: list[str]) -> str:
    if any(word in text for word in ["asap", "urgent", "today", "this week", "need a quote"]) or "quote" in matched:
        return "high"
    if any(word in text for word in ["recommend", "moving to", "looking for", "need"]):
        return "medium"
    return "low"


def recommended_action_for(score: int, urgency: str) -> str:
    if score >= 70 or urgency == "high":
        return "save lead"
    if score >= 45:
        return "reply"
    if score >= 20:
        return "watch"
    return "ignore"


def summarize_intent(raw_text: str, lead_type: str, location: str) -> str:
    clipped = " ".join(raw_text.split())[:180]
    location_part = f" in {location}" if location else ""
    if lead_type == "unknown":
        return f"Potential local lead{location_part}: {clipped}"
    return f"Potential {lead_type} lead{location_part}: {clipped}"


def suggested_reply(location: str) -> str:
    location_text = f" in {location}" if location else ""
    return (
        f"Hi - I saw your post about looking for insurance help{location_text}. "
        "I am an independent insurance advisor and would be happy to help compare options if you are still looking."
    )


def normalize_source_type(value: object) -> str:
    normalized = str(value or "other").strip().lower().replace(" ", "_").replace("-", "_")
    return normalized if normalized in SOURCE_TYPES else "other"


def optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def first_present(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""
