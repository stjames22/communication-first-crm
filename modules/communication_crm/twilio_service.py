from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from . import crm_service, front_desk_service
from .models import CrmContact, CrmConversation, CrmMessage


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = str(value).strip().lower()
    if not cleaned:
        return default
    return cleaned in {"1", "true", "yes", "y", "on"}


def communication_demo_mode() -> bool:
    return env_bool("COMMUNICATION_DEMO_MODE", True)


def twilio_credentials() -> dict[str, str]:
    return {
        "account_sid": os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
        "auth_token": os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
        "phone_number": crm_service.normalize_phone(os.getenv("TWILIO_PHONE_NUMBER", "")) or os.getenv("TWILIO_PHONE_NUMBER", "").strip(),
    }


def has_live_twilio_credentials() -> bool:
    credentials = twilio_credentials()
    return bool(credentials["account_sid"] and credentials["auth_token"] and credentials["phone_number"])


def should_validate_signatures() -> bool:
    return env_bool("TWILIO_VALIDATE_SIGNATURES", True)


def validate_twilio_signature(url: str, form_fields: dict[str, Any], signature: Optional[str]) -> bool:
    token = twilio_credentials()["auth_token"]
    if not token or not should_validate_signatures():
        return True
    if not signature:
        return False

    signed = url + "".join(f"{key}{form_fields[key]}" for key in sorted(form_fields))
    digest = hmac.new(token.encode("utf-8"), signed.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def handle_inbound_sms(db: Session, form_fields: dict[str, Any]) -> dict[str, Any]:
    from_number = crm_service.normalize_phone(form_fields.get("From")) or str(form_fields.get("From") or "").strip()
    to_number = crm_service.normalize_phone(form_fields.get("To")) or str(form_fields.get("To") or "").strip()
    body = str(form_fields.get("Body") or "").strip()
    message_sid = str(form_fields.get("MessageSid") or "").strip() or None

    if not from_number:
        raise ValueError("From is required")
    if not body:
        raise ValueError("Body is required")

    existing = None
    if message_sid:
        existing = db.query(CrmMessage).filter(CrmMessage.provider_message_id == message_sid).first()
    if existing:
        conversation = db.query(CrmConversation).filter(CrmConversation.id == existing.conversation_id).first()
        contact = db.query(CrmContact).filter(CrmContact.id == existing.contact_id).first()
        return {
            "status": "received",
            "duplicate": True,
            "contact": contact,
            "conversation": conversation,
            "message": existing,
            "summary": front_desk_service.build_context_summary(existing.body, conversation.status if conversation else "open"),
        }

    inbound = crm_service.store_inbound_message(
        db,
        phone=from_number,
        message=body,
        channel="sms",
        auto_first_message_enabled=False,
    )
    contact = inbound["contact"]
    message = inbound["message"]
    message.provider_message_id = message_sid
    message.delivery_status = "received"

    conversation = db.query(CrmConversation).filter(CrmConversation.id == message.conversation_id).first()
    if not conversation:
        raise ValueError("conversation could not be created")
    conversation.channel_type = "sms"
    conversation.status = "open" if conversation.status not in {"priority"} else conversation.status

    summary = front_desk_service.build_context_summary(body, conversation.status)
    front_desk_service.store_context_summary(db, conversation, summary)
    crm_service.create_activity(
        db,
        contact_id=contact.id,
        related_type="provider_event",
        related_id=message.id,
        activity_type="twilio.sms.inbound",
        title="Twilio SMS received",
        body=body,
        metadata={
            "provider": "twilio",
            "message_sid": message_sid,
            "from": from_number,
            "to": to_number,
            "summary": summary,
        },
    )
    return {
        "status": "received",
        "duplicate": False,
        "contact": contact,
        "conversation": conversation,
        "message": message,
        "summary": summary,
    }


def send_sms_reply(db: Session, conversation_id: str, body: str, actor_user: Optional[str] = None) -> Optional[dict[str, Any]]:
    clean_body = str(body or "").strip()
    if not clean_body:
        raise ValueError("body is required")

    conversation = db.query(CrmConversation).filter(CrmConversation.id == conversation_id).first()
    if not conversation:
        return None
    contact = db.query(CrmContact).filter(CrmContact.id == conversation.contact_id).first()
    if not contact:
        return None

    to_number = crm_service.normalize_phone(contact.mobile_phone)
    if not to_number or str(contact.mobile_phone or "").startswith(("email:", "name:")):
        raise ValueError("contact does not have a valid SMS phone number")

    if has_live_twilio_credentials():
        return _send_live_twilio_sms(db, conversation, to_number, clean_body, actor_user=actor_user)
    return _store_demo_outbound(db, conversation, clean_body, actor_user=actor_user)


def _store_demo_outbound(
    db: Session,
    conversation: CrmConversation,
    body: str,
    actor_user: Optional[str] = None,
) -> dict[str, Any]:
    message = _create_outbound_message(
        db,
        conversation=conversation,
        body=body,
        delivery_status="demo",
        provider_message_id=None,
        actor_user=actor_user,
    )
    crm_service.create_activity(
        db,
        contact_id=conversation.contact_id,
        related_type="message",
        related_id=message.id,
        activity_type="twilio.sms.demo_outbound",
        title="Demo SMS reply logged",
        body=body,
        actor_user=actor_user,
        metadata={"provider": "twilio", "mode": "demo", "warning": "Twilio credentials are not configured"},
    )
    return {
        "status": "demo",
        "mode": "demo",
        "warning": "Twilio credentials are missing; outbound SMS was stored locally only.",
        "message": message,
    }


def _send_live_twilio_sms(
    db: Session,
    conversation: CrmConversation,
    to_number: str,
    body: str,
    actor_user: Optional[str] = None,
) -> dict[str, Any]:
    credentials = twilio_credentials()
    status_value = "failed"
    provider_message_id = None
    warning = None
    metadata: dict[str, Any] = {"provider": "twilio", "mode": "live", "to": to_number, "from": credentials["phone_number"]}

    try:
        response = _post_twilio_message(
            account_sid=credentials["account_sid"],
            auth_token=credentials["auth_token"],
            from_number=credentials["phone_number"],
            to_number=to_number,
            body=body,
        )
        provider_message_id = str(response.get("sid") or "").strip() or None
        status_value = "sent"
        metadata["twilio_response"] = response
    except Exception as exc:
        warning = f"Twilio send failed: {exc}"
        metadata["error"] = str(exc)

    message = _create_outbound_message(
        db,
        conversation=conversation,
        body=body,
        delivery_status=status_value,
        provider_message_id=provider_message_id,
        actor_user=actor_user,
    )
    crm_service.create_activity(
        db,
        contact_id=conversation.contact_id,
        related_type="message",
        related_id=message.id,
        activity_type=f"twilio.sms.{status_value}",
        title="Live SMS sent" if status_value == "sent" else "Live SMS failed",
        body=body,
        actor_user=actor_user,
        metadata=metadata,
    )
    return {"status": status_value, "mode": "live", "warning": warning, "message": message}


def _create_outbound_message(
    db: Session,
    *,
    conversation: CrmConversation,
    body: str,
    delivery_status: str,
    provider_message_id: Optional[str],
    actor_user: Optional[str],
) -> CrmMessage:
    message = CrmMessage(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        direction="outbound",
        channel="sms",
        provider_message_id=provider_message_id,
        body=body,
        delivery_status=delivery_status,
        sent_by_user=actor_user,
        created_at=datetime.utcnow(),
    )
    db.add(message)
    db.flush()
    conversation.last_message_at = message.created_at
    conversation.unread_count = 0
    return message


def _post_twilio_message(
    *,
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    body: str,
) -> dict[str, Any]:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(account_sid)}/Messages.json"
    encoded = urllib.parse.urlencode({"To": to_number, "From": from_number, "Body": body}).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, method="POST")
    token = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(raw or exc.reason) from exc
    parsed = json.loads(raw or "{}")
    return parsed if isinstance(parsed, dict) else {}


def twiml_response(message: str = "") -> str:
    body = f"<Message>{_xml_escape(message)}</Message>" if message else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def _xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
