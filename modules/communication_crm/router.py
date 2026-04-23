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
