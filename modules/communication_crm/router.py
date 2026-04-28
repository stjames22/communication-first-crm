from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db

from . import crm_service

MODULE_DIR = Path(__file__).resolve().parent
STATIC_DIR = MODULE_DIR / "static"

router = APIRouter()


@router.get("/workspace", response_class=HTMLResponse)
def crm_workspace() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@router.get("/static/{asset_name}")
def crm_static(asset_name: str) -> Response:
    allowed = {
        "app.js": "application/javascript",
        "styles.css": "text/css",
    }
    if asset_name not in allowed:
        raise HTTPException(status_code=404, detail="CRM asset not found")
    return Response((STATIC_DIR / asset_name).read_text(encoding="utf-8"), media_type=allowed[asset_name])


@router.get("/api/dashboard")
def crm_dashboard(db: Session = Depends(get_db)):
    return crm_service.dashboard(db)


@router.get("/api/contacts")
def crm_contacts(db: Session = Depends(get_db)):
    return crm_service.list_contacts(db)


@router.get("/api/contacts/{contact_id}")
def crm_contact_detail(contact_id: str, db: Session = Depends(get_db)):
    detail = crm_service.get_contact_detail(db, contact_id)
    if not detail:
        raise HTTPException(status_code=404, detail="CRM contact not found")
    return detail


@router.get("/api/contacts/{contact_id}/timeline")
def crm_contact_timeline(contact_id: str, db: Session = Depends(get_db)):
    return crm_service.list_timeline(db, contact_id)


