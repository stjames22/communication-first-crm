from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db

from . import crm_service, twilio_service

router = APIRouter()


@router.post("/api/twilio/sms/inbound")
async def twilio_sms_inbound(
    request: Request,
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    db: Session = Depends(get_db),
) -> Response:
    form = await request.form()
    form_fields = {key: str(value) for key, value in form.items()}
    if not twilio_service.validate_twilio_signature(str(request.url), form_fields, x_twilio_signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature")
    try:
        twilio_service.handle_inbound_sms(db, form_fields)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return Response(twilio_service.twiml_response(), media_type="application/xml")


@router.post("/api/twilio/sms/send")
def twilio_sms_send(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    conversation_id = str(payload.get("conversation_id") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not conversation_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="conversation_id is required")
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="body is required")
    try:
        result = twilio_service.send_sms_reply(
            db,
            conversation_id=conversation_id,
            body=body,
            actor_user=optional_string(payload.get("actor_user")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CRM conversation not found")
    db.commit()
    message = result["message"]
    return {
        "status": result["status"],
        "mode": result["mode"],
        "warning": result.get("warning"),
        "message": {
            "id": message.id,
            "conversation_id": message.conversation_id,
            "contact_id": message.contact_id,
            "direction": message.direction,
            "channel": message.channel,
            "body": message.body,
            "delivery_status": message.delivery_status,
            "provider_message_id": message.provider_message_id,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        },
    }


@router.get("/api/twilio/sms/status")
def twilio_sms_status() -> dict:
    return {
        "mode": "live_sms" if twilio_service.has_live_twilio_credentials() else "demo",
        "demo_mode": twilio_service.communication_demo_mode(),
        "twilio_configured": twilio_service.has_live_twilio_credentials(),
        "signature_validation": twilio_service.should_validate_signatures(),
        "phone_number": twilio_service.twilio_credentials()["phone_number"] or None,
    }


def optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
