from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal
import base64
from html import escape
import json
import logging
import os
import re
import secrets
import socket
import ssl
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, List, Optional

from fastapi import Body, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, Security, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import desc, func, inspect, or_
from pydantic import ValidationError

from .db import Base, engine, get_db
from .assumptions import (
    blower_delivery_fee,
    labor_defaults,
    material_assumption,
    normalize_blower_material,
    serialize_pricing_assumptions,
    update_job_type_assumptions,
    update_labor_defaults,
    update_measurement_defaults,
    update_material_assumptions,
    update_material_pricing_tables,
)
from .ai_estimator import estimate_job
from .ai_photo_analysis import (
    parse_handwritten_measurement_rows_from_image_url,
    parse_handwritten_measurement_rows_from_uploaded_image,
)
from .models import IntakeMedia, IntakeSubmission, Job, JobPhoto, Lead, Quote, QuoteEvent, QuoteItem, QuoteMedia, Signal, UploadedAsset
from .quote_output import build_quote_pdf, build_text_quote
from .quote_pricing import calculate_quote
from .schemas import (
    ImportResult,
    LeadCreate,
    LeadUpdate,
    LeadOut,
    QuoteCreate,
    UploadedAssetRef,
    QuoteItemInput,
    QuoteOut,
    QuotePreviewCreate,
    QuotePreviewOut,
    QuoteTextOut,
    SignalCreate,
    SignalOut,
    SignalUpdate,
)
from .settings import get_settings, refresh_settings, runtime_openai_api_key
from .storage import storage
from modules.communication_crm import create_crm_tables, crm_router
from modules.communication_crm import crm_service

settings = get_settings()
ESTIMATOR_HTML_PATH = Path(__file__).resolve().parent / "static" / "estimator.html"
CUSTOMER_UPLOAD_HTML_PATH = Path(__file__).resolve().parent / "static" / "customer_upload.html"
QUOTE_TOOL_HTML_PATH = Path(__file__).resolve().parent / "static" / "barkboys_quote_tool.html"
DEMO_HTML_PATH = Path(__file__).resolve().parent / "static" / "demo.html"
STAFF_LOGIN_HTML_PATH = Path(__file__).resolve().parent / "static" / "staff_login.html"
STAFF_CUSTOMER_UPLOADS_HTML_PATH = Path(__file__).resolve().parent / "static" / "staff_customer_uploads.html"
STAFF_CRM_HTML_PATH = Path(__file__).resolve().parent / "static" / "staff_crm.html"
ADMIN_PRICING_HTML_PATH = Path(__file__).resolve().parent / "static" / "admin_pricing.html"
SERVICE_TEMPLATES_PATH = Path(__file__).resolve().parent / "data" / "service_templates.json"
logger = logging.getLogger("growthsignal")
ESTIMATOR_SESSION_COOKIE = "barkboys_staff_session"
ESTIMATOR_SESSION_MAX_AGE = 60 * 60 * 12
ESTIMATOR_SESSIONS: set[str] = set()
OPENAI_DNS_FAILURE_MARKERS = (
    "nodename nor servname provided",
    "name or service not known",
    "temporary failure in name resolution",
    "failure in name resolution",
    "getaddrinfo failed",
    "no address associated with hostname",
)

app = FastAPI(title="Communication First CRM", version="0.1.0")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        raw_body = (await request.body()).decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - defensive logging only
        raw_body = "<unavailable>"
    detail = _format_request_validation_error(exc)
    logger.warning(
        "request_validation_failed path=%s detail=%s body=%s",
        request.url.path,
        detail,
        raw_body[:4000],
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": detail, "errors": exc.errors()},
    )


@app.on_event("startup")
def ensure_tables_on_startup() -> None:
    # Ensure schema exists even if init scripts were skipped.
    Base.metadata.create_all(bind=engine)
    create_crm_tables(engine)
    _ensure_runtime_columns()
    logger.info("startup_openai_key_present=%s", bool(os.getenv("OPENAI_API_KEY")))
    _log_runtime_storage_risks()


def _log_runtime_storage_risks() -> None:
    current_settings = refresh_settings()
    database_url = str(current_settings.database_url or "").strip()
    storage_backend = str(current_settings.storage_backend or "").strip().lower()
    uploads_root = str(storage.local_root())

    logger.info(
        "runtime_configuration storage_backend=%s uploads_root=%s database_url=%s",
        storage_backend or "local",
        uploads_root,
        database_url or "<unset>",
    )

    if storage_backend != "s3":
        logger.warning(
            "runtime_storage_risk local_upload_storage_in_use uploads_root=%s persistence_depends_on_host_disk=true",
            uploads_root,
        )

    if database_url.startswith("sqlite:///"):
        logger.warning(
            "runtime_storage_risk sqlite_database_in_use database_url=%s persistence_depends_on_host_disk=true",
            database_url,
        )


def _ensure_runtime_columns() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "jobs" in table_names:
            job_columns = {column["name"] for column in inspector.get_columns("jobs")}
            job_column_defs = {
                "email": "VARCHAR(256)",
                "zip_code": "VARCHAR(16)",
                "area_sqft": "NUMERIC(12,2)",
                "terrain_type": "VARCHAR(32)",
                "primary_job_type": "VARCHAR(64)",
                "detected_tasks_json": "TEXT",
                "sales_rep": "VARCHAR(128)",
                "follow_up_date": "DATE",
                "lead_status": "VARCHAR(32)",
                "internal_notes": "TEXT",
                "exclusions": "TEXT",
                "crew_instructions": "TEXT",
                "estimated_labor_hours": "NUMERIC(10,2)",
                "material_cost": "NUMERIC(10,2)",
                "equipment_cost": "NUMERIC(10,2)",
                "suggested_price": "NUMERIC(10,2)",
            }
            for column_name, column_type in job_column_defs.items():
                if column_name not in job_columns:
                    conn.exec_driver_sql(f"ALTER TABLE jobs ADD COLUMN {column_name} {column_type}")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS jobs_email_idx ON jobs (email)")

        if "uploaded_assets" in table_names:
            uploaded_asset_columns = {column["name"] for column in inspector.get_columns("uploaded_assets")}
            uploaded_asset_column_defs = {
                "parse_mode": "VARCHAR(32) NOT NULL DEFAULT 'auto'",
                "parse_result_json": "TEXT",
                "upload_status": "VARCHAR(32) NOT NULL DEFAULT 'uploaded'",
                "error_message": "TEXT",
            }
            for column_name, column_type in uploaded_asset_column_defs.items():
                if column_name not in uploaded_asset_columns:
                    conn.exec_driver_sql(f"ALTER TABLE uploaded_assets ADD COLUMN {column_name} {column_type}")

        if "quote_media" in table_names:
            quote_media_columns = {column["name"] for column in inspector.get_columns("quote_media")}
            quote_media_column_defs = {
                "parse_mode": "VARCHAR(32) NOT NULL DEFAULT 'auto'",
                "parse_result_json": "TEXT",
                "upload_status": "VARCHAR(32) NOT NULL DEFAULT 'uploaded'",
                "error_message": "TEXT",
            }
            for column_name, column_type in quote_media_column_defs.items():
                if column_name not in quote_media_columns:
                    conn.exec_driver_sql(f"ALTER TABLE quote_media ADD COLUMN {column_name} {column_type}")

        if "quote_items" in table_names:
            quote_item_columns = {column["name"] for column in inspector.get_columns("quote_items")}
            quote_item_column_defs = {
                "description": "TEXT",
            }
            for column_name, column_type in quote_item_column_defs.items():
                if column_name not in quote_item_columns:
                    conn.exec_driver_sql(f"ALTER TABLE quote_items ADD COLUMN {column_name} {column_type}")

        if "quotes" in table_names:
            quote_columns = {column["name"] for column in inspector.get_columns("quotes")}
            if "contact_id" not in quote_columns:
                conn.exec_driver_sql("ALTER TABLE quotes ADD COLUMN contact_id VARCHAR(36)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS quotes_contact_id_idx ON quotes (contact_id)")

        if "leads" in table_names:
            lead_columns = {column["name"] for column in inspector.get_columns("leads")}
            lead_column_defs = {
                "customer_name": "VARCHAR(128)",
                "address": "VARCHAR(256)",
                "sales_rep": "VARCHAR(128)",
                "follow_up_date": "DATE",
                "quote_amount": "NUMERIC(10,2)",
                "quote_id": "INTEGER",
                "job_notes": "TEXT",
                "status": "VARCHAR(32)",
                "created_at": "TIMESTAMP",
            }
            for column_name, column_type in lead_column_defs.items():
                if column_name not in lead_columns:
                    conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN {column_name} {column_type}")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS leads_status_idx ON leads (status)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS leads_quote_id_idx ON leads (quote_id)")


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ZIP_CODE_REGEX = re.compile(r"^\d{5}(?:-\d{4})?$")
QUOTE_PAGE_URL_RE = re.compile(r"/quotes/(\d+)")
SAFE_FILE_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
LIDAR_FILE_EXTENSIONS = {"usdz", "ply", "obj", "las", "laz", "zip"}
AI_FREQUENCIES = {"one_time", "weekly", "biweekly", "monthly"}
AI_TURF_CONDITIONS = {"healthy", "average", "overgrown"}
AI_SLOPE_OPTIONS = {"flat", "mild", "steep"}
AI_DEBRIS_LEVELS = {"low", "medium", "high"}
AI_MATERIAL_OPTIONS = {"mulch", "rock", "soil", "sand", "gravel", "compost"}
AI_TRUCK_OPTIONS = {"pickup", "single_axle", "tandem", "trailer"}
AI_PLACEMENT_OPTIONS = {"blown_in", "conveyor", "dumped"}
AI_TERRAIN_OPTIONS = {"flat", "mixed", "sloped", "hilly", "wooded"}
LEAD_STATUS_OPTIONS = {"new", "measured", "quoted", "follow_up", "won", "lost"}
BLOWER_MATERIAL_OPTIONS = {"mulch", "soil", "compost"}
BLOWER_DEFAULT_MATERIAL = "mulch"
BLOWER_DEFAULT_TRUCK_TYPE = "single_axle"
BLOWER_DEFAULT_PLACEMENT_METHOD = "blown_in"
BARKBOYS_HQ_LABEL = "BarkBoys HQ (Salem, OR 97301)"


def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
    barkboys_staff_session: Optional[str] = Cookie(default=None),
):
    if barkboys_staff_session and barkboys_staff_session in ESTIMATOR_SESSIONS:
        return

    if settings.api_key and x_api_key and secrets.compare_digest(x_api_key, settings.api_key):
        return

    if not settings.api_key and not settings.estimator_user and not settings.estimator_password:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid API credentials required",
    )


def require_estimator_access(
    barkboys_staff_session: Optional[str] = Cookie(default=None),
):
    if barkboys_staff_session and barkboys_staff_session in ESTIMATOR_SESSIONS:
        return

    if not settings.estimator_user and not settings.estimator_password:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Estimator authentication required",
    )


app.include_router(crm_router, prefix="/crm", dependencies=[Depends(require_api_key)])


