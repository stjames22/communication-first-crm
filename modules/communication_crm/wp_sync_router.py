from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db

from . import wp_sync_service

router = APIRouter()


@router.post("/api/wp/lead")
def wordpress_lead(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        result = wp_sync_service.ingest_wordpress_lead(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return result


@router.post("/api/wp/traffic-event")
def wordpress_traffic_event(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        event = wp_sync_service.ingest_website_event(db, payload, event_type=payload.get("event_type") or "visit")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {"status": "synced", "event": event}


@router.post("/api/wp/easy-link-click")
def wordpress_easy_link_click(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        event = wp_sync_service.ingest_website_event(db, payload, event_type="easy_link_click")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {"status": "synced", "event": event}


@router.get("/api/wp/dashboard-summary")
def wordpress_dashboard_summary(db: Session = Depends(get_db)):
    return wp_sync_service.wordpress_dashboard_summary(db)