@router.post("/api/contacts/resolve")
def crm_resolve_contact(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        result = crm_service.resolve_contact_details(
            db,
            phone=payload.get("phone"),
            name=payload.get("name"),
            email=payload.get("email"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {
        "contact": crm_service.serialize_contact(result["contact"]),
        "match_type": result["match_type"],
        "normalized": result["normalized"],
        "duplicate_warning": result["duplicate_warning"],
        "warnings": result["warnings"],
        "duplicate_candidates": result["duplicate_candidates"],
    }


@router.post("/api/contacts/{contact_id}/notes")
def crm_add_contact_note(
    contact_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    body = str(payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body is required")
    try:
        note = crm_service.add_contact_note(db, contact_id, body, actor_user=optional_string(payload.get("actor_user")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not note:
        raise HTTPException(status_code=404, detail="CRM contact not found")
    db.commit()
    return {"id": note.id, "status": "saved"}


@router.get("/api/conversations")
def crm_conversations(db: Session = Depends(get_db)):
    return crm_service.list_conversations(db)


@router.get("/api/conversations/{conversation_id}")
def crm_conversation_detail(conversation_id: str, db: Session = Depends(get_db)):
    detail = crm_service.get_conversation_detail(db, conversation_id)
    if not detail:
        raise HTTPException(status_code=404, detail="CRM conversation not found")
    db.commit()
    return detail


@router.post("/api/conversations/{conversation_id}/messages")
def crm_send_message(
    conversation_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    body = str(payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body is required")
    result = crm_service.send_message(db, conversation_id, body, actor_user=optional_string(payload.get("actor_user")))
    if not result:
        raise HTTPException(status_code=404, detail="CRM conversation not found")
    db.commit()
    return result


@router.post("/api/manual-messages")
def crm_manual_message(payload: dict = Body(...), db: Session = Depends(get_db)):
    direction = str(payload.get("direction") or "inbound").strip().lower()
    if direction not in {"inbound", "outbound"}:
        raise HTTPException(status_code=400, detail="direction must be inbound or outbound")
    if direction == "inbound":
        try:
            result = crm_service.store_inbound_message(
                db,
                phone=payload.get("phone"),
                name=payload.get("name"),
                email=payload.get("email"),
                message=str(payload.get("message") or payload.get("body") or ""),
                channel=payload.get("channel") or "manual",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.commit()
        return {
            "status": "received",
            "contact_id": result["contact"].id,
            "message_id": result["message"].id,
            "match_type": result["resolution"]["match_type"],
            "duplicate_warning": result["resolution"]["duplicate_warning"],
        }

    contact_id = str(payload.get("contact_id") or "").strip()
    if not contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required for outbound manual messages")
    try:
        message = crm_service.store_outbound_reply(db, contact_id, str(payload.get("message") or payload.get("body") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not message:
        raise HTTPException(status_code=404, detail="CRM contact not found")
    db.commit()
    return {"status": "sent", "contact_id": contact_id, "message_id": message.id}


@router.post("/api/webhooks/sms")
def crm_sms_webhook(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        result = crm_service.store_provider_inbound_message(db, payload, provider=optional_string(payload.get("provider")) or "sms")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {
        "status": "received",
        "provider": result["provider"],
        "contact_id": result["contact"].id,
        "message_id": result["message"].id,
        "match_type": result["resolution"]["match_type"],
        "duplicate_warning": result["resolution"]["duplicate_warning"],
    }


@router.post("/api/webhooks/calls")
def crm_call_webhook(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        result = crm_service.store_call_event(db, payload, provider=optional_string(payload.get("provider")) or "phone")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {
        "status": "logged",
        "contact_id": result["contact"].id,
        "call_id": result["call"].id,
        "activity_id": result["activity"].id,
        "match_type": result["resolution"]["match_type"],
        "duplicate_warning": result["resolution"]["duplicate_warning"],
    }


@router.get("/api/contacts/{contact_id}/assistant")
def crm_assistant_suggestions(contact_id: str, db: Session = Depends(get_db)):
    result = crm_service.assistant_suggestions(db, contact_id)
    if not result:
        raise HTTPException(status_code=404, detail="CRM contact not found")
    return result


@router.post("/api/contacts/{contact_id}/draft-reply")
def crm_draft_reply(contact_id: str, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    activity = crm_service.create_draft_reply(db, contact_id, actor_user=optional_string(payload.get("actor_user")))
    if not activity:
        raise HTTPException(status_code=404, detail="CRM contact not found")
    db.commit()
    return {"status": "drafted", "activity": crm_service.serialize_activity(activity)}


@router.patch("/api/review/{activity_id}")
def crm_review_activity(activity_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    activity = crm_service.update_review_activity(
        db,
        activity_id,
        status_value=str(payload.get("status") or "reviewed"),
        body=payload.get("body"),
        actor_user=optional_string(payload.get("actor_user")),
    )
    if not activity:
        raise HTTPException(status_code=404, detail="CRM review item not found")
    db.commit()
    return {"status": "updated", "activity": crm_service.serialize_activity(activity)}


@router.post("/api/review/{activity_id}/approve-send")
def crm_approve_send(activity_id: str, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    result = crm_service.approve_and_send_draft(db, activity_id, actor_user=optional_string(payload.get("actor_user")))
    if not result:
        raise HTTPException(status_code=404, detail="CRM review item not found")
    db.commit()
    return {
        "status": "sent",
        "activity": crm_service.serialize_activity(result["activity"]),
        "message_id": result["message"].id,
    }


@router.post("/api/contacts/{contact_id}/follow-ups")
def crm_assign_follow_up(contact_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        task = crm_service.assign_follow_up(
            db,
            contact_id,
            title=str(payload.get("title") or ""),
            due_at=optional_string(payload.get("due_at")),
            assigned_user=optional_string(payload.get("assigned_user")),
            priority=str(payload.get("priority") or "normal"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not task:
        raise HTTPException(status_code=404, detail="CRM contact not found")
    db.commit()
    return {"status": "assigned", "task": crm_service.serialize_task(task)}


@router.post("/api/contacts/{contact_id}/resolve")
def crm_mark_resolved(contact_id: str, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    result = crm_service.mark_contact_resolved(db, contact_id, actor_user=optional_string(payload.get("actor_user")))
    if not result:
        raise HTTPException(status_code=404, detail="CRM contact not found")
    db.commit()
    return {"status": "resolved", "contact_id": result["contact"].id, "conversations_closed": result["conversations_closed"]}


@router.get("/api/quotes")
def crm_quotes(db: Session = Depends(get_db)):
    return crm_service.list_quotes(db)


@router.get("/api/calls")
def crm_calls(db: Session = Depends(get_db)):
    return crm_service.list_calls(db)


@router.get("/api/external-links")
def crm_external_links(db: Session = Depends(get_db)):
    return crm_service.list_external_links(db)


@router.post("/api/dev/seed-demo")
def crm_seed_demo(db: Session = Depends(get_db)):
    return crm_service.seed_demo(db)


def optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