@app.post("/api/inbound")
def communication_crm_inbound(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    try:
        result = crm_service.store_inbound_message(
            db,
            phone=str(payload.get("phone") or ""),
            message=str(payload.get("message") or ""),
            name=payload.get("name"),
            email=payload.get("email"),
            channel=payload.get("channel"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return {
        "status": "received",
        "contact_id": result["contact"].id,
        "message_id": result["message"].id,
    }


@app.post("/api/inbound-message")
def communication_crm_inbound_message(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    try:
        result = crm_service.store_inbound_message(
            db,
            phone=payload.get("phone"),
            name=payload.get("name"),
            email=payload.get("email"),
            message=str(payload.get("message") or ""),
            channel=payload.get("channel"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return {
        "status": "received",
        "contact_id": result["contact"].id,
        "message_id": result["message"].id,
    }


@app.get("/api/conversations/recent")
def communication_crm_recent_conversations(db: Session = Depends(get_db)) -> list[dict]:
    return crm_service.list_conversations(db)


@app.get("/api/conversations/{contact_id}")
def communication_crm_contact_conversations(contact_id: str, db: Session = Depends(get_db)) -> list[dict]:
    messages = crm_service.list_contact_messages(db, contact_id)
    if messages is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return messages


@app.get("/api/contacts/{contact_id}/timeline")
def communication_crm_contact_timeline(contact_id: str, db: Session = Depends(get_db)) -> list[dict]:
    if not crm_service.get_contact_detail(db, contact_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return crm_service.list_timeline(db, contact_id)


@app.post("/api/contacts/{contact_id}/start-quote")
def communication_crm_start_quote(contact_id: str, db: Session = Depends(get_db)) -> dict:
    result = crm_service.start_quote_from_contact(db, contact_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    db.commit()
    return result


@app.post("/api/reply")
def communication_crm_reply(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    contact_id = str(payload.get("contact_id") or "").strip()
    if not contact_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="contact_id is required")
    try:
        message = crm_service.store_outbound_reply(
            db,
            contact_id=contact_id,
            message=str(payload.get("message") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    db.commit()
    return {"status": "sent", "message_id": message.id}


def _staff_session_required_redirect(barkboys_staff_session: Optional[str] = None) -> Optional[RedirectResponse]:
    if barkboys_staff_session and barkboys_staff_session in ESTIMATOR_SESSIONS:
        return None
    if not settings.estimator_user and not settings.estimator_password:
        return None
    return RedirectResponse(url="/staff-login", status_code=status.HTTP_303_SEE_OTHER)


def _payload_to_dict(payload: Any) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    if hasattr(payload, "dict"):
        return payload.dict()
    return dict(payload)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", [])) or "field"
        msg = err.get("msg", "Invalid value")
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts)


def _format_request_validation_error(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", [])) or "field"
        msg = err.get("msg", "Invalid value")
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts) or "Request validation failed"


def _validated_fetchable_image_url(value: object) -> str:
    image_url = str(value or "").strip()
    if not image_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="imageUrl is required")

    parsed = urllib.parse.urlparse(image_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="imageUrl must be a signed or publicly fetchable http/https URL",
        )
    return image_url


def _handwritten_parse_error_status(reason_code: str) -> int:
    if reason_code == "openai_not_configured":
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_502_BAD_GATEWAY


def _handwritten_parse_error_message(reason_code: str) -> str:
    code = str(reason_code or "").strip().lower()
    if code == "openai_not_configured":
        return "OpenAI measurement parsing is not configured. Set OPENAI_API_KEY in the Railway service."
    if code == "openai_auth_failed":
        return "OpenAI rejected the measurement parsing request. Check the OPENAI_API_KEY configured in Railway."
    if code == "openai_model_unavailable":
        return "The configured OpenAI vision model is unavailable. Check GS_OPENAI_VISION_MODEL in Railway."
    if code == "openai_rate_limited":
        return "OpenAI rate limited measurement parsing. Retry in a moment."
    if code == "openai_dns_failed":
        return "The production app cannot resolve api.openai.com. Check Railway egress DNS/network configuration."
    if code == "openai_tls_failed":
        return "The production app reached OpenAI but TLS validation failed. Check GS_OPENAI_CA_BUNDLE or network SSL interception."
    if code == "openai_connection_refused":
        return "The production app could not connect to OpenAI. The connection was refused."
    if code == "openai_connection_reset":
        return "The production app lost the OpenAI connection during measurement parsing."
    if code == "openai_request_timed_out":
        return "OpenAI measurement parsing timed out. Retry in a moment."
    if code == "openai_request_failed":
        return "The production app could not reach OpenAI for measurement parsing."
    if code == "openai_sdk_not_installed":
        return "The OpenAI Python SDK is not installed in the deployment."
    if code == "openai_ca_bundle_invalid":
        return "GS_OPENAI_CA_BUNDLE is set but invalid on the production app."
    if code == "openai_missing_json":
        return "OpenAI returned an invalid measurement payload."
    if code == "openai_missing_rows":
        return "OpenAI responded, but no measurement rows were returned."
    if code == "openai_invalid_response":
        return "OpenAI returned an unexpected measurement response."
    if code == "openai_missing_measurement_lines":
        return "OpenAI returned a response, but it did not include measurement lines."
    return f"Handwritten measurement parsing failed ({code or 'unknown_error'})."


def _load_service_templates() -> list[dict]:
    if not SERVICE_TEMPLATES_PATH.exists():
        return []
    try:
        data = json.loads(SERVICE_TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _render_static_page(path: Path, missing_detail: str, replacements: Optional[dict[str, str]] = None) -> str:
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=missing_detail)
    html = path.read_text(encoding="utf-8")
    for key, value in (replacements or {}).items():
        html = html.replace(f"{{{{{key}}}}}", escape(value))
    return html


def _build_stamp_for_path(path: Path) -> str:
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        modified_at = datetime.utcnow()
    return f"Build {modified_at.strftime('%Y-%m-%d %-I:%M %p')}"


def _ui_page_build_info(path: Path, route: str) -> dict:
    exists = path.exists()
    info = {
        "route": route,
        "path": str(path),
        "exists": exists,
        "build_stamp": _build_stamp_for_path(path),
    }
    if exists:
        try:
            modified_at = datetime.utcfromtimestamp(path.stat().st_mtime)
            info["last_modified_utc"] = modified_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            info["last_modified_utc"] = None
    else:
        info["last_modified_utc"] = None
    return info


def _track_quote_event(db: Session, event_name: str, quote_id: Optional[int], metadata: dict):
    event = QuoteEvent(
        event_name=event_name,
        quote_id=quote_id,
        metadata_json=json.dumps(metadata, default=str),
    )
    try:
        db.add(event)
        db.commit()
        logger.info("quote_event=%s quote_id=%s metadata=%s", event_name, quote_id, metadata)
    except Exception as exc:
        db.rollback()
        logger.warning("quote_event_write_failed event=%s error=%s", event_name, exc)


def _estimate_ai_preview_confidence(
    *,
    photo_count: int,
    covered_angle_count: int,
    lidar_count: int,
    capture_device: str,
    dimension_area: Decimal,
    combined_bed_area: Decimal,
    lot_size_present: bool,
    edge_length_present: bool,
    bed_group_count: int,
    measurement_entry_count: int,
    trusted_measurements_available: bool,
    openai_used: bool,
) -> float:
    base_confidence = Decimal("0.55")
    if photo_count >= 4:
        base_confidence += Decimal("0.15")
    if covered_angle_count >= 3:
        base_confidence += Decimal("0.10")
    if lidar_count > 0:
        base_confidence += Decimal("0.12")
    if capture_device == "iphone_lidar" and lidar_count > 0:
        base_confidence += Decimal("0.08")
    if dimension_area > 0:
        base_confidence += Decimal("0.14")
    if combined_bed_area > 0:
        base_confidence += Decimal("0.10")
    if measurement_entry_count >= 2:
        base_confidence += Decimal("0.06")
    if measurement_entry_count >= 5:
        base_confidence += Decimal("0.04")
    if trusted_measurements_available:
        base_confidence += Decimal("0.06")
    if bed_group_count >= 3:
        base_confidence += Decimal("0.05")
    if bed_group_count >= 8:
        base_confidence += Decimal("0.05")
    if bed_group_count >= 12:
        base_confidence += Decimal("0.05")
    if openai_used:
        base_confidence += Decimal("0.04")
    if lot_size_present:
        base_confidence += Decimal("0.05")
    if edge_length_present:
        base_confidence += Decimal("0.03")
    if combined_bed_area > 0 and bed_group_count >= 3:
        base_confidence = max(base_confidence, Decimal("0.72"))
    if combined_bed_area > 0 and bed_group_count >= 8:
        base_confidence = max(base_confidence, Decimal("0.80"))
    if combined_bed_area > 0 and bed_group_count >= 12:
        base_confidence = max(base_confidence, Decimal("0.85"))
    if trusted_measurements_available and measurement_entry_count >= 5:
        base_confidence = max(base_confidence, Decimal("0.88"))
    return float(min(base_confidence, Decimal("0.95")))


def _safe_upload_name(filename: str) -> str:
    base = Path(filename or "upload.bin").name
    clean = SAFE_FILE_RE.sub("_", base).strip("._")
    return clean or "upload.bin"


def _upload_asset_url(asset_id: int) -> str:
    return f"/api/uploads/assets/{asset_id}/content"


def _quote_media_category(media_kind: Optional[str]) -> str:
    normalized = str(media_kind or "").strip().lower()
    if normalized == "measurement_note":
        return "measurement_note"
    if normalized == "exclusion_photo":
        return "exclusion"
    return "site_media"


def _classify_upload_media_kind(filename: str, content_type: str, category: str) -> str:
    normalized_category = str(category or "site_media").strip().lower()
    if normalized_category == "measurement_note":
        return "measurement_note"
    if normalized_category == "exclusion":
        return "exclusion_photo"
    lowered_type = str(content_type or "").strip().lower()
    if lowered_type.startswith("image/"):
        return "photo"
    ext = Path(str(filename or "")).suffix.lower().lstrip(".")
    if ext in LIDAR_FILE_EXTENSIONS:
        return "lidar_scan"
    return "photo"


def _serialize_uploaded_asset(asset: UploadedAsset) -> dict[str, object]:
    return {
        "id": asset.id,
        "url": _upload_asset_url(asset.id),
        "storageKey": asset.storage_path,
        "filename": asset.file_name,
        "mimeType": asset.content_type,
        "size": asset.file_size,
        "category": asset.category,
        "createdAt": asset.created_at,
        "media_kind": asset.media_kind,
        "parseMode": _normalize_parse_mode(getattr(asset, "parse_mode", "auto")),
        "parserResult": _safe_json_loads(getattr(asset, "parse_result_json", None)),
        "status": _normalize_upload_status(getattr(asset, "upload_status", "uploaded")),
        "error": getattr(asset, "error_message", None),
    }


def _serialize_quote_media_asset(media: QuoteMedia) -> dict[str, object]:
    return {
        "id": media.id,
        "url": "",
        "storageKey": media.storage_path,
        "filename": media.file_name,
        "mimeType": media.content_type,
        "size": media.file_size,
        "category": _quote_media_category(media.media_kind),
        "createdAt": media.created_at,
        "media_kind": media.media_kind,
        "parseMode": _normalize_parse_mode(getattr(media, "parse_mode", "auto")),
        "parserResult": _safe_json_loads(getattr(media, "parse_result_json", None)),
        "status": _normalize_upload_status(getattr(media, "upload_status", "uploaded")),
        "error": getattr(media, "error_message", None),
        "capture_device": media.capture_device,
    }


def _serialize_job_photo_asset(photo: JobPhoto) -> dict[str, object]:
    return {
        "id": photo.id,
        "url": "",
        "storageKey": photo.storage_path,
        "filename": photo.file_name,
        "mimeType": photo.content_type,
        "size": photo.file_size,
        "category": "site_media",
        "createdAt": photo.created_at,
        "media_kind": "photo",
        "parseMode": "auto",
        "parserResult": None,
        "status": "ready",
        "error": None,
    }


def _measurement_note_parser_snapshot(asset: UploadedAsset, rows: list[dict]) -> dict[str, object]:
    normalized_rows = rows if isinstance(rows, list) else []
    source_asset_id = str(asset.id)
    source_filename = str(asset.file_name or "").strip()
    measurement_entries: list[dict[str, object]] = []

    for row in normalized_rows:
        if not isinstance(row, dict):
            continue
        try:
            length = float(row.get("length") or 0)
            width = float(row.get("width") or 0)
        except (TypeError, ValueError):
            continue
        if length <= 0 or width <= 0:
            continue
        raw_text = str(row.get("raw") or f"{length:g}x{width:g}").strip() or f"{length:g}x{width:g}"
        area_sqft = round(length * width, 2)
        volume_cu_yd = round((area_sqft * (2 / 12)) / 27, 2)
        measurement_entries.append(
            {
                "entry_type": "dimension_pair",
                "include": True,
                "raw_text": raw_text,
                "original_raw_text": raw_text,
                "length_ft": length,
                "width_ft": width,
                "estimated_area_sqft": area_sqft,
                "estimated_material_yards": volume_cu_yd,
                "confidence": 0.98,
                "source_images": [source_filename] if source_filename else [],
                "source_asset_id": source_asset_id,
                "source_filename": source_filename,
                "source_type": "measurement-note",
                "notes": "",
                "needs_review": True,
                "inferred_from_photo_estimate": False,
                "inference_basis": "",
            }
        )

    return {
        "rows": normalized_rows,
        "measurement_entries": measurement_entries,
        "source_type": "measurement-note",
        "source_asset_id": source_asset_id,
        "source_filename": source_filename,
    }


def _uploaded_asset_analysis_record(asset: UploadedAsset) -> dict[str, object]:
    return {
        "id": asset.id,
        "file_name": asset.file_name,
        "content_type": asset.content_type,
        "storage_path": asset.storage_path,
        "category": asset.category,
        "media_kind": asset.media_kind,
        "parse_mode": _normalize_parse_mode(getattr(asset, "parse_mode", "auto")),
        "status": _normalize_upload_status(getattr(asset, "upload_status", "uploaded")),
    }


def _measurement_lines_from_entries(entries: list[dict]) -> list[str]:
    lines: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("entry_type") or "").strip().lower() != "dimension_pair":
            continue
        raw_text = str(entry.get("raw_text") or "").strip()
        if raw_text:
            lines.append(raw_text.replace(" ", ""))
            continue
        try:
            length = float(entry.get("length_ft") or 0)
            width = float(entry.get("width_ft") or 0)
        except (TypeError, ValueError):
            continue
        if length > 0 and width > 0:
            lines.append(f"{length:g}x{width:g}")
    return lines


def _dimension_rows_from_measurement_entries(entries: list[dict]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("entry_type") or "").strip().lower() != "dimension_pair":
            continue
        try:
            length = float(entry.get("length_ft") or 0)
            width = float(entry.get("width_ft") or 0)
        except (TypeError, ValueError):
            continue
        if length <= 0 or width <= 0:
            continue
        rows.append(
            {
                "raw": str(entry.get("raw_text") or f"{length:g}x{width:g}").strip() or f"{length:g}x{width:g}",
                "length": length,
                "width": width,
            }
        )
    return rows


def _ensure_measurement_entry_sources(
    entries: list[dict],
    assets: list[UploadedAsset],
) -> list[dict]:
    normalized: list[dict] = []
    asset_by_name = {
        str(asset.file_name or "").strip(): asset
        for asset in assets
        if str(asset.file_name or "").strip()
    }
    single_asset = assets[0] if len(assets) == 1 else None

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        current = dict(entry)
        source_asset_id = str(current.get("source_asset_id") or "").strip()
        source_filename = str(current.get("source_filename") or "").strip()
        if not source_asset_id or not source_filename:
            candidate_names = [
                str(name or "").strip()
                for name in list(current.get("source_images") or [])
                if str(name or "").strip()
            ]
            matched_asset = None
            for candidate_name in candidate_names:
                matched_asset = asset_by_name.get(candidate_name)
                if matched_asset is not None:
                    break
            if matched_asset is None:
                matched_asset = single_asset
            if matched_asset is not None:
                if not str(current.get("source_asset_id") or "").strip():
                    current["source_asset_id"] = str(matched_asset.id)
                if not str(current.get("source_filename") or "").strip():
                    current["source_filename"] = str(matched_asset.file_name or "").strip()
                if not str(current.get("source_type") or "").strip():
                    current["source_type"] = (
                        "measurement-note" if str(matched_asset.category or "").strip().lower() == "measurement_note" else "site-media"
                    )
        normalized.append(current)
    return normalized


def _dimensions_response_payload(
    *,
    assets: list[UploadedAsset],
    measurement_entries: list[dict],
    measurement_parse: dict,
    extraction_meta: dict,
    message: str,
    ok: bool = True,
    error: str = "",
    error_type: str = "",
    details: str = "",
) -> dict[str, object]:
    measurement_lines = _measurement_lines_from_entries(measurement_entries)
    rows = _dimension_rows_from_measurement_entries(measurement_entries)
    return {
        "ok": ok,
        "error": error,
        "error_type": error_type,
        "details": details,
        "assets": [_serialize_uploaded_asset(asset) for asset in assets],
        "rows": rows,
        "measurement_entries": measurement_entries,
        "measurements": measurement_lines,
        "measurementsText": "\n".join(measurement_lines),
        "measurement_parse": measurement_parse or {},
        "extraction_meta": extraction_meta or {},
        "message": message,
    }


def _dimensions_failure_fields(
    *,
    measurement_entries: list[dict],
    measurement_parse: dict,
    extraction_meta: dict,
    message: str,
) -> dict[str, str | bool]:
    if measurement_entries:
        return {"ok": True, "error": "", "error_type": "", "details": ""}

    parse_error = str((extraction_meta.get("parse_error") or "")).strip()
    parse_error_type = str((extraction_meta.get("error_type") or "")).strip()
    parse_details = str((extraction_meta.get("details") or "")).strip()
    if parse_error:
        return {
            "ok": False,
            "error": parse_error,
            "error_type": parse_error_type or "measurement_parse_exception",
            "details": parse_details,
        }

    openai_error = str((extraction_meta.get("openai_error") or "")).strip().lower()
    openai_error_message = str((extraction_meta.get("openai_error_message") or "")).strip()
    openai_error_type = str((extraction_meta.get("openai_error_type") or "")).strip()
    openai_error_details = str((extraction_meta.get("openai_error_details") or "")).strip()
    if openai_error:
        return {
            "ok": False,
            "error": openai_error_message or _handwritten_parse_error_message(openai_error),
            "error_type": openai_error_type or openai_error,
            "details": openai_error_details or str(message or "").strip(),
        }

    parse_classification = str((measurement_parse.get("classification") or "")).strip().lower()
    if parse_classification:
        return {
            "ok": False,
            "error": str(message or "").strip() or "Measurement parsing failed.",
            "error_type": parse_classification,
            "details": "",
        }

    if str(message or "").strip():
        return {
            "ok": False,
            "error": str(message or "").strip(),
            "error_type": "no_measurements_detected",
            "details": "",
        }

    return {
        "ok": False,
        "error": "Measurement parsing failed.",
        "error_type": "unknown_error",
        "details": "",
    }


def _dimension_upload_message(
    *,
    measurement_entries: list[dict],
    measurement_parse: dict,
    extraction_meta: dict,
    asset_name: str,
) -> str:
    measurement_lines = _measurement_lines_from_entries(measurement_entries)
    if measurement_lines:
        return f"Measurements detected from {asset_name or 'uploaded image'}."

    parse_classification = str((measurement_parse.get("classification") or "")).strip().lower()
    openai_error = str((extraction_meta.get("openai_error") or "")).strip().lower()
    openai_configured = extraction_meta.get("openai_configured") is True
    fallback_used = extraction_meta.get("fallback_ocr_used") is True

    if openai_error == "openai_dns_failed":
        return (
            "Measurement parsing is unavailable right now because this Mac cannot resolve api.openai.com. "
            "The image uploaded successfully. Check Wi-Fi, DNS, VPN, or network filtering, then retry or enter rows manually."
        )
    if openai_error:
        return (
            f"Measurement parsing is unavailable right now ({openai_error}). "
            "The image uploaded successfully. Retry after fixing the OpenAI connection or enter rows manually."
        )
    if parse_classification == "failed_ocr_unreadable_note":
        return "Could not read clear measurements from this image. Try a clearer image or enter rows manually."
    if openai_configured and not fallback_used:
        return (
            "No measurements were detected, and fallback OCR is currently disabled on this machine. "
            "Try a clearer image or enter rows manually."
        )
    return "No measurements detected. Try a clearer image or enter rows manually."


def _normalize_parse_mode(value: object) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized in {"auto", "force_measurement_note", "force_scene_photo"}:
        return normalized
    return "auto"


def _normalize_site_media_parse_mode(value: object) -> str:
    return _normalize_parse_mode(value)


def _normalize_media_category(value: object, default: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized in {"site_media", "measurement_note", "exclusion"}:
        return normalized
    return default


def _normalize_upload_status(value: object, default: str = "uploaded") -> str:
    normalized = str(value or default).strip().lower()
    if normalized in {"queued", "uploading", "uploaded", "parsing", "ready", "error"}:
        return normalized
    return default


def _safe_json_loads(value: object) -> Optional[dict]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _to_bool(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_decimal_or_default(value: Optional[str], default: Decimal) -> Decimal:
    if value is None or str(value).strip() == "":
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _normalize_choice(value: Optional[str], allowed: set[str], default: str) -> str:
    candidate = (value or "").strip().lower()
    if candidate in allowed:
        return candidate
    return default


def _normalize_blower_material(value: Optional[str]) -> str:
    return normalize_blower_material(str(value or BLOWER_DEFAULT_MATERIAL))


def _normalize_lead_status(value: Optional[str], default: str = "new") -> str:
    candidate = (value or "").strip().lower()
    if candidate == "contacted":
        candidate = "follow_up"
    return _normalize_choice(candidate, LEAD_STATUS_OPTIONS, default)


def _extract_quote_id_from_page_url(page_url: Optional[str]) -> Optional[int]:
    if not page_url:
        return None
    match = QUOTE_PAGE_URL_RE.search(str(page_url))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _linked_quote_id_for_lead(lead: Lead) -> Optional[int]:
    quote_id = getattr(lead, "quote_id", None)
    if quote_id:
        try:
            return int(quote_id)
        except (TypeError, ValueError):
            return None
    return _extract_quote_id_from_page_url(getattr(lead, "page_url", None))


def _extract_zip_code(address: str, fallback: Optional[str] = None) -> str:
    source = " ".join(part for part in [address or "", fallback or ""] if part).strip()
    match = re.search(r"\b(\d{5}(?:-\d{4})?)\b", source)
    return match.group(1) if match else ""


def _zip_prefix(zip_code: Optional[str]) -> str:
    match = re.match(r"^(\d{5})", str(zip_code or "").strip())
    return match.group(1) if match else ""


def _normalize_zip_code(value: object, *, address: str = "") -> str:
    trimmed = str(value or "").strip()
    if trimmed:
        if not ZIP_CODE_REGEX.match(trimmed):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ZIP Code must be 12345 or 12345-6789")
        return trimmed
    return _extract_zip_code(address)


def _infer_photo_tags(files: List[dict]) -> set[str]:
    tags = set()
    for file in files:
        name = str(file.get("file_name") or "").lower()
        for token in ("front", "back", "left", "right", "gate", "slope", "obstacle", "driveway"):
            if token in name:
                tags.add(token)
    return tags


def _infer_photo_tags_from_names(file_names: List[str]) -> set[str]:
    tags = set()
    for file_name in file_names:
        name = (file_name or "").lower()
        for token in ("front", "back", "left", "right", "gate", "slope", "obstacle", "driveway"):
            if token in name:
                tags.add(token)
    return tags


def _task_label(job_type: str) -> str:
    labels = {
        "flower_bed_refresh": "Flower Bed Refresh",
        "topsoil_install": "Topsoil Install",
        "mulch_refresh": "Mulch Refresh",
        "compost_refresh": "Compost Refresh",
        "leaf_cleanup": "Leaf Cleanup",
        "hedge_trim": "Hedge Trim",
        "brush_removal": "Brush Removal",
        "general_cleanup": "General Cleanup",
    }
    return labels.get(str(job_type or "").strip().lower(), "General Cleanup")


def _normalize_detected_tasks(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _build_detected_task_items(task_breakdown: list[dict]) -> list[QuoteItemInput]:
    items = []
    for row in task_breakdown or []:
        price = _to_decimal_or_default(str(row.get("suggested_price") or "0"), Decimal("0"))
        items.append(
            QuoteItemInput(
                name=_task_label(str(row.get("job_type") or "general_cleanup")),
                quantity=Decimal("1"),
                unit="service",
                base_price=price,
                per_unit_price=Decimal("0"),
                min_charge=price,
            )
        )
    if items:
        return items
    fallback_price = Decimal("150")
    return [
        QuoteItemInput(
            name="General Cleanup",
            quantity=Decimal("1"),
            unit="service",
            base_price=fallback_price,
            per_unit_price=Decimal("0"),
            min_charge=fallback_price,
        )
    ]


def _compose_lead_notes(job: Job) -> str:
    parts = []
    if getattr(job, "sales_rep", None):
        parts.append(f"Sales rep: {job.sales_rep}")
    if getattr(job, "follow_up_date", None):
        parts.append(f"Follow-up date: {job.follow_up_date}")
    if getattr(job, "primary_job_type", None):
        parts.append(f"Primary job type: {job.primary_job_type}")
    detected_tasks = _normalize_detected_tasks(getattr(job, "detected_tasks_json", None))
    if detected_tasks:
        parts.append(
            "Detected tasks: "
            + ", ".join(_task_label(str(item.get("job_type") or "general_cleanup")) for item in detected_tasks)
        )
    if job.notes:
        parts.append(f"Job notes: {job.notes}")
    if getattr(job, "internal_notes", None):
        parts.append(f"Internal notes (BarkBoys only): {job.internal_notes}")
    if getattr(job, "exclusions", None):
        parts.append(f"Exclusions: {job.exclusions}")
    if getattr(job, "crew_instructions", None):
        parts.append(f"Crew instructions: {job.crew_instructions}")
    return "\n".join(parts)


def _estimate_distance_miles_from_address(address: str) -> Decimal:
    text = (address or "").strip().lower()
    zip_code = _zip_prefix(_extract_zip_code(text))
    if zip_code:
        if zip_code.startswith("973"):
            return Decimal("8")
        if zip_code.startswith("970"):
            return Decimal("22")
        if zip_code.startswith("972"):
            return Decimal("35")
        if zip_code.startswith("971"):
            return Decimal("40")
        if zip_code.startswith("974"):
            return Decimal("55")
        return Decimal("65")

    if "salem" in text:
        return Decimal("8")
    if "keizer" in text:
        return Decimal("12")
    if "portland" in text:
        return Decimal("38")
    if "eugene" in text:
        return Decimal("62")
    return Decimal("28")


def _build_demo_ai_items(
    lot_size: Decimal,
    edge_length: Decimal,
    turf_condition: str,
    slope: str,
    debris_level: str,
    obstacle_count: int,
    has_gates_bool: bool,
    include_haulaway_bool: bool,
    include_blowing_bool: bool,
    material_type: str = BLOWER_DEFAULT_MATERIAL,
    customer_address: str = "",
) -> list[QuoteItemInput]:
    condition_multiplier = {
        "healthy": Decimal("0.90"),
        "average": Decimal("1.00"),
        "overgrown": Decimal("1.25"),
    }[turf_condition]
    slope_multiplier = {
        "flat": Decimal("1.00"),
        "mild": Decimal("1.08"),
        "steep": Decimal("1.18"),
    }[slope]
    debris_multiplier = {
        "low": Decimal("0.00"),
        "medium": Decimal("0.30"),
        "high": Decimal("0.65"),
    }[debris_level]

    material = material_assumption(material_type)
    material_type = material["material_type"]
    blower_defaults = labor_defaults()
    blower_placement_rate = blower_defaults["blower_placement_rate_per_yard"]
    miles = _estimate_distance_miles_from_address(customer_address)
    distance_delivery_fee = blower_delivery_fee(miles)

    mowing_per_unit = (Decimal("0.0100") * condition_multiplier * slope_multiplier).quantize(Decimal("0.0001"))
    edging_per_unit = (Decimal("0.1100") * condition_multiplier).quantize(Decimal("0.0001"))
    estimated_material_yards = max(Decimal("3"), (lot_size / Decimal("1200")).quantize(Decimal("0.01")))

    ai_items = [
        QuoteItemInput(
            name="Mowing",
            quantity=lot_size,
            unit="sq ft",
            base_price=Decimal("25"),
            per_unit_price=mowing_per_unit,
            min_charge=Decimal("55"),
        ),
        QuoteItemInput(
            name="Edging",
            quantity=edge_length,
            unit="linear ft",
            base_price=Decimal("10"),
            per_unit_price=edging_per_unit,
            min_charge=Decimal("35"),
        ),
        QuoteItemInput(
            name=f"{material_type.title()} Material",
            quantity=estimated_material_yards,
            unit="yard",
            base_price=Decimal("0"),
            per_unit_price=material["cost_per_yard"],
            min_charge=Decimal("0"),
        ),
        QuoteItemInput(
            name="Blower Placement",
            quantity=estimated_material_yards,
            unit="yard",
            base_price=Decimal("0"),
            per_unit_price=blower_placement_rate,
            min_charge=Decimal("0"),
        ),
        QuoteItemInput(
            name="Blower Delivery",
            quantity=estimated_material_yards,
            unit="yard",
            base_price=distance_delivery_fee,
            per_unit_price=material["delivery_fee_per_yard"],
            min_charge=distance_delivery_fee,
        ),
    ]

    if include_blowing_bool:
        ai_items.append(
            QuoteItemInput(
                name="Blowing",
                quantity=Decimal("1"),
                unit="service",
                base_price=blower_defaults["blowing_service_fee"],
                per_unit_price=Decimal("0"),
                min_charge=blower_defaults["blowing_service_fee"],
            )
        )

    if obstacle_count > 0:
        ai_items.append(
            QuoteItemInput(
                name="Obstacle Handling",
                quantity=Decimal(obstacle_count),
                unit="obstacle",
                base_price=Decimal("0"),
                per_unit_price=blower_defaults["obstacle_fee_per_obstacle"],
                min_charge=Decimal("0"),
            )
        )

    if has_gates_bool:
        ai_items.append(
            QuoteItemInput(
                name="Gate Access Handling",
                quantity=Decimal("1"),
                unit="service",
                base_price=blower_defaults["gate_access_fee"],
                per_unit_price=Decimal("0"),
                min_charge=blower_defaults["gate_access_fee"],
            )
        )

    if debris_level != "low":
        ai_items.append(
            QuoteItemInput(
                name="Debris Cleanup",
                quantity=Decimal("1"),
                unit="service",
                base_price=(Decimal("20") + (Decimal("25") * debris_multiplier)).quantize(Decimal("0.01")),
                per_unit_price=Decimal("0"),
                min_charge=Decimal("20"),
            )
        )

    if include_haulaway_bool:
        ai_items.append(
            QuoteItemInput(
                name="Green Waste Haul Away",
                quantity=Decimal("1"),
                unit="load",
                base_price=blower_defaults["haul_away_base_fee"],
                per_unit_price=Decimal("0"),
                min_charge=blower_defaults["haul_away_base_fee"],
            )
        )

    return ai_items


def _decode_payload_files(files: List[dict], media_kind: str) -> list[dict]:
    decoded = []
    for file in files:
        name = _safe_upload_name(str(file.get("file_name") or "upload.bin"))
        content_type = str(file.get("content_type") or "application/octet-stream")
        encoded = str(file.get("data_base64") or "")
        if "," in encoded and encoded.lower().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        if not encoded:
            continue
        try:
            raw = base64.b64decode(encoded, validate=False)
        except Exception:
            continue
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File too large: {name}",
            )
        decoded.append(
            {
                "file_name": name,
                "content_type": content_type,
                "file_size": len(raw),
                "media_kind": media_kind[:32],
                "raw": raw,
            }
        )
    return decoded


def _save_upload_group(
    files: List[dict],
    destination_prefix: str,
    media_kind: str,
) -> list[dict]:
    stored = []
    for file in _decode_payload_files(files, media_kind):
        raw = file["raw"]
        safe_name = file["file_name"]
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        final_name = f"{stamp}_{safe_name}"
        storage_path = storage.save_bytes(
            f"{destination_prefix}/{final_name}",
            raw,
            content_type=file["content_type"],
        )
        stored.append(
            {
                "file_name": safe_name,
                "content_type": file["content_type"],
                "file_size": len(raw),
                "media_kind": media_kind[:32],
                "storage_path": storage_path,
            }
        )
    return stored


async def _store_uploaded_assets(
    *,
    files: List[UploadFile],
    draft_token: str,
    category: str,
    parse_mode: str = "auto",
    db: Session,
) -> list[UploadedAsset]:
    saved_assets: list[UploadedAsset] = []
    normalized_category = _normalize_media_category(category, "site_media")
    normalized_parse_mode = _normalize_parse_mode(parse_mode)
    if normalized_category == "measurement_note":
        normalized_parse_mode = "force_measurement_note"
    elif normalized_category == "site_media":
        normalized_parse_mode = _normalize_site_media_parse_mode(normalized_parse_mode)
    elif normalized_category == "exclusion":
        normalized_parse_mode = "auto"
    safe_draft = SAFE_FILE_RE.sub("_", str(draft_token or "").strip()).strip("._")
    if not safe_draft:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Draft token is required")

    for upload in files or []:
        safe_name = _safe_upload_name(upload.filename or "upload.bin")
        content_type = str(upload.content_type or "application/octet-stream")
        logger.info(
            "upload_started draft=%s category=%s filename=%s content_type=%s parse_mode=%s",
            safe_draft,
            normalized_category,
            safe_name,
            content_type,
            normalized_parse_mode,
        )
        raw = await upload.read()
        if not raw:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Uploaded file is empty: {safe_name}")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File too large: {safe_name}",
            )
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        final_name = f"{stamp}_{safe_name}"
        storage_path = storage.save_bytes(
            f"draft-uploads/{safe_draft}/{normalized_category}/{final_name}",
            raw,
            content_type=content_type,
        )
        asset = UploadedAsset(
            draft_token=safe_draft,
            file_name=safe_name,
            content_type=content_type,
            file_size=len(raw),
            category=normalized_category,
            media_kind=_classify_upload_media_kind(safe_name, content_type, normalized_category),
            parse_mode=normalized_parse_mode,
            upload_status="uploaded",
            storage_path=storage_path,
        )
        db.add(asset)
        db.flush()
        saved_assets.append(asset)
        logger.info(
            "upload_completed asset_id=%s url=%s category=%s filename=%s size=%s draft=%s parse_mode=%s",
            asset.id,
            _upload_asset_url(asset.id),
            asset.category,
            asset.file_name,
            asset.file_size,
            safe_draft,
            asset.parse_mode,
        )

    db.commit()
    return saved_assets


def _uploaded_asset_refs(records: list[object], db: Session) -> list[dict]:
    refs: list[dict] = []
    ids: list[int] = []
    for item in records or []:
        if isinstance(item, UploadedAssetRef):
            data = item.model_dump(by_alias=True) if hasattr(item, "model_dump") else item.dict(by_alias=True)
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue
        try:
            asset_id = int(data.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if asset_id <= 0:
            continue
        ids.append(asset_id)
        refs.append(
            {
                "id": asset_id,
                "category": _normalize_media_category(data.get("category"), "site_media"),
                "parse_mode": (
                    _normalize_parse_mode(data.get("parseMode") or data.get("parse_mode"))
                    if (data.get("parseMode") is not None or data.get("parse_mode") is not None)
                    else None
                ),
                "parser_result": data.get("parserResult") if isinstance(data.get("parserResult"), dict) else None,
                "status": _normalize_upload_status(data.get("status"), "uploaded") if data.get("status") is not None else None,
                "error": str(data.get("error") or "").strip() or None,
            }
        )

    if not ids:
        return []

    asset_rows = db.query(UploadedAsset).filter(UploadedAsset.id.in_(ids)).all()
    asset_map = {row.id: row for row in asset_rows}
    normalized: list[dict] = []
    for ref in refs:
        row = asset_map.get(ref["id"])
        if row is None:
            continue
        normalized.append(
            {
                "id": row.id,
                "file_name": row.file_name,
                "content_type": row.content_type,
                "storage_path": row.storage_path,
                "category": ref["category"] or row.category,
                "parse_mode": (
                    _normalize_site_media_parse_mode(ref["parse_mode"] if ref["parse_mode"] is not None else getattr(row, "parse_mode", "auto"))
                    if str((ref["category"] or row.category or "site_media")).strip().lower() == "site_media"
                    else _normalize_parse_mode(ref["parse_mode"] if ref["parse_mode"] is not None else getattr(row, "parse_mode", "auto"))
                ),
                "parser_result": ref["parser_result"] if ref["parser_result"] is not None else _safe_json_loads(getattr(row, "parse_result_json", None)),
                "status": _normalize_upload_status(ref["status"] if ref["status"] is not None else getattr(row, "upload_status", "uploaded")),
                "error": ref["error"] if ref["error"] is not None else getattr(row, "error_message", None),
                "media_kind": row.media_kind,
            }
        )
    return normalized


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/health")
def api_health() -> dict:
    return {"ok": True, "service": "communication-first-crm"}


@app.get("/health/ui")
def ui_build_health() -> dict:
    return {
        "status": "ok",
        "pages": {
            "staff_estimator": _ui_page_build_info(ESTIMATOR_HTML_PATH, "/staff-estimator"),
            "staff_login": _ui_page_build_info(STAFF_LOGIN_HTML_PATH, "/staff-login"),
            "staff_crm": _ui_page_build_info(STAFF_CRM_HTML_PATH, "/staff-crm"),
            "staff_customer_uploads": _ui_page_build_info(STAFF_CUSTOMER_UPLOADS_HTML_PATH, "/staff-customer-uploads"),
            "public_estimator": _ui_page_build_info(CUSTOMER_UPLOAD_HTML_PATH, "/public-estimator"),
            "admin_pricing": _ui_page_build_info(ADMIN_PRICING_HTML_PATH, "/admin-pricing"),
            "demo": _ui_page_build_info(DEMO_HTML_PATH, "/demo"),
        },
    }


@app.get("/api/uploads", dependencies=[Depends(require_api_key)])
def list_uploaded_assets(
    draft_token: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    safe_draft = SAFE_FILE_RE.sub("_", str(draft_token or "").strip()).strip("._")
    rows = (
        db.query(UploadedAsset)
        .filter(UploadedAsset.draft_token == safe_draft, UploadedAsset.quote_id.is_(None))
        .order_by(UploadedAsset.created_at.asc(), UploadedAsset.id.asc())
        .all()
    )
    return {"draftToken": safe_draft, "assets": [_serialize_uploaded_asset(row) for row in rows]}


@app.get("/api/uploads/assets/{asset_id}/content", dependencies=[Depends(require_api_key)])
def uploaded_asset_content(asset_id: int, db: Session = Depends(get_db)):
    asset = db.query(UploadedAsset).filter(UploadedAsset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uploaded asset not found")
    raw = storage.read_bytes(asset.storage_path)
    headers = {"Content-Disposition": f'inline; filename="{asset.file_name}"'}
    return Response(content=raw, media_type=asset.content_type or "application/octet-stream", headers=headers)


@app.post("/api/uploads/site-media", dependencies=[Depends(require_api_key)])
async def upload_site_media_assets(
    draft_token: str = Form(...),
    parse_mode: str = Form("auto"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    logger.info(
        "site_media_upload_started draft_token=%s parse_mode=%s files=%s",
        draft_token,
        parse_mode,
        [getattr(file, "filename", "") for file in files or []],
    )
    assets = await _store_uploaded_assets(
        files=files,
        draft_token=draft_token,
        category="site_media",
        parse_mode=parse_mode,
        db=db,
    )
    logger.info(
        "site_media_upload_completed draft_token=%s asset_ids=%s",
        draft_token,
        [asset.id for asset in assets],
    )
    return {"draftToken": draft_token, "assets": [_serialize_uploaded_asset(asset) for asset in assets]}


@app.post("/api/uploads/measurement-notes", dependencies=[Depends(require_api_key)])
async def upload_measurement_note_assets(
    draft_token: str = Form(...),
    parse_mode: str = Form("force_measurement_note"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    logger.info(
        "measurement_note_upload_started draft_token=%s parse_mode=%s files=%s",
        draft_token,
        parse_mode,
        [getattr(file, "filename", "") for file in files or []],
    )
    assets = await _store_uploaded_assets(
        files=files,
        draft_token=draft_token,
        category="measurement_note",
        parse_mode=parse_mode,
        db=db,
    )
    logger.info(
        "measurement_note_upload_completed draft_token=%s asset_ids=%s",
        draft_token,
        [asset.id for asset in assets],
    )
    return {"draftToken": draft_token, "assets": [_serialize_uploaded_asset(asset) for asset in assets]}


@app.post("/api/uploads/dimensions", dependencies=[Depends(require_api_key)])
async def upload_dimension_assets(
    draft_token: str = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    logger.info(
        "dimensions_endpoint upload_started draft_token=%s files=%s",
        draft_token,
        [getattr(file, "filename", "") for file in files or []],
    )
    assets = await _store_uploaded_assets(
        files=files,
        draft_token=draft_token,
        category="site_media",
        parse_mode="auto",
        db=db,
    )
    logger.info(
        "dimensions_endpoint upload_stored assets=%s",
        [
            {
                "id": asset.id,
                "file_name": asset.file_name,
                "storage_path": asset.storage_path,
                "content_type": asset.content_type,
            }
            for asset in assets
        ],
    )

    measurement_entries: list[dict] = []
    measurement_parse: dict[str, object] = {}
    extraction_meta: dict[str, object] = {}
    message = "No measurements detected. Try a clearer image or enter rows manually."
    parse_failed = False
    failure_error = ""
    failure_error_type = ""
    failure_details = ""

    try:
        logger.info(
            "dimensions_endpoint parser_invoking asset_ids=%s uploaded_images=%s",
            [asset.id for asset in assets],
            [
                {
                    "id": asset.id,
                    "file_name": asset.file_name,
                    "storage_path": asset.storage_path,
                }
                for asset in assets
            ],
        )
        ai_result = estimate_job(
            lot_size_sqft=Decimal("0"),
            edge_length=Decimal("0"),
            terrain_type="mixed",
            zip_code="",
            uploaded_images=[_uploaded_asset_analysis_record(asset) for asset in assets],
            measurement_reference_images=None,
            job_notes=None,
            exclusions=None,
            material_type=BLOWER_DEFAULT_MATERIAL,
            placement_method=BLOWER_DEFAULT_PLACEMENT_METHOD,
            obstacle_count=0,
            include_haulaway=False,
            has_gates=False,
            primary_job_type_override=None,
            material_depth_inches=None,
            confirmed_measurement_entries=None,
            allow_site_media_measurement_reference_detection=True,
        )
        measurement_entries = _ensure_measurement_entry_sources(ai_result.get("measurement_entries") or [], assets)
        measurement_parse = ai_result.get("measurement_parse") or {}
        extraction_meta = ai_result.get("extraction_meta") or {}
        parse_classification = str((measurement_parse.get("classification") or "")).strip().lower()
        measurement_lines = _measurement_lines_from_entries(measurement_entries)
        message = _dimension_upload_message(
            measurement_entries=measurement_entries,
            measurement_parse=measurement_parse,
            extraction_meta=extraction_meta,
            asset_name=str(assets[0].file_name or "uploaded image").strip(),
        )
        logger.info(
            "dimensions_endpoint parser_output asset_ids=%s classification=%s rows=%s openai_error=%s openai_used=%s fallback_ocr_used=%s raw_entries=%s normalized_lines=%s ocr_debug=%s message=%s",
            [asset.id for asset in assets],
            parse_classification,
            len(measurement_entries),
            extraction_meta.get("openai_error"),
            extraction_meta.get("openai_used"),
            extraction_meta.get("fallback_ocr_used"),
            measurement_entries,
            measurement_lines,
            extraction_meta.get("ocr_debug"),
            message,
        )
    except Exception as exc:
        logger.exception("dimensions_endpoint parse_failed asset_ids=%s", [asset.id for asset in assets])
        detailed_error = str(exc).strip() or "unknown_error"
        message = f"Measurement parsing failed: {detailed_error}"
        extraction_meta = {
            "parse_error": detailed_error,
            "reason_code": "measurement_parse_exception",
            "error_type": exc.__class__.__name__,
            "details": traceback.format_exc(limit=3).strip(),
        }
        parse_failed = True

    failure_fields = _dimensions_failure_fields(
        measurement_entries=measurement_entries,
        measurement_parse=measurement_parse,
        extraction_meta=extraction_meta,
        message=message,
    )
    failure_error = str(failure_fields.get("error") or "")
    failure_error_type = str(failure_fields.get("error_type") or "")
    failure_details = str(failure_fields.get("details") or "")
    response_failed = failure_fields.get("ok") is False
    soft_no_measurement_failure = failure_error_type in {
        "scene_photo_estimation",
        "failed_ocr_unreadable_note",
        "no_measurements_detected",
    }

    parser_snapshot = {
        "ok": bool(failure_fields.get("ok") is True),
        "error": failure_error,
        "error_type": failure_error_type,
        "details": failure_details,
        "measurement_entries": measurement_entries,
        "measurement_parse": measurement_parse,
        "extraction_meta": extraction_meta,
        "measurements": _measurement_lines_from_entries(measurement_entries),
        "message": message,
    }

    for asset in assets:
        asset.parse_result_json = json.dumps(parser_snapshot, default=str)
        hard_failure = parse_failed or (response_failed and bool(failure_error) and not soft_no_measurement_failure)
        asset.upload_status = "error" if hard_failure else "ready"
        asset.error_message = None if soft_no_measurement_failure else (failure_error or (message if response_failed else None))
    db.commit()
    for asset in assets:
        db.refresh(asset)

    response_payload = _dimensions_response_payload(
        assets=assets,
        measurement_entries=measurement_entries,
        measurement_parse=measurement_parse,
        extraction_meta=extraction_meta,
        message=message,
        ok=bool(failure_fields.get("ok") is True),
        error=failure_error,
        error_type=failure_error_type,
        details=failure_details,
    )
    logger.info(
        "dimensions_endpoint response_json asset_ids=%s measurements=%s rows=%s message=%s",
        [asset.id for asset in assets],
        response_payload.get("measurements"),
        len(response_payload.get("measurement_entries") or []),
        response_payload.get("message"),
    )
    if response_payload.get("ok") is False:
        logger.warning("dimensions_endpoint failure_response_json payload=%s", response_payload)
    return response_payload


@app.post("/api/measurement-notes/upload", dependencies=[Depends(require_api_key)])
async def upload_measurement_note_panel_assets(
    draft_token: str | None = Form(None),
    parse_mode: str = Form("force_measurement_note"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    resolved_draft_token = str(draft_token or "").strip() or f"temp-note-{secrets.token_hex(8)}"
    logger.info(
        "measurement_note_panel_upload_started draft_token=%s parse_mode=%s files=%s",
        resolved_draft_token,
        parse_mode,
        [getattr(file, "filename", "") for file in files or []],
    )
    assets = await _store_uploaded_assets(
        files=files,
        draft_token=resolved_draft_token,
        category="measurement_note",
        parse_mode=parse_mode,
        db=db,
    )
    logger.info(
        "measurement_note_panel_upload_completed draft_token=%s asset_ids=%s",
        resolved_draft_token,
        [asset.id for asset in assets],
    )
    return {"draftToken": resolved_draft_token, "assets": [_serialize_uploaded_asset(asset) for asset in assets]}


@app.post("/api/test/parse-handwritten", dependencies=[Depends(require_api_key)])
def parse_handwritten_test(payload: dict = Body(...)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON body is required")

    image_url = _validated_fetchable_image_url(payload.get("imageUrl") or payload.get("image_url"))
    rows, error = parse_handwritten_measurement_rows_from_image_url(image_url)
    if error:
        detail_message = _handwritten_parse_error_message(error)
        raise HTTPException(
            status_code=_handwritten_parse_error_status(error),
            detail={
                "message": detail_message,
                "reason_code": error,
            },
        )
    return {"rows": rows}


def _parse_measurement_note_asset_rows(
    *,
    asset_id: int,
    db: Session,
) -> tuple[UploadedAsset, list[dict]]:
    asset = db.query(UploadedAsset).filter(UploadedAsset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uploaded asset not found")

    category = _normalize_media_category(getattr(asset, "category", "site_media"), "site_media")
    status_name = _normalize_upload_status(getattr(asset, "upload_status", "uploaded"))
    if category != "measurement_note":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Measurement Sheet / Note Photo assets can use handwritten note parsing",
        )
    if status_name not in {"uploaded", "ready"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Measurement note parsing requires an uploaded note asset",
        )

    asset_url = _upload_asset_url(asset.id)
    logger.info(
        "parser_route_chosen route=measurement-note asset_id=%s url=%s storage_key=%s status=%s",
        asset.id,
        asset_url,
        asset.storage_path,
        status_name,
    )
    logger.info(
        "parse_started route=measurement-note asset_id=%s url=%s filename=%s",
        asset.id,
        asset_url,
        asset.file_name,
    )

    asset.upload_status = "parsing"
    asset.error_message = None
    db.commit()
    db.refresh(asset)

    raw = storage.read_bytes(asset.storage_path)
    rows, error = parse_handwritten_measurement_rows_from_uploaded_image(
        image_bytes=raw,
        source_name=asset.file_name,
        content_type=asset.content_type,
        asset_reference=asset_url,
    )

    if error:
        detail_message = _handwritten_parse_error_message(error)
        logger.warning(
            "measurement_note_parse_failed asset_id=%s filename=%s reason_code=%s detail=%s",
            asset.id,
            asset.file_name,
            error,
            detail_message,
        )
        asset.upload_status = "error"
        asset.error_message = detail_message
        db.commit()
        raise HTTPException(
            status_code=_handwritten_parse_error_status(error),
            detail={
                "message": detail_message,
                "reason_code": error,
            },
        )

    asset.parse_result_json = json.dumps(_measurement_note_parser_snapshot(asset, rows), default=str)
    asset.upload_status = "ready"
    asset.error_message = None
    db.commit()
    db.refresh(asset)
    return asset, rows


@app.post("/api/measurement-notes/parse", dependencies=[Depends(require_api_key)])
def parse_measurement_note(payload: dict = Body(...), db: Session = Depends(get_db)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON body is required")
    raw_asset_id = payload.get("assetId") or payload.get("asset_id")
    try:
        asset_id = int(str(raw_asset_id).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="assetId is required")

    _asset, rows = _parse_measurement_note_asset_rows(asset_id=asset_id, db=db)
    return {"rows": rows}


@app.post("/api/uploads/assets/{asset_id}/parse-measurements", dependencies=[Depends(require_api_key)])
def parse_uploaded_measurement_asset(
    asset_id: int,
    db: Session = Depends(get_db),
):
    asset, rows = _parse_measurement_note_asset_rows(asset_id=asset_id, db=db)
    return {
        "asset": _serialize_uploaded_asset(asset),
        "rows": rows,
    }


@app.post("/api/uploads/assets/{asset_id}/use-as-measurement-note", dependencies=[Depends(require_api_key)])
def use_uploaded_asset_as_measurement_note(
    asset_id: int,
    db: Session = Depends(get_db),
):
    asset = db.query(UploadedAsset).filter(UploadedAsset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uploaded asset not found")

    asset.category = "measurement_note"
    asset.media_kind = "measurement_note"
    asset.parse_mode = "force_measurement_note"
    asset.upload_status = "uploaded"
    asset.parse_result_json = None
    asset.error_message = None
    db.commit()
    db.refresh(asset)
    return {"asset": _serialize_uploaded_asset(asset)}


@app.post("/api/uploads/exclusions", dependencies=[Depends(require_api_key)])
async def upload_exclusion_assets(
    draft_token: str = Form(...),
    parse_mode: str = Form("auto"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    logger.info(
        "exclusion_upload_started draft_token=%s parse_mode=%s files=%s",
        draft_token,
        parse_mode,
        [getattr(file, "filename", "") for file in files or []],
    )
    assets = await _store_uploaded_assets(
        files=files,
        draft_token=draft_token,
        category="exclusion",
        parse_mode=parse_mode,
        db=db,
    )
    logger.info(
        "exclusion_upload_completed draft_token=%s asset_ids=%s",
        draft_token,
        [asset.id for asset in assets],
    )
    return {"draftToken": draft_token, "assets": [_serialize_uploaded_asset(asset) for asset in assets]}


def _openai_health_payload() -> dict:
    current_settings = refresh_settings()
    api_key = runtime_openai_api_key()
    model = (current_settings.openai_vision_model or "gpt-4.1").strip() or "gpt-4.1"
    ca_bundle = (current_settings.openai_ca_bundle or "").strip()
    try:
        if current_settings.openai_allow_insecure_ssl:
            ssl_context = ssl._create_unverified_context()
        elif ca_bundle:
            ssl_context = ssl.create_default_context(cafile=ca_bundle)
        else:
            ssl_context = ssl.create_default_context()
    except (ssl.SSLError, OSError):
        return {
            "ok": False,
            "color": "red",
            "label": "AI Error",
            "detail": "Invalid CA bundle",
            "reason_code": "openai_ca_bundle_invalid",
            "model": model,
        }

    if not api_key:
        return {
            "ok": False,
            "color": "red",
            "label": "AI Off",
            "detail": "Missing API key",
            "reason_code": "openai_not_configured",
            "model": model,
        }

    encoded_model = urllib.parse.quote(model, safe="")
    request = urllib.request.Request(
        f"https://api.openai.com/v1/models/{encoded_model}",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )

    def _network_failure(reason) -> tuple[str, str]:
        text = str(reason or "").lower()
        if any(marker in text for marker in OPENAI_DNS_FAILURE_MARKERS):
            return "DNS failure", "openai_dns_failed"
        if "timed out" in text:
            return "Request timed out", "openai_request_timed_out"
        if any(token in text for token in ("certificate verify failed", "ssl", "tls", "wrong version number", "alert handshake failure")):
            return "TLS / certificate issue", "openai_tls_failed"
        if "connection refused" in text:
            return "Connection refused", "openai_connection_refused"
        if any(token in text for token in ("connection reset", "eof occurred in violation of protocol", "unexpected eof")):
            return "Connection reset", "openai_connection_reset"
        return "Connection failed", "openai_request_failed"

    try:
        with urllib.request.urlopen(request, timeout=8, context=ssl_context) as response:
            if 200 <= response.status < 300:
                return {
                    "ok": True,
                    "color": "green",
                    "label": "AI Ready",
                    "detail": f"{model} connected",
                    "reason_code": "ok",
                    "model": model,
                }
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            detail = "Key rejected"
            reason = "openai_auth_failed"
        elif exc.code == 404:
            detail = "Model unavailable"
            reason = "openai_model_unavailable"
        elif exc.code == 429:
            detail = "Billing or rate limit"
            reason = "openai_rate_limited"
        else:
            detail = f"HTTP {exc.code}"
            reason = "openai_http_error"
        return {
            "ok": False,
            "color": "red",
            "label": "AI Error",
            "detail": detail,
            "reason_code": reason,
            "model": model,
        }
    except urllib.error.URLError as exc:
        detail, reason = _network_failure(getattr(exc, "reason", exc))
        return {
            "ok": False,
            "color": "red",
            "label": "AI Error",
            "detail": detail,
            "reason_code": reason,
            "model": model,
        }
    except (TimeoutError, OSError) as exc:
        detail, reason = _network_failure(exc)
        return {
            "ok": False,
            "color": "red",
            "label": "AI Error",
            "detail": detail,
            "reason_code": reason,
            "model": model,
        }

    return {
        "ok": False,
        "color": "red",
        "label": "AI Error",
        "detail": "Unknown issue",
        "reason_code": "openai_unknown_error",
        "model": model,
    }


def _openai_proxy_diagnostics() -> dict:
    proxy_keys = [
        key
        for key in (
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
            "NO_PROXY",
            "no_proxy",
        )
        if str(os.environ.get(key, "")).strip()
    ]
    if proxy_keys:
        return {
            "present": True,
            "detail": f"Environment proxy vars detected: {', '.join(proxy_keys)}",
            "keys": proxy_keys,
        }
    return {
        "present": False,
        "detail": "No proxy environment variables detected",
        "keys": [],
    }


@app.get("/health/openai", dependencies=[Depends(require_estimator_access)])
def openai_health() -> dict:
    return _openai_health_payload()


@app.get("/health/openai/diagnostics", dependencies=[Depends(require_estimator_access)])
def openai_health_diagnostics() -> dict:
    current_settings = refresh_settings()
    host = "api.openai.com"
    port = 443
    model = (current_settings.openai_vision_model or "gpt-4.1").strip() or "gpt-4.1"
    api_key = runtime_openai_api_key()
    ca_bundle = (current_settings.openai_ca_bundle or "").strip()
    health = _openai_health_payload()

    diagnostics = {
        "host": host,
        "port": port,
        "model": model,
        "key_present": bool(api_key),
        "insecure_ssl": bool(current_settings.openai_allow_insecure_ssl),
        "ca_bundle": ca_bundle,
        "proxy": _openai_proxy_diagnostics(),
        "dns": {"ok": False, "detail": "Not checked"},
        "tcp": {"ok": False, "detail": "Not checked"},
        "http": {
            "ok": bool(health.get("ok")),
            "detail": str(health.get("detail") or "Unknown issue"),
            "reason_code": str(health.get("reason_code") or "unknown"),
        },
    }

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        unique = sorted({row[4][0] for row in addresses if row and len(row) >= 5 and row[4]})
        diagnostics["dns"] = {
            "ok": True,
            "detail": f"Resolved {len(unique)} address{'es' if len(unique) != 1 else ''}",
            "addresses": unique[:4],
        }
    except OSError as exc:
        diagnostics["dns"] = {
            "ok": False,
            "detail": str(exc) or "DNS lookup failed",
        }
        diagnostics["tcp"] = {
            "ok": False,
            "detail": "Skipped because DNS lookup failed",
        }
        return diagnostics

    try:
        with socket.create_connection((host, port), timeout=5):
            diagnostics["tcp"] = {
                "ok": True,
                "detail": "Connected to port 443",
            }
    except OSError as exc:
        diagnostics["tcp"] = {
            "ok": False,
            "detail": str(exc) or "TCP connection failed",
        }

    return diagnostics


def _render_staff_estimator_page():
    return _render_static_page(
        ESTIMATOR_HTML_PATH,
        "Estimator UI not found",
        replacements={
            "ESTIMATOR_BUILD_STAMP": _build_stamp_for_path(ESTIMATOR_HTML_PATH),
        },
    )


def _render_public_estimator_page():
    return _render_static_page(CUSTOMER_UPLOAD_HTML_PATH, "Customer upload UI not found")


def _render_quote_tool_page():
    return _render_static_page(QUOTE_TOOL_HTML_PATH, "Quote tool UI not found")


def _render_demo_page():
    return _render_static_page(
        DEMO_HTML_PATH,
        "Demo UI not found",
        replacements={
            "ESTIMATOR_USER": settings.estimator_user or "demo",
            "ESTIMATOR_PASSWORD": settings.estimator_password or "demo123",
        },
    )


def _render_staff_login_page():
    return _render_static_page(
        STAFF_LOGIN_HTML_PATH,
        "Staff login UI not found",
        replacements={
            "ESTIMATOR_USER": settings.estimator_user or "demo",
        },
    )


def _render_staff_customer_uploads_page():
    return _render_static_page(
        STAFF_CUSTOMER_UPLOADS_HTML_PATH,
        "Staff customer upload UI not found",
    )


def _render_admin_pricing_page():
    return _render_static_page(
        ADMIN_PRICING_HTML_PATH,
        "Admin pricing UI not found",
    )


def _render_staff_crm_page():
    return _render_static_page(
        STAFF_CRM_HTML_PATH,
        "Staff CRM UI not found",
    )


def _html_page_response(html: str, *, build_stamp: Optional[str] = None, source_path: Optional[Path] = None) -> HTMLResponse:
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    if build_stamp:
        headers["X-Barkboys-Build"] = build_stamp
    if source_path is not None:
        try:
            last_modified = datetime.utcfromtimestamp(source_path.stat().st_mtime)
            headers["Last-Modified"] = last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT")
        except OSError:
            pass
    return HTMLResponse(
        content=html,
        headers=headers,
    )


def _create_estimator_session(response: Response) -> str:
    session_token = secrets.token_urlsafe(32)
    ESTIMATOR_SESSIONS.add(session_token)
    response.set_cookie(
        key=ESTIMATOR_SESSION_COOKIE,
        value=session_token,
        max_age=ESTIMATOR_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return session_token


def _clear_estimator_session(response: Response, session_token: Optional[str]) -> None:
    if session_token:
        ESTIMATOR_SESSIONS.discard(session_token)
    response.delete_cookie(key=ESTIMATOR_SESSION_COOKIE, path="/")


@app.get("/", response_class=HTMLResponse)
def home_page():
    return RedirectResponse(url="/crm/workspace", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return _html_page_response(_render_demo_page())


@app.get("/staff-login", response_class=HTMLResponse)
def staff_login_page(barkboys_staff_session: Optional[str] = Cookie(default=None)):
    if barkboys_staff_session and barkboys_staff_session in ESTIMATOR_SESSIONS:
        return RedirectResponse(url="/staff-estimator", status_code=status.HTTP_303_SEE_OTHER)
    if not settings.estimator_user and not settings.estimator_password:
        return RedirectResponse(url="/staff-estimator", status_code=status.HTTP_303_SEE_OTHER)
    return _html_page_response(_render_staff_login_page())


@app.post("/staff-login")
def create_staff_session(response: Response, payload: dict = Body(...)):
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")

    if not settings.estimator_user and not settings.estimator_password:
        _create_estimator_session(response)
        return {"ok": True, "redirect_url": "/staff-estimator"}

    user_ok = secrets.compare_digest(username, settings.estimator_user)
    pass_ok = secrets.compare_digest(password, settings.estimator_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid staff login",
        )

    _create_estimator_session(response)
    return {"ok": True, "redirect_url": "/staff-estimator"}


@app.post("/staff-logout")
def destroy_staff_session(
    response: Response,
    barkboys_staff_session: Optional[str] = Cookie(default=None),
):
    _clear_estimator_session(response, barkboys_staff_session)
    return {"ok": True}


@app.get("/staff-estimator", response_class=HTMLResponse)
def staff_estimator_page(barkboys_staff_session: Optional[str] = Cookie(default=None)):
    redirect = _staff_session_required_redirect(barkboys_staff_session)
    if redirect:
        return redirect
    return _html_page_response(
        _render_staff_estimator_page(),
        build_stamp=_build_stamp_for_path(ESTIMATOR_HTML_PATH),
        source_path=ESTIMATOR_HTML_PATH,
    )


@app.get("/estimator", response_class=HTMLResponse)
def estimator_page_legacy(barkboys_staff_session: Optional[str] = Cookie(default=None)):
    redirect = _staff_session_required_redirect(barkboys_staff_session)
    if redirect:
        return redirect
    return _html_page_response(
        _render_staff_estimator_page(),
        build_stamp=_build_stamp_for_path(ESTIMATOR_HTML_PATH),
        source_path=ESTIMATOR_HTML_PATH,
    )


@app.get("/public-estimator", response_class=HTMLResponse)
def public_estimator_page():
    return _html_page_response(_render_public_estimator_page())


@app.get("/customer-upload", response_class=HTMLResponse)
def customer_upload_page_legacy():
    return _html_page_response(_render_public_estimator_page())


@app.get("/quote-tool", response_class=HTMLResponse)
def quote_tool_page(barkboys_staff_session: Optional[str] = Cookie(default=None)):
    redirect = _staff_session_required_redirect(barkboys_staff_session)
    if redirect:
        return redirect
    return _html_page_response(_render_quote_tool_page())


@app.get("/staff-customer-uploads", response_class=HTMLResponse)
def staff_customer_uploads_page(barkboys_staff_session: Optional[str] = Cookie(default=None)):
    redirect = _staff_session_required_redirect(barkboys_staff_session)
    if redirect:
        return redirect
    return _html_page_response(_render_staff_customer_uploads_page())


@app.get("/admin-pricing", response_class=HTMLResponse)
def admin_pricing_page(barkboys_staff_session: Optional[str] = Cookie(default=None)):
    redirect = _staff_session_required_redirect(barkboys_staff_session)
    if redirect:
        return redirect
    return _html_page_response(_render_admin_pricing_page())


@app.get("/staff-crm", response_class=HTMLResponse)
def staff_crm_page(barkboys_staff_session: Optional[str] = Cookie(default=None)):
    redirect = _staff_session_required_redirect(barkboys_staff_session)
    if redirect:
        return redirect
    return _html_page_response(_render_staff_crm_page())


@app.post("/intake-submissions")
def create_intake_submission(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    customer_name = str(payload.get("customer_name") or "")
    phone = str(payload.get("phone") or "").strip() or None
    email = str(payload.get("email") or "").strip() or None
    address = str(payload.get("address") or "")
    notes = str(payload.get("notes") or "") or None
    capture_device = str(payload.get("capture_device") or "any_smartphone")
    material_type = str(payload.get("material_type") or BLOWER_DEFAULT_MATERIAL)
    delivery_truck_type = BLOWER_DEFAULT_TRUCK_TYPE
    placement_method = BLOWER_DEFAULT_PLACEMENT_METHOD
    lot_size_sqft = str(payload.get("lot_size_sqft") or "")
    edge_linear_ft = str(payload.get("edge_linear_ft") or "")
    turf_condition = str(payload.get("turf_condition") or "average")
    obstacles_count = str(payload.get("obstacles_count") or "0")
    slope = str(payload.get("slope") or "flat")
    debris_level = str(payload.get("debris_level") or "low")
    has_gates = str(payload.get("has_gates") or "")
    include_haulaway = str(payload.get("include_haulaway") or "")
    include_blowing = payload.get("include_blowing")
    include_blowing = None if include_blowing is None else str(include_blowing)
    website = str(payload.get("website") or "")
    photos = payload.get("photos") or []
    lidar_files = payload.get("lidar_files") or []
    if website and website.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Spam detected")

    customer_name = customer_name.strip()
    address = address.strip()
    if not customer_name or not address:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name and address are required")
    if not phone and not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone or email is required",
        )
    if email and not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Valid email is required")
    zip_code = _normalize_zip_code(payload.get("zip_code"), address=address)

    frequency = "one_time"
    lot_size = _to_decimal_or_default(lot_size_sqft, Decimal("0"))
    edge_length = _to_decimal_or_default(edge_linear_ft, Decimal("0"))
    turf_condition = _normalize_choice(turf_condition, AI_TURF_CONDITIONS, "average")
    slope = _normalize_choice(slope, AI_SLOPE_OPTIONS, "flat")
    debris_level = _normalize_choice(debris_level, AI_DEBRIS_LEVELS, "low")
    obstacle_count = max(int(_to_decimal_or_default(obstacles_count, Decimal("0"))), 0)
    has_gates_bool = _to_bool(has_gates)
    include_haulaway_bool = _to_bool(include_haulaway)
    include_blowing_bool = _to_bool(include_blowing) if include_blowing is not None else True
    capture_device_clean = (capture_device or "any_smartphone").strip().lower()[:64]
    material_type = _normalize_blower_material(material_type)

    framed_inputs = {
        "frequency": frequency,
        "lot_size_sqft": str(lot_size),
        "edge_linear_ft": str(edge_length),
        "turf_condition": turf_condition,
        "slope": slope,
        "obstacles_count": obstacle_count,
        "debris_level": debris_level,
        "has_gates": has_gates_bool,
        "include_haulaway": include_haulaway_bool,
        "include_blowing": include_blowing_bool,
        "material_type": material_type,
        "delivery_truck_type": delivery_truck_type,
        "placement_method": placement_method,
        "zip_code": zip_code,
        "photo_count": len(photos),
        "lidar_count": len(lidar_files),
    }

    submission = IntakeSubmission(
        customer_name=customer_name,
        phone=phone,
        email=email.lower() if email else None,
        address=address,
        notes=(notes or "").strip() or None,
        capture_device=capture_device_clean,
        framed_inputs_json=json.dumps(framed_inputs),
        status="new",
    )
    db.add(submission)
    db.flush()

    submission_prefix = f"intake-{submission.id}"
    stored_photos = _save_upload_group(photos, submission_prefix, "photo")
    stored_lidar = _save_upload_group(lidar_files, submission_prefix, "lidar_scan")
    all_stored = stored_photos + stored_lidar
    for media in all_stored:
        db.add(
            IntakeMedia(
                intake_submission_id=submission.id,
                file_name=media["file_name"],
                content_type=media["content_type"],
                file_size=media["file_size"],
                media_kind=media["media_kind"],
                storage_path=media["storage_path"],
            )
        )

    lot_for_estimate = lot_size if lot_size > 0 else Decimal("4500") + (Decimal(len(stored_photos)) * Decimal("350"))
    edge_for_estimate = edge_length if edge_length > 0 else max(
        Decimal("120"),
        (lot_for_estimate.sqrt() * Decimal("3.6")).quantize(Decimal("0.01")),
    )
    ai_items = _build_demo_ai_items(
        lot_size=lot_for_estimate,
        edge_length=edge_for_estimate,
        turf_condition=turf_condition,
        slope=slope,
        debris_level=debris_level,
        obstacle_count=obstacle_count,
        has_gates_bool=has_gates_bool,
        include_haulaway_bool=include_haulaway_bool,
        include_blowing_bool=include_blowing_bool,
        material_type=material_type,
        customer_address=address,
    )
    quick_pricing = calculate_quote(
        items=ai_items,
        frequency=frequency,
        tax_rate=Decimal("0"),
        zone_modifier_percent=Decimal("0"),
    )
    quick_total = Decimal(str(quick_pricing["total"]))
    quick_low = (quick_total * Decimal("0.90")).quantize(Decimal("0.01"))
    quick_high = (quick_total * Decimal("1.15")).quantize(Decimal("0.01"))

    db.commit()

    return {
        "id": submission.id,
        "status": submission.status,
        "customer_name": submission.customer_name,
        "photo_count": len(stored_photos),
        "lidar_count": len(stored_lidar),
        "message": "Submission received. BarkBoys team will review and send estimate.",
        "instant_estimate": {
            "range_low": str(quick_low),
            "range_high": str(quick_high),
            "currency": "USD",
            "disclaimer": "Estimate based on customer input and photos. BarkBoys reviews every estimate for accuracy before final quote.",
            "delivery_fee_basis": f"Delivery fee estimated from address distance to {BARKBOYS_HQ_LABEL} plus the material load.",
        },
    }


@app.get("/intake-submissions", dependencies=[Depends(require_api_key)])
def list_intake_submissions(db: Session = Depends(get_db)):
    rows = (
        db.query(IntakeSubmission)
        .order_by(desc(IntakeSubmission.created_at), desc(IntakeSubmission.id))
        .limit(200)
        .all()
    )
    payload = []
    for row in rows:
        media_count = db.query(IntakeMedia).filter(IntakeMedia.intake_submission_id == row.id).count()
        payload.append(
            {
                "id": row.id,
                "customer_name": row.customer_name,
                "phone": row.phone,
                "email": row.email,
                "address": row.address,
                "status": row.status,
                "capture_device": row.capture_device,
                "created_at": row.created_at,
                "media_count": media_count,
                "framed_inputs": json.loads(row.framed_inputs_json) if row.framed_inputs_json else {},
            }
        )
    return payload


@app.post("/intake-submissions/{submission_id}/ai-draft", dependencies=[Depends(require_api_key)])
def build_ai_draft_from_intake(
    submission_id: int,
    payload: Optional[dict] = Body(default=None),
    frequency: str = "one_time",
    tax_rate: str = "0",
    zone_modifier_percent: str = "0",
    db: Session = Depends(get_db),
):
    submission = db.get(IntakeSubmission, submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intake submission not found")

    framed = {}
    if submission.framed_inputs_json:
        try:
            framed = json.loads(submission.framed_inputs_json)
        except json.JSONDecodeError:
            framed = {}

    frequency = _normalize_choice(frequency, AI_FREQUENCIES, "one_time")
    turf_condition = _normalize_choice(str(framed.get("turf_condition", "average")), AI_TURF_CONDITIONS, "average")
    slope = _normalize_choice(str(framed.get("slope", "flat")), AI_SLOPE_OPTIONS, "flat")
    debris_level = _normalize_choice(str(framed.get("debris_level", "low")), AI_DEBRIS_LEVELS, "low")

    lot_size_sqft_raw = str(framed.get("lot_size_sqft", "") or "")
    edge_linear_ft_raw = str(framed.get("edge_linear_ft", "") or "")
    lot_size = _to_decimal_or_default(lot_size_sqft_raw, Decimal("0"))
    edge_length = _to_decimal_or_default(edge_linear_ft_raw, Decimal("0"))
    obstacle_count = max(int(_to_decimal_or_default(str(framed.get("obstacles_count", "0")), Decimal("0"))), 0)
    tax_rate_dec = _to_decimal_or_default(tax_rate, Decimal("0"))
    zone_modifier_dec = _to_decimal_or_default(zone_modifier_percent, Decimal("0"))
    has_gates_bool = bool(framed.get("has_gates", False))
    include_haulaway_bool = bool(framed.get("include_haulaway", False))
    include_blowing_bool = bool(framed.get("include_blowing", True))
    material_type = _normalize_blower_material(str(framed.get("material_type", BLOWER_DEFAULT_MATERIAL)))
    primary_job_type = str(framed.get("primary_job_type", "") or "").strip().lower() or None
    terrain_type = _normalize_choice(str(framed.get("terrain_type", "mixed")), AI_TERRAIN_OPTIONS, "mixed")
    zip_code = _extract_zip_code(submission.address, str(framed.get("zip_code", "") or ""))
    delivery_truck_type = BLOWER_DEFAULT_TRUCK_TYPE
    placement_method = BLOWER_DEFAULT_PLACEMENT_METHOD

    media_rows = db.query(IntakeMedia).filter(IntakeMedia.intake_submission_id == submission.id).all()
    photo_names = [row.file_name for row in media_rows if row.media_kind == "photo"]
    lidar_count = sum(1 for row in media_rows if row.media_kind == "lidar_scan")

    photo_tags = _infer_photo_tags_from_names(photo_names)
    required_photo_angles = ["front", "back", "left", "right"]
    covered_angles = [tag for tag in required_photo_angles if tag in photo_tags]
    missing_angles = [tag for tag in required_photo_angles if tag not in photo_tags]
    photo_payload = [
        {
            "file_name": row.file_name,
            "storage_path": row.storage_path,
            "content_type": row.content_type,
        }
        for row in media_rows
        if row.media_kind == "photo"
    ]

    ai_result = estimate_job(
        lot_size_sqft=lot_size,
        edge_length=edge_length,
        terrain_type=terrain_type,
        zip_code=zip_code,
        uploaded_images=photo_payload,
        job_notes=submission.notes,
        exclusions=None,
        material_type=material_type,
        placement_method=placement_method,
        obstacle_count=obstacle_count,
        include_haulaway=include_haulaway_bool,
        has_gates=has_gates_bool,
        primary_job_type_override=primary_job_type,
        confirmed_measurement_entries=(payload or {}).get("measurement_entries") or None,
    )
    if lot_size <= 0:
        lot_size = _to_decimal_or_default(str(ai_result.get("area_sqft") or ""), Decimal("0"))
    if edge_length <= 0:
        edge_length = _to_decimal_or_default(str(ai_result.get("edge_length_ft") or ""), Decimal("0"))

    material_yards_estimate = _to_decimal_or_default(
        str(ai_result.get("recommended_material_yards") or ""),
        Decimal("0"),
    )
    if material_yards_estimate <= 0:
        material_yards_estimate = max(
            Decimal("0.25"),
            (max(lot_size, Decimal("1")) / Decimal("1200")).quantize(Decimal("0.01")),
        )
    ai_items = _build_detected_task_items(ai_result.get("task_breakdown") or [])

    pricing = calculate_quote(
        items=ai_items,
        frequency=frequency,
        tax_rate=tax_rate_dec,
        zone_modifier_percent=zone_modifier_dec,
    )

    dimension_area = _to_decimal_or_default(
        str((ai_result.get("dimension_observations") or {}).get("estimated_area_sqft") or ""),
        Decimal("0"),
    )
    combined_bed_area = _to_decimal_or_default(str(ai_result.get("combined_bed_area_sqft") or ""), Decimal("0"))
    measurement_entries = ai_result.get("measurement_entries") or []
    bed_groups = ai_result.get("bed_groups") or []
    extraction_meta = ai_result.get("extraction_meta") or {}
    confidence = _estimate_ai_preview_confidence(
        photo_count=len(photo_names),
        covered_angle_count=len(covered_angles),
        lidar_count=lidar_count,
        capture_device=submission.capture_device or "",
        dimension_area=dimension_area,
        combined_bed_area=combined_bed_area,
        lot_size_present=bool(lot_size_sqft_raw),
        edge_length_present=bool(edge_linear_ft_raw),
        bed_group_count=len(bed_groups),
        measurement_entry_count=len(measurement_entries),
        trusted_measurements_available=bool(extraction_meta.get("trusted_measurements_available")),
        openai_used=bool(extraction_meta.get("openai_used")),
    )

    submission.status = "draft_generated"
    db.commit()

    _track_quote_event(
        db=db,
        event_name="intake_converted_to_draft",
        quote_id=None,
        metadata={
            "intake_submission_id": submission.id,
            "confidence": confidence,
            "frequency": frequency,
            "photo_count": len(photo_names),
            "lidar_count": lidar_count,
        },
    )

    return {
        "intake_submission_id": submission.id,
        "model": "demo-photo-estimator-v1",
        "summary": "AI draft generated from customer intake submission.",
        "confidence": confidence,
        "review_required": confidence < 0.85,
        "job": {
            "customer_name": submission.customer_name,
            "phone": submission.phone,
            "address": submission.address,
            "notes": submission.notes,
            "zip_code": zip_code,
            "area_sqft": ai_result["area_sqft"],
            "terrain_type": terrain_type,
            "primary_job_type": ai_result["primary_job_type"],
            "detected_tasks": ai_result["detected_tasks"],
            "detected_zones": ai_result.get("detected_zones") or [],
            "bed_groups": bed_groups,
            "combined_bed_area_sqft": ai_result.get("combined_bed_area_sqft"),
            "combined_bed_material_yards": ai_result.get("combined_bed_material_yards"),
            "crew_instructions": ai_result["crew_instructions"],
            "estimated_labor_hours": ai_result["estimated_labor_hours"],
            "material_cost": ai_result["material_cost"],
            "equipment_cost": ai_result["equipment_cost"],
            "suggested_price": ai_result["suggested_price"],
            "source": f"Customer Upload #{submission.id}",
            "capture_device": submission.capture_device,
        },
        "provided_inputs": {
            "lot_size_sqft": str(lot_size),
            "edge_linear_ft": str(edge_length),
            "material_yards": str(material_yards_estimate),
            "material_depth_inches": str(ai_result.get("measurement_defaults", {}).get("material_depth_inches") or ""),
            "turf_condition": turf_condition,
            "slope": slope,
            "terrain_type": terrain_type,
            "zip_code": zip_code,
            "obstacles_count": obstacle_count,
            "debris_level": debris_level,
            "has_gates": has_gates_bool,
            "include_haulaway": include_haulaway_bool,
            "include_blowing": include_blowing_bool,
            "material_type": material_type,
            "delivery_truck_type": delivery_truck_type,
            "placement_method": placement_method,
        },
        "photo_checklist": {
            "required_angles": required_photo_angles,
            "covered_angles": covered_angles,
            "missing_angles": missing_angles,
            "photo_count": len(photo_names),
            "lidar_count": lidar_count,
        },
        "frequency": frequency,
        "zone_modifier_percent": zone_modifier_dec,
        "items": pricing["items"],
        "subtotal": pricing["subtotal"],
        "zone_adjustment": pricing["zone_adjustment"],
        "frequency_discount_percent": pricing["frequency_discount_percent"],
        "discount_amount": pricing["discount_amount"],
        "tax_rate": pricing["tax_rate"],
        "tax_amount": pricing["tax_amount"],
        "total": pricing["total"],
        "estimated_labor_hours": ai_result["estimated_labor_hours"],
        "material_cost": ai_result["material_cost"],
        "equipment_cost": ai_result["equipment_cost"],
        "suggested_price": ai_result["suggested_price"],
        "recommended_crew_size": ai_result["recommended_crew_size"],
        "estimated_duration_hours": ai_result["estimated_duration_hours"],
        "crew_instructions": ai_result["crew_instructions"],
        "primary_job_type": ai_result["primary_job_type"],
        "detected_tasks": ai_result["detected_tasks"],
        "detected_zones": ai_result.get("detected_zones") or [],
        "zone_summary": ai_result.get("zone_summary") or "",
        "measurement_entries": measurement_entries,
        "bed_groups": bed_groups,
        "combined_bed_area_sqft": ai_result.get("combined_bed_area_sqft"),
        "combined_bed_material_yards": ai_result.get("combined_bed_material_yards"),
        "task_breakdown": ai_result["task_breakdown"],
        "dimension_observations": ai_result.get("dimension_observations"),
        "missing_angle_estimate": ai_result.get("missing_angle_estimate"),
        "extraction_meta": extraction_meta,
    }


@app.get("/quote-templates", dependencies=[Depends(require_api_key)])
def list_quote_templates():
    return {"templates": _load_service_templates()}


@app.get("/pricing-assumptions", dependencies=[Depends(require_api_key)])
def list_pricing_assumptions():
    return serialize_pricing_assumptions()


@app.get("/admin/material-costs", dependencies=[Depends(require_estimator_access)])
def list_admin_material_costs():
    data = serialize_pricing_assumptions()
    return {
        "materials": data.get("material_assumptions") or [],
        "material_pricing_tables": data.get("material_pricing_tables") or {},
    }


@app.put("/admin/material-costs", dependencies=[Depends(require_estimator_access)])
def save_admin_material_costs(payload: dict = Body(...)):
    materials = payload.get("materials")
    material_pricing_tables = payload.get("material_pricing_tables")
    if materials is None and material_pricing_tables is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide materials and/or material_pricing_tables")
    if materials is not None and not isinstance(materials, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="materials must be a list")
    if material_pricing_tables is not None and not isinstance(material_pricing_tables, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="material_pricing_tables must be an object")
    try:
        if materials is not None:
            update_material_assumptions(materials)
        if material_pricing_tables is not None:
            update_material_pricing_tables(material_pricing_tables)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    data = serialize_pricing_assumptions()
    return {
        "materials": data.get("material_assumptions") or [],
        "material_pricing_tables": data.get("material_pricing_tables") or {},
    }


@app.get("/admin/labor-assumptions", dependencies=[Depends(require_estimator_access)])
def list_admin_labor_assumptions():
    data = serialize_pricing_assumptions()
    return {
        "job_types": data.get("job_type_assumptions") or [],
        "labor_defaults": data.get("labor_defaults") or {},
    }


@app.get("/admin/measurement-settings", dependencies=[Depends(require_estimator_access)])
def list_admin_measurement_settings():
    return {"measurement_defaults": serialize_pricing_assumptions().get("measurement_defaults") or {}}


@app.put("/admin/labor-assumptions", dependencies=[Depends(require_estimator_access)])
def save_admin_labor_assumptions(payload: dict = Body(...)):
    job_types = payload.get("job_types")
    labor_defaults_payload = payload.get("labor_defaults")
    if job_types is None and labor_defaults_payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide job_types and/or labor_defaults",
        )
    if job_types is not None and not isinstance(job_types, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="job_types must be a list")
    if labor_defaults_payload is not None and not isinstance(labor_defaults_payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="labor_defaults must be an object")
    try:
        if job_types is not None:
            update_job_type_assumptions(job_types)
        if labor_defaults_payload is not None:
            update_labor_defaults(labor_defaults_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    data = serialize_pricing_assumptions()
    return {
        "job_types": data.get("job_type_assumptions") or [],
        "labor_defaults": data.get("labor_defaults") or {},
    }


@app.put("/admin/measurement-settings", dependencies=[Depends(require_estimator_access)])
def save_admin_measurement_settings(payload: dict = Body(...)):
    measurement_payload = payload.get("measurement_defaults")
    if measurement_payload is None:
        measurement_payload = payload
    if not isinstance(measurement_payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="measurement_defaults must be an object")
    try:
        updated = update_measurement_defaults(measurement_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"measurement_defaults": updated}


@app.get("/signals", response_model=List[SignalOut], dependencies=[Depends(require_api_key)])
def list_signals(db: Session = Depends(get_db)):
    return (
        db.query(Signal)
        .order_by(desc(Signal.date_observed), desc(Signal.id))
        .all()
    )


@app.post("/signals", response_model=SignalOut, dependencies=[Depends(require_api_key)])
def create_signal(payload: SignalCreate, db: Session = Depends(get_db)):
    signal = Signal(**_payload_to_dict(payload))
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal


@app.put("/signals/{signal_id}", response_model=SignalOut, dependencies=[Depends(require_api_key)])
def update_signal(signal_id: int, payload: SignalUpdate, db: Session = Depends(get_db)):
    signal = db.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")

    data = _payload_to_dict(payload)
    for key, value in data.items():
        setattr(signal, key, value)
    signal.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(signal)
    return signal


@app.delete("/signals/{signal_id}", dependencies=[Depends(require_api_key)])
def delete_signal(signal_id: int, db: Session = Depends(get_db)):
    signal = db.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")

    db.delete(signal)
    db.commit()
    return {"deleted": True}


@app.post("/import", response_model=ImportResult, dependencies=[Depends(require_api_key)])
def import_signals(payload: Any, db: Session = Depends(get_db)):
    if isinstance(payload, dict) and isinstance(payload.get("signals"), list):
        records = payload["signals"]
    elif isinstance(payload, list):
        records = payload
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    created = 0
    errors: list[dict] = []

    for index, record in enumerate(records):
        try:
            signal_in = SignalCreate(**record)
        except ValidationError as exc:
            errors.append({
                "row": index + 1,
                "error": _format_validation_error(exc),
            })
            continue

        signal = Signal(**_payload_to_dict(signal_in))
        db.add(signal)
        created += 1

    db.commit()

    return ImportResult(created=created, errors=errors)


@app.post("/leads", response_model=LeadOut, dependencies=[Depends(require_api_key)])
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    if payload.website and payload.website.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Spam detected")

    if not payload.email or not EMAIL_REGEX.match(payload.email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Valid email is required")

    data = _payload_to_dict(payload)
    data.pop("website", None)
    data.setdefault("customer_name", data.get("name"))
    data["status"] = _normalize_lead_status(data.get("status"), "new")
    data.setdefault("created_at", datetime.utcnow())

    lead = Lead(**data)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@app.get("/leads", response_model=List[LeadOut], dependencies=[Depends(require_api_key)])
def list_leads(limit: int = 25, db: Session = Depends(get_db)):
    safe_limit = max(1, min(limit, 100))
    return (
        db.query(Lead)
        .order_by(desc(Lead.created_at), desc(Lead.submitted_at), desc(Lead.id))
        .limit(safe_limit)
        .all()
    )


@app.patch("/leads/{lead_id}", response_model=LeadOut, dependencies=[Depends(require_api_key)])
def update_lead(lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    if hasattr(payload, "model_dump"):
        data = payload.model_dump(exclude_unset=True)
    elif hasattr(payload, "dict"):
        data = payload.dict(exclude_unset=True)
    else:
        data = _payload_to_dict(payload)
    for key, value in data.items():
        if key == "email":
            if value is None:
                continue
            candidate = value.strip().lower()
            if candidate and not EMAIL_REGEX.match(candidate):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Valid email is required",
                )
            setattr(lead, key, candidate or lead.email)
            continue
        if key == "sales_rep":
            setattr(lead, key, (value or "").strip() or None)
            continue
        if key == "status":
            setattr(lead, key, _normalize_lead_status(value, lead.status or "new"))
            continue
        setattr(lead, key, value)

    linked_quote_id = _linked_quote_id_for_lead(lead)
    if linked_quote_id:
        if getattr(lead, "quote_id", None) != linked_quote_id:
            lead.quote_id = linked_quote_id
        quote = db.get(Quote, linked_quote_id)
        if quote:
            job = db.get(Job, quote.job_id)
            if job:
                if "status" in data:
                    job.lead_status = lead.status
                if "follow_up_date" in data:
                    job.follow_up_date = lead.follow_up_date
                if "sales_rep" in data:
                    job.sales_rep = lead.sales_rep

    db.commit()
    db.refresh(lead)
    return lead


def _get_quote_or_404(quote_id: int, db: Session) -> Quote:
    quote = (
        db.query(Quote)
        .options(
            selectinload(Quote.job),
            selectinload(Quote.job).selectinload(Job.photos),
            selectinload(Quote.items),
            selectinload(Quote.media),
        )
        .filter(Quote.id == quote_id)
        .first()
    )
    if not quote:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    return quote


def _get_job_or_404(job_id: int, db: Session) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _serialize_quote(quote: Quote) -> dict:
    detected_tasks = _normalize_detected_tasks(getattr(quote.job, "detected_tasks_json", None))
    item_dicts = [
        {
            "id": item.id,
            "name": item.name,
            "description": item.description,
            "quantity": item.quantity,
            "unit": item.unit,
            "base_price": item.base_price,
            "per_unit_price": item.per_unit_price,
            "min_charge": item.min_charge,
            "line_total": item.line_total,
        }
        for item in quote.items
    ]

    pricing = {
        "subtotal": quote.subtotal,
        "zone_modifier_percent": quote.zone_modifier_percent,
        "zone_adjustment": quote.zone_adjustment,
        "frequency_discount_percent": quote.frequency_discount_percent,
        "discount_amount": quote.discount_amount,
        "tax_rate": quote.tax_rate,
        "tax_amount": quote.tax_amount,
        "total": quote.total,
    }
    text_quote = build_text_quote(
        quote.id,
        quote.job,
        quote.frequency,
        pricing,
        item_dicts,
        quote.created_at,
    )

    return {
        "id": quote.id,
        "contact_id": quote.contact_id,
        "created_at": quote.created_at,
        "frequency": quote.frequency,
        "zone_modifier_percent": quote.zone_modifier_percent,
        "subtotal": quote.subtotal,
        "zone_adjustment": quote.zone_adjustment,
        "frequency_discount_percent": quote.frequency_discount_percent,
        "discount_amount": quote.discount_amount,
        "tax_rate": quote.tax_rate,
        "tax_amount": quote.tax_amount,
        "total": quote.total,
        "job": {
            "id": quote.job.id,
            "customer_name": quote.job.customer_name,
            "phone": quote.job.phone,
            "email": quote.job.email,
            "address": quote.job.address,
            "zip_code": quote.job.zip_code,
            "area_sqft": quote.job.area_sqft,
            "terrain_type": quote.job.terrain_type,
            "primary_job_type": quote.job.primary_job_type,
            "detected_tasks": detected_tasks,
            "sales_rep": quote.job.sales_rep,
            "follow_up_date": quote.job.follow_up_date,
            "lead_status": quote.job.lead_status,
            "notes": quote.job.notes,
            "internal_notes": quote.job.internal_notes,
            "exclusions": quote.job.exclusions,
            "crew_instructions": quote.job.crew_instructions,
            "estimated_labor_hours": quote.job.estimated_labor_hours,
            "material_cost": quote.job.material_cost,
            "equipment_cost": quote.job.equipment_cost,
            "suggested_price": quote.job.suggested_price,
            "source": quote.job.source,
            "created_at": quote.job.created_at,
        },
        "job_photos": [_serialize_job_photo_asset(photo) for photo in quote.job.photos],
        "items": item_dicts,
        "media": [_serialize_quote_media_asset(media) for media in quote.media],
        "text_quote": text_quote,
    }


def _attach_uploaded_assets_to_quote(
    *,
    db: Session,
    quote: Quote,
    job: Job,
    capture_device: str,
    asset_refs: list[object],
) -> dict[str, int]:
    refs = _uploaded_asset_refs(asset_refs, db)
    if not refs:
        return {"quote_media": 0, "job_photos": 0}

    asset_ids = [int(ref["id"]) for ref in refs]
    asset_rows = db.query(UploadedAsset).filter(UploadedAsset.id.in_(asset_ids)).all()
    asset_map = {row.id: row for row in asset_rows}
    quote_media_saved = 0
    job_photos_saved = 0

    for ref in refs:
        asset = asset_map.get(int(ref["id"]))
        if asset is None:
            continue
        asset.parse_mode = _normalize_parse_mode(ref.get("parse_mode") or getattr(asset, "parse_mode", "auto"))
        asset.parse_result_json = json.dumps(ref.get("parser_result")) if isinstance(ref.get("parser_result"), dict) else None
        asset.upload_status = _normalize_upload_status(ref.get("status") or getattr(asset, "upload_status", "uploaded"))
        asset.error_message = str(ref.get("error") or "").strip() or None
        asset.quote_id = quote.id
        asset.job_id = job.id
        db.add(
            QuoteMedia(
                quote_id=quote.id,
                file_name=asset.file_name,
                content_type=asset.content_type,
                file_size=asset.file_size,
                media_kind=asset.media_kind,
                parse_mode=asset.parse_mode,
                parse_result_json=asset.parse_result_json,
                upload_status=asset.upload_status,
                error_message=asset.error_message,
                capture_device=(capture_device or "unknown")[:64],
                storage_path=asset.storage_path,
            )
        )
        quote_media_saved += 1
        if asset.category == "site_media" and asset.media_kind == "photo":
            db.add(
                JobPhoto(
                    job_id=job.id,
                    file_name=asset.file_name,
                    content_type=asset.content_type,
                    file_size=asset.file_size,
                    storage_path=asset.storage_path,
                )
            )
            job_photos_saved += 1

    return {"quote_media": quote_media_saved, "job_photos": job_photos_saved}


def _import_intake_media_records(
    *,
    db: Session,
    intake_submission_id: int,
    quote: Quote,
    job: Job,
) -> dict[str, int]:
    submission = db.get(IntakeSubmission, intake_submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer upload not found")

    rows = (
        db.query(IntakeMedia)
        .filter(IntakeMedia.intake_submission_id == submission.id)
        .order_by(IntakeMedia.created_at.asc(), IntakeMedia.id.asc())
        .all()
    )
    quote_media_saved = 0
    job_photos_saved = 0
    for row in rows:
        db.add(
            QuoteMedia(
                quote_id=quote.id,
                file_name=row.file_name,
                content_type=row.content_type,
                file_size=row.file_size,
                media_kind=row.media_kind,
                capture_device=(submission.capture_device or "customer_upload")[:64],
                storage_path=row.storage_path,
            )
        )
        quote_media_saved += 1
        if row.media_kind == "photo":
            db.add(
                JobPhoto(
                    job_id=job.id,
                    file_name=row.file_name,
                    content_type=row.content_type,
                    file_size=row.file_size,
                    storage_path=row.storage_path,
                )
            )
            job_photos_saved += 1
    return {"quote_media": quote_media_saved, "job_photos": job_photos_saved}


def _crm_rows(
    *,
    db: Session,
    limit: int,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
):
    safe_limit = max(1, min(limit, 200))
    requested_status = str(status_filter or "").strip().lower()
    if requested_status and requested_status not in LEAD_STATUS_OPTIONS:
        allowed = ", ".join(sorted(LEAD_STATUS_OPTIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of: {allowed}",
        )

    search_term = str(search or "").strip().lower()
    search_like = f"%{search_term}%"

    query = (
        db.query(Quote)
        .join(Job, Quote.job_id == Job.id)
        .options(
            selectinload(Quote.job),
            selectinload(Quote.items),
            selectinload(Quote.media),
        )
    )
    if search_term:
        filters = [
            func.lower(Job.customer_name).like(search_like),
            func.lower(func.coalesce(Job.phone, "")).like(search_like),
            func.lower(func.coalesce(Job.email, "")).like(search_like),
            func.lower(Job.address).like(search_like),
            func.lower(func.coalesce(Job.zip_code, "")).like(search_like),
        ]
        if search_term.isdigit():
            filters.append(Quote.id == int(search_term))
        query = query.filter(or_(*filters))

    fetch_limit = min(500, safe_limit * 5 if requested_status else safe_limit)
    quotes = (
        query
        .order_by(desc(Quote.created_at), desc(Quote.id))
        .limit(fetch_limit)
        .all()
    )
    quote_ids = [quote.id for quote in quotes]

    lead_by_quote_id: dict[int, Lead] = {}
    if quote_ids:
        linked_leads = (
            db.query(Lead)
            .filter(Lead.quote_id.in_(quote_ids))
            .order_by(desc(Lead.id))
            .all()
        )
        for lead in linked_leads:
            quote_id = getattr(lead, "quote_id", None)
            if quote_id and quote_id not in lead_by_quote_id:
                lead_by_quote_id[int(quote_id)] = lead

        missing_quote_ids = [quote_id for quote_id in quote_ids if quote_id not in lead_by_quote_id]
        if missing_quote_ids:
            page_urls = [f"/quotes/{quote_id}" for quote_id in missing_quote_ids]
            fallback_leads = (
                db.query(Lead)
                .filter(Lead.page_url.in_(page_urls))
                .order_by(desc(Lead.id))
                .all()
            )
            for lead in fallback_leads:
                quote_id = _extract_quote_id_from_page_url(lead.page_url)
                if quote_id and quote_id not in lead_by_quote_id:
                    lead_by_quote_id[quote_id] = lead

    latest_events_by_quote_id: dict[int, QuoteEvent] = {}
    if quote_ids:
        recent_events = (
            db.query(QuoteEvent)
            .filter(QuoteEvent.quote_id.in_(quote_ids))
            .order_by(desc(QuoteEvent.created_at), desc(QuoteEvent.id))
            .all()
        )
        for event in recent_events:
            if event.quote_id and event.quote_id not in latest_events_by_quote_id:
                latest_events_by_quote_id[int(event.quote_id)] = event

    today = date.today()
    payload = []
    for quote in quotes:
        linked_lead = lead_by_quote_id.get(quote.id)
        lead_status = linked_lead.status if linked_lead and linked_lead.status else quote.job.lead_status
        crm_status = _normalize_lead_status(lead_status, "quoted")

        if requested_status and crm_status != requested_status:
            continue

        sales_rep = (
            linked_lead.sales_rep
            if linked_lead and linked_lead.sales_rep
            else quote.job.sales_rep
        )
        follow_up_date = (
            linked_lead.follow_up_date
            if linked_lead and linked_lead.follow_up_date
            else quote.job.follow_up_date
        )
        latest_event = latest_events_by_quote_id.get(quote.id)

        follow_up_bucket = "none"
        if follow_up_date:
            if follow_up_date < today:
                follow_up_bucket = "overdue"
            elif follow_up_date == today:
                follow_up_bucket = "today"
            elif follow_up_date <= today + timedelta(days=2):
                follow_up_bucket = "upcoming"
            else:
                follow_up_bucket = "scheduled"

        payload.append(
            {
                "quote_id": quote.id,
                "created_at": quote.created_at,
                "customer_name": quote.job.customer_name,
                "phone": quote.job.phone,
                "email": quote.job.email,
                "address": quote.job.address,
                "zip_code": quote.job.zip_code,
                "status": crm_status,
                "sales_rep": sales_rep,
                "follow_up_date": follow_up_date,
                "follow_up_bucket": follow_up_bucket,
                "total": quote.total,
                "frequency": quote.frequency,
                "item_count": len(quote.items),
                "media_count": len(quote.media),
                "source": quote.job.source,
                "lead_id": linked_lead.id if linked_lead else None,
                "lead_notes": linked_lead.job_notes if linked_lead else None,
                "estimator_url": f"/staff-estimator?quote_id={quote.id}",
                "pdf_url": f"/quotes/{quote.id}/pdf",
                "text_url": f"/quotes/{quote.id}/text",
                "last_activity_at": latest_event.created_at if latest_event else quote.created_at,
                "last_activity_type": latest_event.event_name if latest_event else "quote_saved",
            }
        )
        if len(payload) >= safe_limit:
            break

    return payload


@app.get("/quotes", response_model=List[QuoteOut], dependencies=[Depends(require_api_key)])
def list_quotes(db: Session = Depends(get_db)):
    quotes = (
        db.query(Quote)
        .options(
            selectinload(Quote.job),
            selectinload(Quote.job).selectinload(Job.photos),
            selectinload(Quote.items),
            selectinload(Quote.media),
        )
        .order_by(desc(Quote.created_at), desc(Quote.id))
        .all()
    )
    return [_serialize_quote(quote) for quote in quotes]


@app.get("/quotes/crm", dependencies=[Depends(require_api_key)])
def list_quotes_crm(
    limit: int = 50,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return _crm_rows(db=db, limit=limit, status_filter=status_filter, search=search)


@app.get("/crm/dashboard", dependencies=[Depends(require_api_key)])
def crm_dashboard(search: Optional[str] = None, db: Session = Depends(get_db)):
    rows = _crm_rows(db=db, limit=200, status_filter=None, search=search)
    today = date.today()

    summary = {
        "total_open": 0,
        "new": 0,
        "measured": 0,
        "quoted": 0,
        "follow_up": 0,
        "won": 0,
        "lost": 0,
        "overdue_followups": 0,
        "today_followups": 0,
        "upcoming_followups": 0,
    }
    pipeline_groups = {
        "new": [],
        "measured": [],
        "quoted": [],
        "follow_up": [],
        "won": [],
        "lost": [],
    }
    recent_activity = []

    for row in rows:
        status_name = _normalize_lead_status(row.get("status"), "quoted")
        if status_name in pipeline_groups:
            pipeline_groups[status_name].append(row)
        summary[status_name] = summary.get(status_name, 0) + 1
        if status_name not in {"won", "lost"}:
            summary["total_open"] += 1

        bucket = row.get("follow_up_bucket")
        if bucket == "overdue":
            summary["overdue_followups"] += 1
        elif bucket == "today":
            summary["today_followups"] += 1
        elif bucket == "upcoming":
            summary["upcoming_followups"] += 1

        recent_activity.append(
            {
                "quote_id": row.get("quote_id"),
                "customer_name": row.get("customer_name"),
                "status": status_name,
                "activity_type": row.get("last_activity_type"),
                "activity_at": row.get("last_activity_at"),
                "follow_up_date": row.get("follow_up_date"),
                "estimator_url": row.get("estimator_url"),
            }
        )

    recent_activity.sort(key=lambda item: (item.get("activity_at") or datetime.min), reverse=True)

    return {
        "generated_on": today,
        "summary": summary,
        "pipeline": pipeline_groups,
        "recent_activity": recent_activity[:12],
        "queue": rows,
    }


@app.post("/quotes/preview", response_model=QuotePreviewOut, dependencies=[Depends(require_api_key)])
def preview_quote(payload: QuotePreviewCreate, db: Session = Depends(get_db)):
    pricing = calculate_quote(
        items=payload.items,
        frequency=payload.frequency,
        tax_rate=payload.tax_rate,
        zone_modifier_percent=payload.zone_modifier_percent,
    )

    _track_quote_event(
        db=db,
        event_name="preview_used",
        quote_id=None,
        metadata={
            "frequency": payload.frequency,
            "item_count": len(payload.items),
            "zone_modifier_percent": str(payload.zone_modifier_percent),
            "tax_rate": str(payload.tax_rate),
            "total": str(pricing["total"]),
        },
    )

    return {
        "frequency": payload.frequency,
        "zone_modifier_percent": payload.zone_modifier_percent,
        "items": pricing["items"],
        "subtotal": pricing["subtotal"],
        "zone_adjustment": pricing["zone_adjustment"],
        "frequency_discount_percent": pricing["frequency_discount_percent"],
        "discount_amount": pricing["discount_amount"],
        "tax_rate": pricing["tax_rate"],
        "tax_amount": pricing["tax_amount"],
        "total": pricing["total"],
    }


@app.post("/quotes/ai-preview", dependencies=[Depends(require_api_key)])
def ai_preview_quote(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    uploaded_asset_refs = _uploaded_asset_refs(payload.get("uploaded_assets") or [], db)
    raw_photos = payload.get("photos") or []
    lidar_files = payload.get("lidar_files") or []
    raw_measurement_photos = payload.get("measurement_photos") or []
    frequency = str(payload.get("frequency") or "monthly")
    tax_rate = str(payload.get("tax_rate") or "0")
    zone_modifier_percent = str(payload.get("zone_modifier_percent") or "0")
    lot_size_sqft = str(payload.get("area_sqft") or payload.get("lot_size_sqft") or "")
    edge_linear_ft = str(payload.get("edge_linear_ft") or "")
    turf_condition = str(payload.get("turf_condition") or "average")
    obstacles_count = str(payload.get("obstacles_count") or "0")
    slope = str(payload.get("slope") or "flat")
    debris_level = str(payload.get("debris_level") or "low")
    has_gates = str(payload.get("has_gates") or "")
    include_haulaway = str(payload.get("include_haulaway") or "")
    include_blowing = payload.get("include_blowing")
    include_blowing = None if include_blowing is None else str(include_blowing)
    material_type = str(payload.get("material_type") or BLOWER_DEFAULT_MATERIAL)
    material_yards_raw = str(payload.get("material_yards") or "")
    material_depth_inches_raw = str(payload.get("material_depth_inches") or "")
    delivery_truck_type = BLOWER_DEFAULT_TRUCK_TYPE
    placement_method = BLOWER_DEFAULT_PLACEMENT_METHOD
    customer_address = str(payload.get("customer_address") or "")
    capture_device = str(payload.get("capture_device") or "other")
    terrain_type = str(payload.get("terrain_type") or "mixed")
    zip_code = str(payload.get("zip_code") or "")
    job_notes = str(payload.get("notes") or "")
    exclusions = str(payload.get("exclusions") or "")
    primary_job_type = str(payload.get("primary_job_type") or "").strip().lower() or None
    confirmed_measurement_entries = payload.get("measurement_entries") or None
    force_measurement_reference_mode = _to_bool(payload.get("force_measurement_reference_mode"))
    frequency = _normalize_choice(frequency, AI_FREQUENCIES, "monthly")
    turf_condition = _normalize_choice(turf_condition, AI_TURF_CONDITIONS, "average")
    slope = _normalize_choice(slope, AI_SLOPE_OPTIONS, "flat")
    debris_level = _normalize_choice(debris_level, AI_DEBRIS_LEVELS, "low")
    terrain_type = _normalize_choice(terrain_type, AI_TERRAIN_OPTIONS, "mixed")

    lot_size = _to_decimal_or_default(lot_size_sqft, Decimal("0"))
    edge_length = _to_decimal_or_default(edge_linear_ft, Decimal("0"))
    material_yards = _to_decimal_or_default(material_yards_raw, Decimal("0"))
    material_depth_inches = _to_decimal_or_default(material_depth_inches_raw, Decimal("0"))
    obstacle_count = max(int(_to_decimal_or_default(obstacles_count, Decimal("0"))), 0)
    tax_rate_dec = _to_decimal_or_default(tax_rate, Decimal("0"))
    zone_modifier_dec = _to_decimal_or_default(zone_modifier_percent, Decimal("0"))
    has_gates_bool = _to_bool(has_gates)
    include_haulaway_bool = _to_bool(include_haulaway)
    include_blowing_bool = _to_bool(include_blowing) if include_blowing is not None else True
    material_type = _normalize_blower_material(material_type)
    zip_code = _extract_zip_code(customer_address, zip_code)

    def _normalize_media_files(files: list[object], default_category: str) -> list[dict]:
        normalized_files: list[dict] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            record["parse_mode"] = _normalize_parse_mode(record.get("parse_mode"))
            record["category"] = _normalize_media_category(record.get("category"), default_category)
            normalized_files.append(record)
        return normalized_files

    photos = _normalize_media_files(raw_photos, "site_media")
    measurement_photos = _normalize_media_files(raw_measurement_photos, "measurement_note")
    for asset in uploaded_asset_refs:
        category = str(asset.get("category") or "site_media")
        if category == "measurement_note":
            measurement_photos.append(dict(asset))
            continue
        if str(asset.get("media_kind") or "").strip().lower() == "lidar_scan":
            lidar_files.append(dict(asset))
        photos.append(dict(asset))

    estimate_photos: list[dict] = []
    measurement_reference_inputs: list[dict] = []

    for item in photos:
        category = str(item.get("category") or "site_media")
        parse_mode = _normalize_site_media_parse_mode(item.get("parse_mode"))
        if category == "exclusion":
            continue
        if force_measurement_reference_mode or parse_mode == "force_measurement_note":
            measurement_reference_inputs.append(dict(item, parse_mode="force_measurement_note"))
            continue
        estimate_photos.append(dict(item, parse_mode=parse_mode))

    for item in measurement_photos:
        category = str(item.get("category") or "measurement_note")
        if category == "exclusion":
            continue
        measurement_reference_inputs.append(
            dict(item, parse_mode=_normalize_parse_mode(item.get("parse_mode")))
        )

    measurement_reference_mode = bool(measurement_reference_inputs)

    for asset in uploaded_asset_refs:
        logger.info(
            "parse_started asset_id=%s category=%s mode=%s status=%s",
            asset.get("id"),
            asset.get("category"),
            asset.get("parse_mode"),
            asset.get("status"),
        )

    photo_tags = _infer_photo_tags(estimate_photos)
    required_photo_angles = ["front", "back", "left", "right"]
    covered_angles = [tag for tag in required_photo_angles if tag in photo_tags]
    missing_angles = [tag for tag in required_photo_angles if tag not in photo_tags]

    try:
        ai_result = estimate_job(
            lot_size_sqft=lot_size,
            edge_length=edge_length,
            terrain_type=terrain_type,
            zip_code=zip_code,
            uploaded_images=estimate_photos,
            measurement_reference_images=measurement_reference_inputs,
            job_notes=job_notes,
            exclusions=exclusions,
            material_type=material_type,
            placement_method=placement_method,
            obstacle_count=obstacle_count,
            include_haulaway=include_haulaway_bool,
            has_gates=has_gates_bool,
            primary_job_type_override=primary_job_type,
            material_depth_inches=material_depth_inches if material_depth_inches > 0 else None,
            confirmed_measurement_entries=confirmed_measurement_entries,
            allow_site_media_measurement_reference_detection=False,
        )
    except Exception as exc:
        logger.exception(
            "ai_preview_parse_failed photos=%s measurement_refs=%s exception=%s",
            [str((item or {}).get("file_name") or (item or {}).get("filename") or "") for item in estimate_photos],
            [str((item or {}).get("file_name") or (item or {}).get("filename") or "") for item in measurement_reference_inputs],
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": f"Measurement parsing failed: {exc}",
                "reason_code": "measurement_parse_exception",
            },
        ) from exc
    parse_classification = str(((ai_result.get("measurement_parse") or {}).get("classification")) or "").strip().lower()
    extraction_meta = ai_result.get("extraction_meta") or {}
    trusted_measurements_available = bool(extraction_meta.get("trusted_measurements_available"))
    combined_bed_material_yards = _to_decimal_or_default(str(ai_result.get("combined_bed_material_yards") or ""), Decimal("0"))
    force_scene_photo_mode = any(
        str((item or {}).get("parse_mode") or "auto").strip().lower() == "force_scene_photo"
        for item in estimate_photos
        if isinstance(item, dict)
    )
    block_scene_photo_material_autofill = (
        parse_classification == "scene_photo_estimation"
        and not trusted_measurements_available
        and combined_bed_material_yards <= 0
        and not force_scene_photo_mode
    )
    if parse_classification in {"exact_measurement_note", "failed_ocr_unreadable_note"}:
        measurement_reference_mode = True
    if lot_size <= 0:
        lot_size = _to_decimal_or_default(str(ai_result.get("area_sqft") or ""), Decimal("0"))
    if edge_length <= 0:
        edge_length = _to_decimal_or_default(str(ai_result.get("edge_length_ft") or ""), Decimal("0"))
    if material_yards <= 0 and not block_scene_photo_material_autofill:
        material_yards = _to_decimal_or_default(str(ai_result.get("recommended_material_yards") or ""), Decimal("0"))
    if material_yards <= 0 and not measurement_reference_mode and not block_scene_photo_material_autofill:
        material_yards = max(
            Decimal("0.25"),
            (max(lot_size, Decimal("1")) / Decimal("1200")).quantize(Decimal("0.01")),
        )
    ai_items = _build_detected_task_items(ai_result.get("task_breakdown") or [])

    pricing = calculate_quote(
        items=ai_items,
        frequency=frequency,
        tax_rate=tax_rate_dec,
        zone_modifier_percent=zone_modifier_dec,
    )

    dimension_area = _to_decimal_or_default(
        str((ai_result.get("dimension_observations") or {}).get("estimated_area_sqft") or ""),
        Decimal("0"),
    )
    combined_bed_area = _to_decimal_or_default(str(ai_result.get("combined_bed_area_sqft") or ""), Decimal("0"))
    bed_groups = ai_result.get("bed_groups") or []
    measurement_entries = ai_result.get("measurement_entries") or []
    confidence = _estimate_ai_preview_confidence(
        photo_count=len(estimate_photos),
        covered_angle_count=len(covered_angles),
        lidar_count=len(lidar_files),
        capture_device=capture_device,
        dimension_area=dimension_area,
        combined_bed_area=combined_bed_area,
        lot_size_present=bool(lot_size_sqft),
        edge_length_present=bool(edge_linear_ft),
        bed_group_count=len(bed_groups),
        measurement_entry_count=len(measurement_entries),
        trusted_measurements_available=trusted_measurements_available,
        openai_used=bool(extraction_meta.get("openai_used")),
    )

    framed_inputs = [
        {"key": "lot_size_sqft", "label": "Approx lot size (sq ft)", "required_for_high_confidence": True},
        {"key": "edge_linear_ft", "label": "Approx edge length (linear ft)", "required_for_high_confidence": True},
        {"key": "turf_condition", "label": "Turf condition (healthy/average/overgrown)", "required_for_high_confidence": True},
        {"key": "slope", "label": "Slope (flat/mild/steep)", "required_for_high_confidence": True},
        {"key": "obstacles_count", "label": "Obstacle count (beds, trees, furniture zones)", "required_for_high_confidence": True},
        {"key": "debris_level", "label": "Debris level (low/medium/high)", "required_for_high_confidence": False},
        {"key": "material_type", "label": "Blowable material", "required_for_high_confidence": True},
        {"key": "material_yards", "label": "Material needed (cu yd)", "required_for_high_confidence": False},
        {"key": "material_depth_inches", "label": "Material depth (in)", "required_for_high_confidence": False},
        {"key": "has_gates", "label": "Gate access constraints", "required_for_high_confidence": False},
        {"key": "include_haulaway", "label": "Include haul-away service", "required_for_high_confidence": False},
    ]

    response_payload = {
        "model": "demo-photo-estimator-v1",
        "summary": "AI draft generated from framed site inputs + uploaded media.",
        "confidence": confidence,
        "review_required": confidence < 0.85,
        "framed_inputs": framed_inputs,
        "provided_inputs": {
            "lot_size_sqft": str(lot_size),
            "area_sqft": str(lot_size),
            "edge_linear_ft": str(edge_length),
            "turf_condition": turf_condition,
            "slope": slope,
            "terrain_type": terrain_type,
            "zip_code": zip_code,
            "obstacles_count": obstacle_count,
            "debris_level": debris_level,
            "has_gates": has_gates_bool,
            "include_haulaway": include_haulaway_bool,
            "include_blowing": include_blowing_bool,
            "material_type": material_type,
            "material_yards": str(material_yards),
            "material_depth_inches": str(ai_result.get("measurement_defaults", {}).get("material_depth_inches") or ""),
            "delivery_truck_type": delivery_truck_type,
            "placement_method": placement_method,
            "customer_address": customer_address,
            "capture_device": capture_device,
        },
        "photo_checklist": {
            "required_angles": required_photo_angles,
            "covered_angles": covered_angles,
            "missing_angles": missing_angles,
            "photo_count": len(estimate_photos),
            "lidar_count": len(lidar_files),
        },
        "frequency": frequency,
        "zone_modifier_percent": zone_modifier_dec,
        "items": pricing["items"],
        "subtotal": pricing["subtotal"],
        "zone_adjustment": pricing["zone_adjustment"],
        "frequency_discount_percent": pricing["frequency_discount_percent"],
        "discount_amount": pricing["discount_amount"],
        "tax_rate": pricing["tax_rate"],
        "tax_amount": pricing["tax_amount"],
        "total": pricing["total"],
        "estimated_labor_hours": ai_result["estimated_labor_hours"],
        "material_cost": ai_result["material_cost"],
        "equipment_cost": ai_result["equipment_cost"],
        "suggested_price": ai_result["suggested_price"],
        "recommended_crew_size": ai_result["recommended_crew_size"],
        "estimated_duration_hours": ai_result["estimated_duration_hours"],
        "crew_instructions": ai_result["crew_instructions"],
        "primary_job_type": ai_result["primary_job_type"],
        "detected_tasks": ai_result["detected_tasks"],
        "detected_zones": ai_result.get("detected_zones") or [],
        "zone_summary": ai_result.get("zone_summary") or "",
        "measurement_entries": measurement_entries,
        "measurement_parse": ai_result.get("measurement_parse") or {},
        "bed_groups": bed_groups,
        "combined_bed_area_sqft": ai_result.get("combined_bed_area_sqft"),
        "combined_bed_material_yards": ai_result.get("combined_bed_material_yards"),
        "task_breakdown": ai_result["task_breakdown"],
        "dimension_observations": ai_result.get("dimension_observations"),
        "missing_angle_estimate": ai_result.get("missing_angle_estimate"),
        "extraction_meta": extraction_meta,
        "notes_for_staff": [
            "This is a demo AI draft. Staff should verify dimensions before final save.",
            "Missing required photo angles reduce confidence.",
            "When angles are missing, review inferred width/depth estimates before final quote.",
            "If photo measurements are detected, area/material are auto-filled from those dimensions.",
            "Handwritten measurement notes and iPhone Measurement screenshots should always be confirmed before sending the final quote.",
        ],
    }
    logger.info(
        "measurement_rows_mapped total_rows=%s exact_rows=%s classification=%s",
        len(measurement_entries),
        sum(
            1
            for entry in measurement_entries
            if isinstance(entry, dict)
            and entry.get("inferred_from_photo_estimate") is not True
            and str(entry.get("entry_type") or "").strip().lower() == "dimension_pair"
        ),
        response_payload["measurement_parse"].get("classification"),
    )
    if uploaded_asset_refs:
        parser_snapshot = {
            "provided_inputs": response_payload["provided_inputs"],
            "estimated_labor_hours": response_payload["estimated_labor_hours"],
            "material_cost": response_payload["material_cost"],
            "equipment_cost": response_payload["equipment_cost"],
            "suggested_price": response_payload["suggested_price"],
            "primary_job_type": response_payload["primary_job_type"],
            "detected_tasks": response_payload["detected_tasks"],
            "detected_zones": response_payload["detected_zones"],
            "measurement_entries": response_payload["measurement_entries"],
            "measurement_parse": response_payload["measurement_parse"],
            "bed_groups": response_payload["bed_groups"],
            "task_breakdown": response_payload["task_breakdown"],
            "combined_bed_area_sqft": response_payload["combined_bed_area_sqft"],
            "combined_bed_material_yards": response_payload["combined_bed_material_yards"],
            "recommended_material_yards": ai_result.get("recommended_material_yards"),
            "dimension_observations": response_payload["dimension_observations"],
            "missing_angle_estimate": response_payload["missing_angle_estimate"],
            "extraction_meta": response_payload["extraction_meta"],
            "crew_instructions": response_payload["crew_instructions"],
            "review_required": response_payload["review_required"],
        }
        asset_rows = db.query(UploadedAsset).filter(UploadedAsset.id.in_([int(item["id"]) for item in uploaded_asset_refs])).all()
        asset_map = {row.id: row for row in asset_rows}
        for item in uploaded_asset_refs:
            row = asset_map.get(int(item["id"]))
            if row is None:
                continue
            row.parse_mode = _normalize_parse_mode(item.get("parse_mode") or getattr(row, "parse_mode", "auto"))
            row.parse_result_json = json.dumps(parser_snapshot, default=str)
            row.upload_status = "ready"
            row.error_message = None
        db.commit()

    _track_quote_event(
        db=db,
        event_name="ai_preview_used",
        quote_id=None,
        metadata={
            "photo_count": len(estimate_photos),
            "lidar_count": len(lidar_files),
            "frequency": frequency,
            "confidence": confidence,
        },
    )

    return response_payload


@app.post("/quotes", response_model=QuoteOut, dependencies=[Depends(require_api_key)])
def create_quote(payload: QuoteCreate, db: Session = Depends(get_db)):
    logger.info(
        "quote_save_started customer_name=%s address_present=%s item_count=%s frequency=%s total_preview_items=%s",
        getattr(payload.job, "customer_name", None),
        bool(getattr(payload.job, "address", None)),
        len(payload.items or []),
        payload.frequency,
        [
            {
                "name": item.name,
                "unit": item.unit,
                "quantity": str(item.quantity),
                "base_price": str(item.base_price),
            }
            for item in payload.items
        ],
    )
    job_data = _payload_to_dict(payload.job)
    customer_name = str(job_data.get("customer_name") or "").strip()
    address = str(job_data.get("address") or "").strip()
    if not customer_name or not address:
        logger.warning(
            "save_blocked_missing_customer_info customer_name_present=%s address_present=%s",
            bool(customer_name),
            bool(address),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add customer name and address before saving the sales quote and customer PDF.",
        )
    job_data["customer_name"] = customer_name
    job_data["address"] = address
    job_email = (job_data.get("email") or "").strip().lower()
    if job_email and not EMAIL_REGEX.match(job_email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Valid email is required")
    job_data["email"] = job_email or None
    job_data["sales_rep"] = (job_data.get("sales_rep") or "").strip() or None
    job_data["internal_notes"] = (job_data.get("internal_notes") or "").strip() or None
    job_data["zip_code"] = (_normalize_zip_code(job_data.get("zip_code"), address=job_data.get("address") or "") or None)
    job_data["terrain_type"] = _normalize_choice(job_data.get("terrain_type"), AI_TERRAIN_OPTIONS, "mixed")
    job_data["lead_status"] = _normalize_lead_status(job_data.get("lead_status"), "quoted")
    job_data["primary_job_type"] = (job_data.get("primary_job_type") or "").strip().lower() or None
    detected_tasks = _normalize_detected_tasks(job_data.pop("detected_tasks", None))
    job_data["detected_tasks_json"] = json.dumps(detected_tasks) if detected_tasks else None
    if not job_data["primary_job_type"] and detected_tasks:
        job_data["primary_job_type"] = str(detected_tasks[0].get("job_type") or "").strip().lower() or None
    capture_device = str(job_data.get("capture_device") or "unknown")
    requested_contact_id = str(getattr(payload, "contact_id", None) or "").strip() or None
    if requested_contact_id:
        crm_contact = db.query(crm_service.CrmContact).filter(crm_service.CrmContact.id == requested_contact_id).first()
        if not crm_contact:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
        if not job_data.get("phone") and not str(crm_contact.mobile_phone or "").startswith(("email:", "name:")):
            job_data["phone"] = crm_contact.mobile_phone
        if not job_data.get("email") and crm_contact.email:
            job_data["email"] = crm_contact.email
        if job_data["customer_name"] == customer_name and crm_contact.display_name:
            job_data["customer_name"] = crm_contact.display_name
        contact_id = crm_contact.id
    else:
        crm_contact = crm_service.resolve_contact(
            db,
            phone=job_data.get("phone"),
            name=job_data.get("customer_name"),
            email=job_data.get("email"),
        )
        contact_id = crm_contact.id

    job = Job(**job_data)
    db.add(job)
    db.flush()

    pricing = calculate_quote(
        items=payload.items,
        frequency=payload.frequency,
        tax_rate=payload.tax_rate,
        zone_modifier_percent=payload.zone_modifier_percent,
    )

    quote = Quote(
        job_id=job.id,
        contact_id=contact_id,
        frequency=payload.frequency,
        tax_rate=pricing["tax_rate"],
        zone_modifier_percent=payload.zone_modifier_percent,
        frequency_discount_percent=pricing["frequency_discount_percent"],
        subtotal=pricing["subtotal"],
        zone_adjustment=pricing["zone_adjustment"],
        discount_amount=pricing["discount_amount"],
        tax_amount=pricing["tax_amount"],
        total=pricing["total"],
    )
    db.add(quote)
    db.flush()

    for item in pricing["items"]:
        db.add(
            QuoteItem(
                quote_id=quote.id,
                name=item["name"],
                description=item.get("description"),
                quantity=item["quantity"],
                unit=item["unit"],
                base_price=item["base_price"],
                per_unit_price=item["per_unit_price"],
                min_charge=item["min_charge"],
                line_total=item["line_total"],
            )
        )

    lead_notes = _compose_lead_notes(job)
    lead = Lead(
        name=job.customer_name,
        customer_name=job.customer_name,
        email=job.email or f"lead-{job.id}@barkboys.local",
        phone=job.phone,
        address=job.address,
        sales_rep=job.sales_rep,
        follow_up_date=job.follow_up_date,
        quote_amount=pricing["total"],
        quote_id=quote.id,
        job_notes=lead_notes or None,
        status=job.lead_status or "quoted",
        message=lead_notes or None,
        page_url=f"/quotes/{quote.id}",
        created_at=datetime.utcnow(),
        submitted_at=datetime.utcnow(),
    )
    db.add(lead)
    crm_service.create_activity(
        db,
        contact_id=contact_id,
        related_type="quote",
        related_id=str(quote.id),
        activity_type="quote.saved",
        title="Quote saved",
        body=f"Quote #{quote.id} saved.",
        metadata={"quote_id": quote.id, "total": str(pricing["total"])},
    )

    uploaded_asset_counts = _attach_uploaded_assets_to_quote(
        db=db,
        quote=quote,
        job=job,
        capture_device=capture_device,
        asset_refs=list(payload.uploaded_assets or []),
    )
    intake_counts = {"quote_media": 0, "job_photos": 0}
    if payload.intake_submission_id:
        intake_counts = _import_intake_media_records(
            db=db,
            intake_submission_id=int(payload.intake_submission_id),
            quote=quote,
            job=job,
        )

    db.commit()

    _track_quote_event(
        db=db,
        event_name="quote_saved",
        quote_id=quote.id,
        metadata={
            "frequency": payload.frequency,
            "item_count": len(payload.items),
            "customer_name": payload.job.customer_name,
            "total": str(pricing["total"]),
            "uploaded_asset_count": uploaded_asset_counts["quote_media"],
            "uploaded_job_photo_count": uploaded_asset_counts["job_photos"],
            "intake_media_count": intake_counts["quote_media"],
            "intake_job_photo_count": intake_counts["job_photos"],
        },
    )

    stored_quote = _get_quote_or_404(quote.id, db)
    logger.info(
        "quote_save_completed quote_id=%s total=%s item_count=%s media_count=%s",
        quote.id,
        pricing["total"],
        len(payload.items),
        len(stored_quote.media or []),
    )
    return _serialize_quote(stored_quote)


@app.get("/quotes/{quote_id}", response_model=QuoteOut, dependencies=[Depends(require_api_key)])
def get_quote(quote_id: int, db: Session = Depends(get_db)):
    quote = _get_quote_or_404(quote_id, db)
    return _serialize_quote(quote)


@app.post("/jobs/{job_id}/photos", dependencies=[Depends(require_api_key)])
def upload_job_photos(
    job_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    files = payload.get("files") or []
    job = _get_job_or_404(job_id, db)

    stored_files = []
    for file in _decode_payload_files(files, "photo"):
        raw = file["raw"]
        safe_name = file["file_name"]
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        final_name = f"{stamp}_{safe_name}"
        storage_path = storage.save_bytes(
            f"{job.id}/{final_name}",
            raw,
            content_type=file["content_type"],
        )

        photo = JobPhoto(
            job_id=job.id,
            file_name=safe_name,
            content_type=file["content_type"],
            file_size=len(raw),
            storage_path=storage_path,
        )
        db.add(photo)
        stored_files.append(
            {
                "id": None,
                "file_name": safe_name,
                "content_type": file["content_type"],
                "file_size": len(raw),
                "storage_path": storage_path,
            }
        )

    db.commit()

    _track_quote_event(
        db=db,
        event_name="job_photos_uploaded",
        quote_id=None,
        metadata={"job_id": job.id, "count": len(stored_files)},
    )

    return {"job_id": job.id, "saved": len(stored_files), "files": stored_files}


@app.post("/jobs/{job_id}/import-intake-media", dependencies=[Depends(require_api_key)])
def import_intake_media_to_job(
    job_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    intake_submission_id = int(payload.get("intake_submission_id") or 0)
    if intake_submission_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valid intake_submission_id is required",
        )

    job = _get_job_or_404(job_id, db)
    submission = db.get(IntakeSubmission, intake_submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intake submission not found")

    media_rows = (
        db.query(IntakeMedia)
        .filter(IntakeMedia.intake_submission_id == submission.id)
        .order_by(IntakeMedia.id.asc())
        .all()
    )

    imported = []

    for row in media_rows:
        if row.media_kind != "photo":
            continue

        safe_name = _safe_upload_name(row.file_name)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        final_name = f"{stamp}_{safe_name}"
        destination_path = storage.copy_into(
            row.storage_path,
            f"{job.id}/{final_name}",
            content_type=row.content_type or "",
        )

        photo = JobPhoto(
            job_id=job.id,
            file_name=safe_name,
            content_type=row.content_type,
            file_size=row.file_size,
            storage_path=destination_path,
        )
        db.add(photo)
        imported.append(
            {
                "file_name": photo.file_name,
                "storage_path": photo.storage_path,
            }
        )

    db.commit()

    _track_quote_event(
        db=db,
        event_name="intake_media_imported_to_job",
        quote_id=None,
        metadata={"job_id": job.id, "intake_submission_id": submission.id, "count": len(imported)},
    )

    return {
        "job_id": job.id,
        "intake_submission_id": submission.id,
        "saved": len(imported),
        "files": imported,
    }


@app.post("/quotes/{quote_id}/media", dependencies=[Depends(require_api_key)])
def upload_quote_media(
    quote_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    files = payload.get("files") or []
    media_kind = str(payload.get("media_kind") or "photo")
    capture_device = str(payload.get("capture_device") or "unknown")
    quote = _get_quote_or_404(quote_id, db)

    stored_files = []
    for file in _decode_payload_files(files, media_kind):
        raw = file["raw"]
        safe_name = file["file_name"]
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        final_name = f"{stamp}_{safe_name}"
        storage_path = storage.save_bytes(
            f"quote-{quote.id}/{final_name}",
            raw,
            content_type=file["content_type"],
        )

        media = QuoteMedia(
            quote_id=quote.id,
            file_name=safe_name,
            content_type=file["content_type"],
            file_size=len(raw),
            media_kind=(media_kind or "photo")[:32],
            capture_device=(capture_device or "unknown")[:64],
            storage_path=storage_path,
        )
        db.add(media)
        stored_files.append(
            {
                "id": None,
                "file_name": safe_name,
                "content_type": file["content_type"],
                "file_size": len(raw),
                "media_kind": media.media_kind,
                "capture_device": media.capture_device,
                "storage_path": storage_path,
            }
        )

    db.commit()

    _track_quote_event(
        db=db,
        event_name="media_uploaded",
        quote_id=quote.id,
        metadata={
            "media_kind": media_kind,
            "capture_device": capture_device,
            "count": len(stored_files),
        },
    )

    return {"quote_id": quote.id, "saved": len(stored_files), "files": stored_files}


@app.post("/quotes/{quote_id}/import-intake-media", dependencies=[Depends(require_api_key)])
def import_intake_media_to_quote(
    quote_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    intake_submission_id = int(payload.get("intake_submission_id") or 0)
    if intake_submission_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valid intake_submission_id is required",
        )

    quote = _get_quote_or_404(quote_id, db)
    submission = db.get(IntakeSubmission, intake_submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intake submission not found")

    media_rows = (
        db.query(IntakeMedia)
        .filter(IntakeMedia.intake_submission_id == submission.id)
        .order_by(IntakeMedia.id.asc())
        .all()
    )

    imported = []

    for row in media_rows:
        safe_name = _safe_upload_name(row.file_name)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        final_name = f"{stamp}_{safe_name}"
        destination_path = storage.copy_into(
            row.storage_path,
            f"quote-{quote.id}/{final_name}",
            content_type=row.content_type or "",
        )

        media = QuoteMedia(
            quote_id=quote.id,
            file_name=safe_name,
            content_type=row.content_type,
            file_size=row.file_size,
            media_kind=row.media_kind,
            capture_device=(submission.capture_device or "customer_upload")[:64],
            storage_path=destination_path,
        )
        db.add(media)
        imported.append(
            {
                "file_name": media.file_name,
                "media_kind": media.media_kind,
                "storage_path": media.storage_path,
            }
        )

    submission.status = "quoted" if imported else submission.status
    db.commit()

    _track_quote_event(
        db=db,
        event_name="intake_media_imported",
        quote_id=quote.id,
        metadata={
            "intake_submission_id": submission.id,
            "count": len(imported),
        },
    )

    return {
        "quote_id": quote.id,
        "intake_submission_id": submission.id,
        "saved": len(imported),
        "files": imported,
    }


@app.get("/quotes/{quote_id}/text", response_model=QuoteTextOut, dependencies=[Depends(require_api_key)])
def get_quote_text(quote_id: int, db: Session = Depends(get_db)):
    quote = _get_quote_or_404(quote_id, db)
    serialized = _serialize_quote(quote)
    return {"quote_id": quote.id, "text_quote": serialized["text_quote"]}


@app.get("/quotes/{quote_id}/pdf", dependencies=[Depends(require_api_key)])
def get_quote_pdf(quote_id: int, db: Session = Depends(get_db)):
    quote = _get_quote_or_404(quote_id, db)
    serialized = _serialize_quote(quote)
    try:
        pdf_data = build_quote_pdf(serialized)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    _track_quote_event(
        db=db,
        event_name="pdf_downloaded",
        quote_id=quote.id,
        metadata={"filename": f"quote-{quote.id}.pdf"},
    )

    return Response(
        content=pdf_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="quote-{quote.id}.pdf"'},
    )
