from __future__ import annotations

import json
import os
import re
import uuid
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import CrmActivity, CrmContact, CrmConversation, CrmQuote, CrmServiceSite, CrmTask


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


def adapter_name() -> str:
    value = str(os.getenv("COMMUNICATION_CRM_ADAPTER") or "local").strip().lower()
    return value if value in {"local", "barkboys", "external"} else "local"


def get_crm_adapter(db: Session) -> "CRMAdapter":
    configured = adapter_name()
    if configured == "barkboys":
        return BarkBoysCRMAdapter(db)
    if configured == "external":
        return ExternalCRMAdapter(db)
    return LocalCRMAdapter(db)


class CRMAdapter(ABC):
    """Contract between Communication Hub and a customer/system CRM."""

    name = "base"

    def __init__(self, db: Session) -> None:
        self.db = db

    @abstractmethod
    def find_contact(
        self,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_contact(self, payload: dict[str, Any]) -> CrmContact:
        raise NotImplementedError

    @abstractmethod
    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> Optional[CrmContact]:
        raise NotImplementedError

    @abstractmethod
    def get_contact_context(self, contact_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def link_conversation(self, contact_id: str, conversation_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_followup(self, contact_id: str, payload: dict[str, Any]) -> Optional[CrmTask]:
        raise NotImplementedError

    def create_quote_or_job(self, contact_id: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        return None

    def get_quote_or_job_context(self, contact_id: str) -> dict[str, Any]:
        return {}


class LocalCRMAdapter(CRMAdapter):
    name = "local"

    def find_contact(
        self,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        normalized_phone = normalize_phone(phone)
        normalized_email = normalize_email(email)
        clean_name = normalize_name(name)

        if normalized_phone:
            contact = self.db.query(CrmContact).filter(CrmContact.mobile_phone == normalized_phone).first()
            if contact:
                return {"contact": contact, "match_type": "phone"}

        if normalized_email:
            contact = self.db.query(CrmContact).filter(func.lower(CrmContact.email) == normalized_email).first()
            if contact:
                return {"contact": contact, "match_type": "email"}

        if clean_name:
            exact = self.db.query(CrmContact).filter(func.lower(CrmContact.display_name) == clean_name.lower()).first()
            if exact:
                return {"contact": exact, "match_type": "name"}
            for row in self.db.query(CrmContact).limit(300).all():
                if _name_similarity(clean_name, row.display_name) >= 0.92:
                    return {"contact": row, "match_type": "fuzzy_name"}

        return None

    def create_contact(self, payload: dict[str, Any]) -> CrmContact:
        normalized_phone = normalize_phone(payload.get("phone"))
        normalized_email = normalize_email(payload.get("email"))
        clean_name = normalize_name(payload.get("name"))
        display_name = clean_name or normalized_email or normalized_phone
        if not display_name:
            raise ValueError("phone, email, or name is required")
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
            status=str(payload.get("status") or "lead")[:32],
            source=str(payload.get("source") or "inbound")[:80],
        )
        self.db.add(contact)
        self.db.flush()
        return contact

    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> Optional[CrmContact]:
        contact = self.db.query(CrmContact).filter(CrmContact.id == contact_id).first()
        if not contact:
            return None
        clean_name = normalize_name(payload.get("name"))
        normalized_email = normalize_email(payload.get("email"))
        normalized_phone = normalize_phone(payload.get("phone"))
        if clean_name and (contact.display_name == contact.mobile_phone or contact.display_name.startswith(("email:", "name:"))):
            contact.display_name = clean_name
        if normalized_email and not contact.email:
            contact.email = normalized_email
        if normalized_phone and contact.mobile_phone != normalized_phone:
            owner = self.db.query(CrmContact).filter(CrmContact.mobile_phone == normalized_phone).first()
            if not owner or owner.id == contact.id:
                contact.mobile_phone = normalized_phone
        return contact

    def get_contact_context(self, contact_id: str) -> dict[str, Any]:
        contact = self.db.query(CrmContact).filter(CrmContact.id == contact_id).first()
        if not contact:
            return {}
        primary_site = contact.sites[0] if contact.sites else None
        latest_quote = contact.quotes[0] if contact.quotes else None
        return {
            "adapter": self.name,
            "contact_id": contact.id,
            "display_name": contact.display_name,
            "phone": contact.mobile_phone,
            "email": contact.email,
            "status": contact.status,
            "source": contact.source,
            "primary_site": _site_context(primary_site) if primary_site else None,
            "quote_or_job": _quote_context(latest_quote) if latest_quote else None,
        }

    def link_conversation(self, contact_id: str, conversation_id: str) -> None:
        conversation = self.db.query(CrmConversation).filter(CrmConversation.id == conversation_id).first()
        if conversation and conversation.contact_id != contact_id:
            conversation.contact_id = contact_id

    def create_followup(self, contact_id: str, payload: dict[str, Any]) -> Optional[CrmTask]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        contact = self.db.query(CrmContact).filter(CrmContact.id == contact_id).first()
        if not contact:
            return None
        task = CrmTask(
            contact_id=contact.id,
            assigned_user=str(payload.get("assigned_user") or "").strip() or None,
            title=title,
            priority=str(payload.get("priority") or "normal").strip().lower()[:32] or "normal",
        )
        self.db.add(task)
        self.db.flush()
        _create_activity(
            self.db,
            contact_id=contact.id,
            related_type="follow_up",
            related_id=task.id,
            activity_type="follow_up.assigned",
            title="Follow-up assigned",
            body=title,
            metadata={"adapter": self.name},
        )
        return task

    def create_quote_or_job(self, contact_id: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        return {
            "adapter": self.name,
            "status": "not_connected",
            "message": "Local CRM scaffold does not create quotes/jobs from Communication Hub yet.",
        }

    def get_quote_or_job_context(self, contact_id: str) -> dict[str, Any]:
        quote = self.db.query(CrmQuote).filter(CrmQuote.contact_id == contact_id).order_by(CrmQuote.updated_at.desc()).first()
        return {"adapter": self.name, "quote_or_job": _quote_context(quote) if quote else None}


class BarkBoysCRMAdapter(LocalCRMAdapter):
    name = "barkboys"

    def get_contact_context(self, contact_id: str) -> dict[str, Any]:
        context = super().get_contact_context(contact_id)
        context["adapter"] = self.name
        context["barkboys"] = {
            "site": context.get("primary_site"),
            "quote_or_job": context.get("quote_or_job"),
            "todo": [
                "Map Communication Hub contact to BarkBoys customer record.",
                "Map service site/address into quote intake defaults.",
                "Link conversation follow-up to quote/job lifecycle.",
            ],
        }
        return context

    def create_quote_or_job(self, contact_id: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        # TODO: connect to BarkBoys quote/job creation once the quote intake contract is finalized.
        return {
            "adapter": self.name,
            "status": "scaffold",
            "contact_id": contact_id,
            "message": "BarkBoys quote/job adapter scaffold loaded; quote/site creation is still TODO.",
        }

    def get_quote_or_job_context(self, contact_id: str) -> dict[str, Any]:
        context = super().get_quote_or_job_context(contact_id)
        context["adapter"] = self.name
        context["todo"] = "Use BarkBoys quote/site models as the first production adapter mapping."
        return context


class ExternalCRMAdapter(CRMAdapter):
    name = "external"

    def _not_configured(self) -> None:
        raise NotImplementedError("External CRM adapter requires a customer-specific implementation.")

    def find_contact(self, phone: Optional[str] = None, email: Optional[str] = None, name: Optional[str] = None) -> Optional[dict[str, Any]]:
        self._not_configured()

    def create_contact(self, payload: dict[str, Any]) -> CrmContact:
        self._not_configured()

    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> Optional[CrmContact]:
        self._not_configured()

    def get_contact_context(self, contact_id: str) -> dict[str, Any]:
        self._not_configured()

    def link_conversation(self, contact_id: str, conversation_id: str) -> None:
        self._not_configured()

    def create_followup(self, contact_id: str, payload: dict[str, Any]) -> Optional[CrmTask]:
        self._not_configured()


def resolve_contact_details_through_adapter(
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

    adapter = get_crm_adapter(db)
    found = adapter.find_contact(phone=normalized_phone, email=normalized_email, name=clean_name)
    if found:
        contact = found["contact"]
        match_type = str(found.get("match_type") or "adapter")
        adapter.update_contact(contact.id, {"phone": normalized_phone, "email": normalized_email, "name": clean_name})
    else:
        contact = adapter.create_contact({"phone": normalized_phone, "email": normalized_email, "name": clean_name, "source": "inbound"})
        match_type = "created"

    duplicate_candidates = _find_duplicate_candidates(
        db,
        contact=contact,
        normalized_phone=normalized_phone,
        normalized_email=normalized_email,
        clean_name=clean_name,
    )
    priority = _priority_for_match(match_type)
    return {
        "contact": contact,
        "adapter": adapter.name,
        "match_type": match_type,
        "matched_existing_contact": match_type != "created",
        "priority": priority["priority"],
        "priority_score": priority["priority_score"],
        "normalized": {"phone": normalized_phone, "email": normalized_email, "name": clean_name},
        "duplicate_warning": bool(duplicate_candidates),
        "warnings": ["possible_duplicate"] if duplicate_candidates else [],
        "duplicate_candidates": [_contact_summary(candidate) for candidate in duplicate_candidates],
    }


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
        for row in db.query(CrmContact).limit(300).all():
            if contact and row.id == contact.id:
                continue
            if row.id not in seen and _name_similarity(clean_name, row.display_name) >= 0.86:
                candidates.append(row)
                seen.add(row.id)
    if normalized_email:
        local_part = normalized_email.split("@", 1)[0]
        for row in db.query(CrmContact).filter(CrmContact.email.isnot(None)).limit(300).all():
            if contact and row.id == contact.id:
                continue
            row_local = str(row.email or "").lower().split("@", 1)[0]
            if row.id not in seen and local_part and row_local and SequenceMatcher(None, local_part, row_local).ratio() >= 0.9:
                candidates.append(row)
                seen.add(row.id)
    if normalized_phone:
        last_seven = re.sub(r"\D+", "", normalized_phone)[-7:]
        for row in db.query(CrmContact).limit(300).all():
            if contact and row.id == contact.id:
                continue
            row_digits = re.sub(r"\D+", "", str(row.mobile_phone or ""))
            if row.id not in seen and last_seven and row_digits.endswith(last_seven):
                candidates.append(row)
                seen.add(row.id)
    return candidates[:5]


def _priority_for_match(match_type: Optional[str]) -> dict[str, Any]:
    if match_type in {"phone", "provider_message_id", "provider_call_id"}:
        return {"priority": "existing_contact", "priority_score": 90}
    if match_type in {"email", "name", "fuzzy_name"}:
        return {"priority": "matched_contact", "priority_score": 75}
    return {"priority": "new_contact", "priority_score": 50}


def _contact_summary(contact: CrmContact) -> dict[str, Any]:
    return {
        "id": contact.id,
        "display_name": contact.display_name,
        "mobile_phone": contact.mobile_phone,
        "email": contact.email,
        "status": contact.status,
        "source": contact.source,
    }


def _site_context(site: CrmServiceSite) -> dict[str, Any]:
    return {
        "id": site.id,
        "label": site.label,
        "address_line_1": site.address_line_1,
        "city": site.city,
        "state": site.state,
        "zip": site.zip,
        "delivery_zone": site.delivery_zone,
    }


def _quote_context(quote: CrmQuote) -> dict[str, Any]:
    return {
        "id": quote.id,
        "quote_number": quote.quote_number,
        "title": quote.title,
        "status": quote.status,
        "updated_at": quote.updated_at.isoformat() if quote.updated_at else None,
    }


def _create_activity(
    db: Session,
    *,
    contact_id: str,
    related_type: str,
    activity_type: str,
    title: str,
    body: Optional[str] = None,
    related_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> CrmActivity:
    activity = CrmActivity(
        contact_id=contact_id,
        related_type=related_type,
        related_id=related_id,
        activity_type=activity_type,
        title=title,
        body=body,
        metadata_json=json.dumps(metadata or {}),
    )
    db.add(activity)
    db.flush()
    return activity
