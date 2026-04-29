import base64
import io
import json
import tempfile
import unittest
import os
import asyncio
import re
import subprocess
import urllib.error
from unittest import mock
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import (
    ai_preview_quote,
    build_ai_draft_from_intake,
    create_intake_submission,
    create_quote,
    get_quote,
    get_quote_pdf,
    import_intake_media_to_job,
    import_intake_media_to_quote,
    list_quotes_crm,
    parse_measurement_note,
    list_uploaded_assets,
    parse_handwritten_test,
    parse_uploaded_measurement_asset,
    upload_dimension_assets,
    upload_measurement_note_panel_assets,
    use_uploaded_asset_as_measurement_note,
    upload_exclusion_assets,
    upload_quote_media,
    upload_site_media_assets,
    upload_measurement_note_assets,
    uploaded_asset_content,
    ui_build_health,
)
from app.models import IntakeSubmission, JobPhoto, Lead, Quote, QuoteEvent, QuoteMedia, UploadedAsset
from app.schemas import QuoteCreate, QuoteItemInput, UploadedAssetRef
import app.ai_photo_analysis as ai_photo_analysis_module
import app.ai_estimator as ai_estimator_module
import app.main as main_module
from app.ai_photo_analysis import (
    _extract_measurement_entries,
    _merge_ocr_measurement_entries,
    _normalize_openai_measurement_rows,
    _select_best_ocr_text,
    analyze_uploaded_images,
)
from app.storage import StorageManager
from app.settings import Settings
from modules.communication_crm import lead_monitor_service
from modules.communication_crm.models import CrmActivity, CrmContact, CrmLeadSignal, CrmMessage


class BarkboysRegressionTests(unittest.TestCase):
    VALID_PNG_BYTES = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pdp0AAAAASUVORK5CYII="
    )
    ESTIMATOR_HTML_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "estimator.html"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.uploads_path = Path(self.temp_dir.name) / "uploads"
        self.uploads_path.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

        self.original_uploads_path = main_module.storage.settings.uploads_path
        self.original_storage_backend = main_module.storage.settings.storage_backend
        self.original_uploads_prefix = main_module.storage.settings.uploads_prefix
        main_module.storage.settings.uploads_path = str(self.uploads_path)
        main_module.storage.settings.storage_backend = "local"
        main_module.storage.settings.uploads_prefix = ""

    def tearDown(self) -> None:
        main_module.storage.settings.uploads_path = self.original_uploads_path
        main_module.storage.settings.storage_backend = self.original_storage_backend
        main_module.storage.settings.uploads_prefix = self.original_uploads_prefix
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _payload_file(self, file_name: str, raw: bytes, content_type: str) -> dict:
        return {
            "file_name": file_name,
            "content_type": content_type,
            "data_base64": base64.b64encode(raw).decode("ascii"),
        }

    def _quote_create_from_draft(self, draft: dict) -> QuoteCreate:
        job = draft["job"]
        return QuoteCreate(
            job={
                "customer_name": job["customer_name"],
                "phone": job["phone"],
                "address": job["address"],
                "zip_code": job["zip_code"],
                "area_sqft": job["area_sqft"],
                "terrain_type": job["terrain_type"],
                "primary_job_type": job["primary_job_type"],
                "detected_tasks": job["detected_tasks"],
                "notes": job["notes"],
                "crew_instructions": job["crew_instructions"],
                "estimated_labor_hours": job["estimated_labor_hours"],
                "material_cost": job["material_cost"],
                "equipment_cost": job["equipment_cost"],
                "suggested_price": job["suggested_price"],
                "source": job["source"],
            },
            items=[
                QuoteItemInput(
                    name=item["name"],
                    quantity=item["quantity"],
                    unit=item["unit"],
                    base_price=item["base_price"],
                    per_unit_price=item["per_unit_price"],
                    min_charge=item["min_charge"],
                )
                for item in draft["items"]
            ],
            frequency=draft["frequency"],
            tax_rate=draft["tax_rate"],
            zone_modifier_percent=draft["zone_modifier_percent"],
        )

    def _upload_file(self, filename: str, raw: bytes, content_type: str) -> UploadFile:
        return UploadFile(file=io.BytesIO(raw), filename=filename, headers=Headers({"content-type": content_type}))

    def _estimator_source(self, start_marker: str, end_marker: str) -> str:
        html = self.ESTIMATOR_HTML_PATH.read_text()
        start = html.index(start_marker)
        end = html.index(end_marker, start)
        return html[start:end]

    def test_core_crm_communication_flow(self) -> None:
        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main_module.app.dependency_overrides[main_module.get_db] = override_get_db
        try:
            client = TestClient(main_module.app)

            inbound = client.post(
                "/api/inbound",
                json={"phone": "(503) 555-0199", "name": "Sam Rivera", "message": "Can I get a quote?"},
            )
            self.assertEqual(inbound.status_code, 200)
            inbound_payload = inbound.json()
            self.assertEqual(inbound_payload["status"], "received")
            self.assertTrue(inbound_payload["contact_id"])
            self.assertTrue(inbound_payload["message_id"])

            with self.SessionLocal() as db:
                contact = db.query(CrmContact).filter(CrmContact.id == inbound_payload["contact_id"]).one()
                self.assertEqual(contact.display_name, "Sam Rivera")
                self.assertEqual(contact.mobile_phone, "+15035550199")

            first_conversation = client.get(f"/api/conversations/{inbound_payload['contact_id']}")
            self.assertEqual(first_conversation.status_code, 200)
            first_messages = first_conversation.json()
            self.assertEqual(len(first_messages), 2)
            self.assertEqual(first_messages[0]["direction"], "inbound")
            self.assertEqual(first_messages[0]["message"], "Can I get a quote?")
            self.assertEqual(first_messages[1]["direction"], "outbound")
            self.assertIn("Got it", first_messages[1]["message"])

            reply = client.post(
                "/api/reply",
                json={"contact_id": inbound_payload["contact_id"], "message": "Yes, we can help."},
            )
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(reply.json()["status"], "sent")

            second_conversation = client.get(f"/api/conversations/{inbound_payload['contact_id']}")
            self.assertEqual(second_conversation.status_code, 200)
            second_messages = second_conversation.json()
            self.assertEqual([item["direction"] for item in second_messages], ["inbound", "outbound", "outbound"])
            self.assertEqual(second_messages[2]["message"], "Yes, we can help.")

            demo = client.post("/crm/api/dev/seed-demo", json={})
            self.assertEqual(demo.status_code, 200)
            self.assertTrue(demo.json()["ok"])
            demo_conversations = client.get("/crm/api/conversations")
            self.assertEqual(demo_conversations.status_code, 200)
            self.assertGreaterEqual(len(demo_conversations.json()), 3)
            demo_dashboard = client.get("/crm/api/dashboard")
            self.assertEqual(demo_dashboard.status_code, 200)
            self.assertTrue(demo_dashboard.json()["followUps"])
            self.assertTrue(demo_dashboard.json()["quoteActivity"])

            matched = client.post(
                "/api/inbound-message",
                json={
                    "phone": "503-555-9999",
                    "email": "sam@example.com",
                    "name": "Sam Rivera",
                    "message": "Following up by email",
                    "channel": "email",
                },
            )
            self.assertEqual(matched.status_code, 200)
            matched_payload = matched.json()

            matched_again = client.post(
                "/api/inbound-message",
                json={
                    "email": "SAM@example.com",
                    "name": "Sam Rivera",
                    "message": "Same contact",
                    "channel": "email",
                },
            )
            self.assertEqual(matched_again.status_code, 200)
            self.assertEqual(matched_again.json()["contact_id"], matched_payload["contact_id"])

            recent = client.get("/api/conversations/recent")
            self.assertEqual(recent.status_code, 200)
            self.assertTrue(any(item["contact_id"] == matched_payload["contact_id"] for item in recent.json()))

            timeline = client.get(f"/api/contacts/{matched_payload['contact_id']}/timeline")
            self.assertEqual(timeline.status_code, 200)
            self.assertTrue(any(item["activity_type"] == "message.inbound" for item in timeline.json()))

            note = client.post(
                f"/crm/api/contacts/{matched_payload['contact_id']}/notes",
                json={"body": "Customer prefers text updates."},
            )
            self.assertEqual(note.status_code, 200)
            noted_timeline = client.get(f"/api/contacts/{matched_payload['contact_id']}/timeline")
            self.assertEqual(noted_timeline.status_code, 200)
            self.assertTrue(any(item["activity_type"] == "note.added" for item in noted_timeline.json()))

            handoff = client.post(f"/api/contacts/{matched_payload['contact_id']}/start-quote", json={})
            self.assertEqual(handoff.status_code, 200)
            self.assertEqual(handoff.json()["contact_id"], matched_payload["contact_id"])

            sms_webhook = client.post(
                "/api/webhooks/sms",
                json={
                    "provider": "twilio",
                    "MessageSid": "SM-test-1",
                    "From": "(503) 555-9999",
                    "Body": "This is urgent, can you confirm next steps today?",
                    "ProfileName": "Sam Rivera",
                },
            )
            self.assertEqual(sms_webhook.status_code, 200)
            self.assertEqual(sms_webhook.json()["contact_id"], matched_payload["contact_id"])
            self.assertIn(sms_webhook.json()["match_type"], {"phone", "name", "email", "fuzzy_name"})

            call_webhook = client.post(
                "/api/webhooks/calls",
                json={
                    "provider": "ringcentral",
                    "id": "call-test-1",
                    "from": "(503) 555-9999",
                    "to": "(503) 555-0100",
                    "direction": "inbound",
                    "status": "missed",
                    "notes": "Missed call from customer.",
                },
            )
            self.assertEqual(call_webhook.status_code, 200)
            self.assertEqual(call_webhook.json()["contact_id"], matched_payload["contact_id"])

            assistant = client.get(f"/api/contacts/{matched_payload['contact_id']}/assistant")
            self.assertEqual(assistant.status_code, 200)
            assistant_payload = assistant.json()
            self.assertIn("urgent_or_confusing", assistant_payload["flags"])
            self.assertIn("Next,", assistant_payload["draft_reply"])

            draft = client.post(f"/api/contacts/{matched_payload['contact_id']}/draft-reply", json={})
            self.assertEqual(draft.status_code, 200)
            draft_activity_id = draft.json()["activity"]["id"]

            edited = client.patch(
                f"/api/review/{draft_activity_id}",
                json={"status": "edited", "body": "Thanks, I saw this. I can help. Next, I will confirm the details today."},
            )
            self.assertEqual(edited.status_code, 200)
            self.assertEqual(edited.json()["activity"]["metadata"]["status"], "edited")

            sent_draft = client.post(f"/api/review/{draft_activity_id}/approve-send", json={})
            self.assertEqual(sent_draft.status_code, 200)
            self.assertTrue(sent_draft.json()["message_id"])

            follow_up = client.post(
                f"/api/contacts/{matched_payload['contact_id']}/follow-ups",
                json={"title": "Confirm service details", "priority": "high"},
            )
            self.assertEqual(follow_up.status_code, 200)
            self.assertEqual(follow_up.json()["task"]["status"], "open")

            resolved = client.post(f"/api/contacts/{matched_payload['contact_id']}/resolve", json={})
            self.assertEqual(resolved.status_code, 200)
            self.assertEqual(resolved.json()["status"], "resolved")

            final_timeline = client.get(f"/api/contacts/{matched_payload['contact_id']}/timeline")
            self.assertEqual(final_timeline.status_code, 200)
            final_activity_types = {item["activity_type"] for item in final_timeline.json()}
            self.assertIn("call.missed", final_activity_types)
            self.assertIn("assistant.draft_reply", final_activity_types)
            self.assertIn("follow_up.assigned", final_activity_types)
            self.assertIn("review.resolved", final_activity_types)

            quote_payload = self._quote_create_from_draft(
                {
                    "job": {
                        "customer_name": "Sam Rivera",
                        "phone": "",
                        "email": "sam@example.com",
                        "address": "10 Main St",
                        "zip_code": "97214",
                        "area_sqft": "100",
                        "terrain_type": "mixed",
                        "primary_job_type": "cleanup",
                        "detected_tasks": [],
                        "notes": "",
                        "crew_instructions": "",
                        "estimated_labor_hours": "0",
                        "material_cost": "0",
                        "equipment_cost": "0",
                        "suggested_price": "0",
                        "source": "crm",
                    },
                    "items": [
                        {
                            "name": "Cleanup",
                            "quantity": "1",
                            "unit": "each",
                            "base_price": "100",
                            "per_unit_price": "0",
                            "min_charge": "0",
                        }
                    ],
                    "frequency": "one_time",
                    "tax_rate": "0",
                    "zone_modifier_percent": "0",
                }
            )
            quote_payload.contact_id = matched_payload["contact_id"]
            with self.SessionLocal() as db:
                quote = create_quote(quote_payload, db)
            self.assertEqual(quote["contact_id"], matched_payload["contact_id"])
            with self.SessionLocal() as db:
                self.assertEqual(db.query(Quote).filter(Quote.contact_id == matched_payload["contact_id"]).count(), 1)
        finally:
            main_module.app.dependency_overrides.pop(main_module.get_db, None)

    def test_first_message_only_once(self) -> None:
        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main_module.app.dependency_overrides[main_module.get_db] = override_get_db
        try:
            client = TestClient(main_module.app)

            first = client.post(
                "/api/inbound-message",
                json={
                    "phone": "(503) 555-3131",
                    "name": "Taylor Morgan",
                    "message": "I need help getting set up.",
                    "channel": "sms",
                },
            )
            self.assertEqual(first.status_code, 200)
            contact_id = first.json()["contact_id"]

            with self.SessionLocal() as db:
                messages = (
                    db.query(CrmMessage)
                    .filter(CrmMessage.contact_id == contact_id)
                    .order_by(CrmMessage.created_at, CrmMessage.id)
                    .all()
                )
                self.assertEqual([message.direction for message in messages], ["inbound", "outbound"])
                self.assertEqual(messages[1].delivery_status, "system_generated")
                self.assertIn("Got it", messages[1].body)

            second = client.post(
                "/api/inbound-message",
                json={
                    "phone": "503-555-3131",
                    "name": "Taylor Morgan",
                    "message": "Following up with one more detail.",
                    "channel": "sms",
                },
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["contact_id"], contact_id)

            with self.SessionLocal() as db:
                outbound_auto_count = (
                    db.query(CrmMessage)
                    .filter(
                        CrmMessage.contact_id == contact_id,
                        CrmMessage.direction == "outbound",
                        CrmMessage.delivery_status == "system_generated",
                    )
                    .count()
                )
                total_messages = db.query(CrmMessage).filter(CrmMessage.contact_id == contact_id).count()
                self.assertEqual(outbound_auto_count, 1)
                self.assertEqual(total_messages, 3)

            timeline = client.get(f"/api/contacts/{contact_id}/timeline")
            self.assertEqual(timeline.status_code, 200)
            auto_items = [item for item in timeline.json() if item.get("system_generated")]
            self.assertEqual(len(auto_items), 1)
            self.assertEqual(auto_items[0]["metadata"]["auto_first_message"], True)
        finally:
            main_module.app.dependency_overrides.pop(main_module.get_db, None)

    def test_lead_monitor_scores_insurance_intent(self) -> None:
        analysis = lead_monitor_service.analyze_lead_signal(
            {
                "source_type": "facebook_group",
                "area_location": "",
                "raw_text": "Moving to Portland and need a recommendation for a broker. Need a home insurance quote this week.",
            }
        )

        self.assertGreaterEqual(analysis["lead_score"], 70)
        self.assertEqual(analysis["lead_type"], "home insurance")
        self.assertEqual(analysis["urgency"], "high")
        self.assertEqual(analysis["recommended_action"], "save lead")
        self.assertIn("Portland", analysis["location_detected"])
        self.assertIn("home insurance", analysis["matched_keywords"])

    def test_lead_monitor_saves_lead_record(self) -> None:
        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main_module.app.dependency_overrides[main_module.get_db] = override_get_db
        try:
            client = TestClient(main_module.app)
            analysis = client.post(
                "/api/lead-monitor/analyze",
                json={
                    "source_type": "reddit",
                    "source_url": "https://example.com/thread",
                    "raw_text": "Any independent agent recommendations for car insurance in Bend 97701?",
                },
            )
            self.assertEqual(analysis.status_code, 200)

            saved = client.post(
                "/api/lead-monitor/leads",
                json={
                    "source_type": "reddit",
                    "source_url": "https://example.com/thread",
                    "raw_text": "Any independent agent recommendations for car insurance in Bend 97701?",
                    "analysis": analysis.json(),
                },
            )
            self.assertEqual(saved.status_code, 200)
            payload = saved.json()
            self.assertEqual(payload["source_type"], "reddit")
            self.assertEqual(payload["lead_type"], "auto insurance")
            self.assertEqual(payload["status"], "new")

            with self.SessionLocal() as db:
                self.assertEqual(db.query(CrmLeadSignal).count(), 1)
        finally:
            main_module.app.dependency_overrides.pop(main_module.get_db, None)

    def test_lead_monitor_attaches_to_existing_customer_activity_without_duplicate(self) -> None:
        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main_module.app.dependency_overrides[main_module.get_db] = override_get_db
        try:
            client = TestClient(main_module.app)
            inbound = client.post(
                "/api/inbound",
                json={
                    "phone": "(503) 555-4141",
                    "name": "Jordan Lee",
                    "email": "jordan@example.com",
                    "message": "Existing customer conversation",
                },
            )
            self.assertEqual(inbound.status_code, 200)
            contact_id = inbound.json()["contact_id"]

            saved = client.post(
                "/api/lead-monitor/leads",
                json={
                    "source_type": "facebook",
                    "source_url": "https://example.com/post",
                    "raw_text": "Jordan asked for an insurance quote in Salem.",
                },
            )
            self.assertEqual(saved.status_code, 200)
            lead_id = saved.json()["id"]

            attached = client.post(
                f"/api/lead-monitor/leads/{lead_id}/attach-customer",
                json={"contact_id": contact_id},
            )
            self.assertEqual(attached.status_code, 200)
            self.assertEqual(attached.json()["lead"]["attached_contact_id"], contact_id)

            with self.SessionLocal() as db:
                self.assertEqual(db.query(CrmContact).filter(CrmContact.mobile_phone == "+15035554141").count(), 1)
                activity = (
                    db.query(CrmActivity)
                    .filter(CrmActivity.contact_id == contact_id, CrmActivity.activity_type == "lead_signal.attached")
                    .one()
                )
                self.assertIn("Jordan asked for an insurance quote", activity.body)
                self.assertIn("https://example.com/post", activity.metadata_json)
        finally:
            main_module.app.dependency_overrides.pop(main_module.get_db, None)

    def test_existing_contact_priority_and_account_summary(self) -> None:
        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main_module.app.dependency_overrides[main_module.get_db] = override_get_db
        try:
            client = TestClient(main_module.app)

            first = client.post(
                "/api/inbound-message",
                json={
                    "phone": "(503) 555-4141",
                    "email": "priority@example.com",
                    "name": "Priority Customer",
                    "message": "Initial service question.",
                    "channel": "sms",
                },
            )
            self.assertEqual(first.status_code, 200)
            contact_id = first.json()["contact_id"]
            self.assertEqual(first.json()["priority_score"], 50)
            self.assertFalse(first.json()["matched_existing_contact"])

            later_new = client.post(
                "/api/inbound-message",
                json={
                    "phone": "(503) 555-5151",
                    "name": "Newer Unknown",
                    "message": "New customer question.",
                    "channel": "sms",
                },
            )
            self.assertEqual(later_new.status_code, 200)
            self.assertEqual(later_new.json()["priority_score"], 50)

            matched = client.post(
                "/api/inbound-message",
                json={
                    "phone": "503-555-4141",
                    "name": "Priority Customer",
                    "message": "Can you confirm timing?",
                    "channel": "sms",
                },
            )
            self.assertEqual(matched.status_code, 200)
            matched_payload = matched.json()
            self.assertEqual(matched_payload["contact_id"], contact_id)
            self.assertTrue(matched_payload["matched_existing_contact"])
            self.assertEqual(matched_payload["match_type"], "phone")
            self.assertEqual(matched_payload["priority"], "existing_contact")
            self.assertEqual(matched_payload["priority_score"], 90)
            self.assertEqual(matched_payload["account_summary"]["contact_id"], contact_id)
            self.assertIn("Can you confirm timing?", matched_payload["account_summary"]["summary"])

            with self.SessionLocal() as db:
                self.assertEqual(
                    db.query(CrmContact).filter(CrmContact.mobile_phone == "+15035554141").count(),
                    1,
                )

            recent = client.get("/api/conversations/recent")
            self.assertEqual(recent.status_code, 200)
            recent_payload = recent.json()
            self.assertGreaterEqual(len(recent_payload), 2)
            self.assertEqual(recent_payload[0]["contact_id"], contact_id)
            self.assertEqual(recent_payload[0]["priority_score"], 90)
            self.assertIn("account_summary", recent_payload[0])
        finally:
            main_module.app.dependency_overrides.pop(main_module.get_db, None)

    def _estimator_const_source(self, const_name: str) -> str:
        html = self.ESTIMATOR_HTML_PATH.read_text()
        match = re.search(rf"const {re.escape(const_name)} = .*?;\n", html, re.S)
        self.assertIsNotNone(match, f"Could not find const {const_name} in estimator.html")
        return match.group(0)

    def test_estimator_quick_entry_uses_tolerant_partial_success_flow(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()

        self.assertIn('function parseQuickEntry(text)', html)
        self.assertIn('function parseQuickEntryText(text)', html)
        self.assertIn('const validParsedRows = (parsed.rows || []).filter(isValidMeasurementRow);', html)
        self.assertIn('if (!validParsedRows.length)', html)
        self.assertIn('Quick Entry loaded ${validParsedRows.length} row', html)
        self.assertIn('Appended ${validParsedRows.length} row', html)
        self.assertIn('quickEntryErrorText(parsed.invalid)', html)

    def test_estimator_quick_entry_parser_allows_spacing_and_ignores_invalid_lines(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()

        self.assertIn(r'const match = line.match(/^(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)$/i);', html)
        self.assertIn('rawLine.split(",").forEach((token) => {', html)
        self.assertIn('if (!line) {', html)
        self.assertIn('invalidLines.push({ lineNumber: index + 1, text: line });', html)

    def test_estimator_parse_failure_recovery_points_to_quick_entry(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()

        self.assertIn('function normalizeMeasurementParseRecoveryMessage(message)', html)
        self.assertIn('Use Quick Entry below to continue now, or Replace Image to retry.', html)
        self.assertIn('Image parsing did not produce usable rows. Paste dimensions here with commas or new lines to continue immediately.', html)

    def test_estimator_quote_id_boot_path_wins_over_other_modes(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()
        boot_start = html.index("(async () => {")
        boot_source = html[boot_start:html.index("</script>", boot_start)]

        self.assertIn('const quoteId = params.get("quote_id");', boot_source)
        self.assertIn('const forceNewQuote = !quoteId && params.get("new") === "1";', boot_source)
        self.assertIn('const intakeId = quoteId ? null : params.get("intake_id");', boot_source)
        self.assertIn("setLeadSearchVisible(!shouldHideLeadSearch);", boot_source)
        self.assertIn("if (!quoteId) {\n        await refreshLeads();\n      }", boot_source)
        routing_start = boot_source.index("if (intakeId) {")
        routing_source = boot_source[routing_start:]
        self.assertLess(routing_source.index("} else if (quoteId) {"), routing_source.index("} else {"))
        self.assertIn("await loadSavedQuote(quoteId);", boot_source)

    def test_estimator_delivery_manual_mode_locks_until_reset(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()

        self.assertIn('const DELIVERY_CITY_MANUAL_LABEL = "Manual city selection";', html)
        self.assertIn('const DELIVERY_PRICE_MANUAL_LABEL = "Manual delivery price";', html)
        self.assertIn("const DELIVERY_PRICE_MANUAL_MESSAGE = `${DELIVERY_PRICE_MANUAL_LABEL}. Click Reset Delivery to recalculate.`;", html)
        self.assertIn('let deliveryCitySource = "auto";', html)
        self.assertIn('let deliveryPriceOverride = false;', html)
        self.assertIn('let deliveryMode = "auto";', html)
        self.assertIn('function setDeliveryCitySource(nextSource, options = {})', html)
        self.assertIn('function setDeliveryPriceOverride(isOverridden, options = {})', html)
        self.assertIn('function setDeliveryMode(nextMode, options = {})', html)
        self.assertIn('function resetDelivery()', html)
        self.assertIn("setDeliveryCitySource(restoredCitySource, { render: false });", html)
        self.assertIn("setDeliveryPriceOverride(parsed.delivery_price_override === true || parsed.deliveryPriceOverride === true, { render: false });", html)
        self.assertIn('if (deliveryPriceOverride === true && !options.force)', html)
        self.assertIn('if (deliveryPriceOverride === true && typeof findAutoLine === "function" && findAutoLine("material"))', html)
        self.assertIn('function bindDeliveryAmountInput(node, index, fieldName)', html)
        self.assertIn('node.addEventListener("change", handler);', html)
        self.assertIn('markDeliveryPriceManual();', html)
        self.assertIn('quoteLines[index].min_charge = amount;', html)
        self.assertIn('quoteLines[index].base_price = amount;', html)
        self.assertIn('$("reset-delivery").addEventListener("click", resetDelivery);', html)

    def test_estimator_hides_admin_pricing_controls_from_normal_staff_flow(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()

        self.assertIn('id="admin-workflow-controls" class="details-box hidden" data-admin-only="true" hidden', html)
        self.assertIn('id="admin-pricing-controls" class="details-box hidden" data-admin-only="true" hidden', html)
        self.assertIn("Admin Workflow / Site Controls", html)
        self.assertIn("Admin Pricing / Delivery Controls", html)
        self.assertNotIn("<strong>Advanced</strong>", html)
        self.assertNotIn("Pricing Options And Overrides", html)
        self.assertIn("let adminMode = false;", html)
        self.assertIn("function showAdvancedOptions()", html)
        self.assertIn("function updateAdvancedPricingControlsVisibility()", html)
        self.assertIn('adminMode = detectAdminMode(params);', html)
        self.assertIn("updateAdvancedPricingControlsVisibility();", html)
        self.assertIn('document.querySelectorAll(\'[data-admin-only="true"]\')', html)
        self.assertIn('controls.toggleAttribute("hidden", !visible);', html)

    def test_estimator_manual_recovery_button_uses_quick_entry_flow(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()

        self.assertIn('function openQuickEntryRecovery(prefill = "")', html)
        self.assertIn('openQuickEntryRecovery($("measurement-paste-input")?.value || "");', html)
        self.assertIn('Paste dimensions like 22x40, 25x30 or put one per line. Valid rows load immediately; invalid rows are skipped.', html)

    def test_estimator_quick_entry_copy_mentions_commas_and_new_lines(self) -> None:
        html = self.ESTIMATOR_HTML_PATH.read_text()

        self.assertIn('Paste dimensions separated by commas or new lines. Example:', html)
        self.assertIn('Paste dimensions here with commas or new lines. Totals update automatically.', html)
        self.assertIn('No usable parsed rows yet. Paste dimensions here with commas or new lines to continue immediately.', html)

    def test_intake_ai_draft_accepts_confirmed_measurements(self) -> None:
        with self.SessionLocal() as db:
            create_response = create_intake_submission(
                payload={
                    "customer_name": "BarkBoys Test",
                    "phone": "555-0101",
                    "address": "123 Salem Ave, Salem, OR 97301",
                    "material_type": "mulch",
                },
                db=db,
            )
            submission_id = create_response["id"]

            data = build_ai_draft_from_intake(
                submission_id=submission_id,
                payload={
                    "measurement_entries": [
                        {"entry_type": "dimension_pair", "length_ft": 14, "width_ft": 20, "raw_text": "14 20"},
                        {"entry_type": "material_yards", "yards": 4, "raw_text": "4 yds"},
                    ]
                },
                db=db,
            )

            self.assertEqual(data["job"]["combined_bed_area_sqft"], 280.0)
            self.assertEqual(float(data["job"]["combined_bed_material_yards"]), 1.73)
            self.assertEqual(data["provided_inputs"]["material_yards"], "4.00")

            submission = db.get(IntakeSubmission, submission_id)
            self.assertIsNotNone(submission)
            self.assertEqual(submission.status, "draft_generated")

    def test_intake_ai_draft_keeps_combined_material_total_for_dimensions_only_notes(self) -> None:
        with self.SessionLocal() as db:
            create_response = create_intake_submission(
                payload={
                    "customer_name": "Bed Plan Test",
                    "phone": "555-0199",
                    "address": "789 Garden Way, Salem, OR 97301",
                    "material_type": "mulch",
                },
                db=db,
            )
            submission_id = create_response["id"]

            data = build_ai_draft_from_intake(
                submission_id=submission_id,
                payload={
                    "measurement_entries": [
                        {"entry_type": "dimension_pair", "length_ft": 14, "width_ft": 20, "raw_text": "14 20"},
                        {"entry_type": "dimension_pair", "length_ft": 25, "width_ft": 30, "raw_text": "25 30"},
                    ]
                },
                db=db,
            )

            self.assertEqual(data["job"]["combined_bed_area_sqft"], 1030.0)
            self.assertAlmostEqual(float(data["job"]["combined_bed_material_yards"]), 6.36, places=2)
            self.assertAlmostEqual(float(data["combined_bed_material_yards"]), 6.36, places=2)
            self.assertAlmostEqual(float(data["provided_inputs"]["material_yards"]), 6.36, places=2)

    def test_ai_preview_confidence_floor_rewards_large_multi_bed_plan(self) -> None:
        confidence = main_module._estimate_ai_preview_confidence(
            photo_count=1,
            covered_angle_count=1,
            lidar_count=0,
            capture_device="other",
            dimension_area=Decimal("0"),
            combined_bed_area=Decimal("14264"),
            lot_size_present=False,
            edge_length_present=False,
            bed_group_count=12,
            measurement_entry_count=0,
            trusted_measurements_available=False,
            openai_used=False,
        )

        self.assertGreaterEqual(confidence, 0.85)
        self.assertLessEqual(confidence, 0.95)

    def test_ai_preview_confidence_floor_rewards_trusted_measurement_batch(self) -> None:
        confidence = main_module._estimate_ai_preview_confidence(
            photo_count=0,
            covered_angle_count=0,
            lidar_count=0,
            capture_device="other",
            dimension_area=Decimal("0"),
            combined_bed_area=Decimal("0"),
            lot_size_present=False,
            edge_length_present=False,
            bed_group_count=0,
            measurement_entry_count=5,
            trusted_measurements_available=True,
            openai_used=False,
        )

        self.assertGreaterEqual(confidence, 0.88)
        self.assertLessEqual(confidence, 0.95)

    def test_storage_manager_local_save_and_copy_keep_files_readable(self) -> None:
        uploads_root = Path(self.temp_dir.name) / "storage"
        manager = StorageManager(
            SimpleNamespace(
                storage_backend="local",
                uploads_path=str(uploads_root),
                uploads_prefix="barkboys",
                s3_bucket="",
                s3_region="",
                s3_endpoint_url="",
                s3_access_key_id="",
                s3_secret_access_key="",
                s3_session_token="",
                s3_force_path_style=False,
            )
        )

        first_ref = manager.save_bytes("quote-1/test.txt", b"hello barkboys", content_type="text/plain")
        copied_ref = manager.copy_into(first_ref, "quote-2/copied.txt", content_type="text/plain")

        self.assertEqual(Path(first_ref).read_text(encoding="utf-8"), "hello barkboys")
        self.assertEqual(Path(copied_ref).read_text(encoding="utf-8"), "hello barkboys")
        self.assertIn("barkboys/quote-1/test.txt", first_ref)
        self.assertIn("barkboys/quote-2/copied.txt", copied_ref)

    def test_storage_manager_reads_legacy_local_path_when_materializing_temp_file(self) -> None:
        uploads_root = Path(self.temp_dir.name) / "storage"
        uploads_root.mkdir(parents=True, exist_ok=True)
        legacy_file = uploads_root / "legacy-note.jpg"
        legacy_file.write_bytes(b"legacy-bytes")
        manager = StorageManager(
            SimpleNamespace(
                storage_backend="s3",
                uploads_path=str(uploads_root),
                uploads_prefix="barkboys",
                s3_bucket="demo-bucket",
                s3_region="",
                s3_endpoint_url="",
                s3_access_key_id="",
                s3_secret_access_key="",
                s3_session_token="",
                s3_force_path_style=False,
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            materialized = manager.ensure_local_path(str(legacy_file), Path(temp_dir), file_name="legacy-note.jpg")

        self.assertEqual(materialized, legacy_file)

    def test_settings_accept_railway_database_url_fallback(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://railway-user:secret@host:5432/barkboys",
            },
            clear=True,
        ):
            settings = Settings()

        self.assertEqual(settings.database_url, "postgresql://railway-user:secret@host:5432/barkboys")

    def test_settings_derive_cors_from_railway_public_domain(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "RAILWAY_PUBLIC_DOMAIN": "barkboys-preview.up.railway.app",
            },
            clear=True,
        ):
            settings = Settings()

        self.assertEqual(settings.cors_origins, ["https://barkboys-preview.up.railway.app"])

    def test_create_intake_submission_requires_name_and_address(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaises(HTTPException) as exc:
                create_intake_submission(
                    payload={
                        "customer_name": "",
                        "phone": "555-0000",
                        "address": "",
                    },
                    db=db,
                )

        self.assertEqual(exc.exception.status_code, 400)
        self.assertEqual(exc.exception.detail, "Name and address are required")

    def test_create_intake_submission_requires_phone_or_email(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaises(HTTPException) as exc:
                create_intake_submission(
                    payload={
                        "customer_name": "Missing Contact",
                        "address": "123 Test Ln, Salem, OR 97301",
                    },
                    db=db,
                )

        self.assertEqual(exc.exception.status_code, 400)
        self.assertEqual(exc.exception.detail, "Phone or email is required")

    def test_create_intake_submission_rejects_invalid_email_and_spam_website(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaises(HTTPException) as invalid_email:
                create_intake_submission(
                    payload={
                        "customer_name": "Bad Email",
                        "email": "not-an-email",
                        "address": "123 Test Ln, Salem, OR 97301",
                    },
                    db=db,
                )
            with self.assertRaises(HTTPException) as spam:
                create_intake_submission(
                    payload={
                        "customer_name": "Spam Bot",
                        "phone": "555-0000",
                        "address": "123 Test Ln, Salem, OR 97301",
                        "website": "https://spam.example",
                    },
                    db=db,
                )

        self.assertEqual(invalid_email.exception.status_code, 400)
        self.assertEqual(invalid_email.exception.detail, "Valid email is required")
        self.assertEqual(spam.exception.status_code, 400)
        self.assertEqual(spam.exception.detail, "Spam detected")

    def test_create_intake_submission_normalizes_common_inputs(self) -> None:
        with self.SessionLocal() as db:
            response = create_intake_submission(
                payload={
                    "customer_name": "  Input Test  ",
                    "email": "  MixedCase@Example.COM  ",
                    "address": " 123 Test Ln, Salem, OR 97301 ",
                    "notes": "  Backyard access through side gate.  ",
                    "capture_device": "  IPHONE_LIDAR  ",
                    "material_type": "MULCH",
                    "turf_condition": "OVERGROWN",
                    "slope": "STEEP",
                    "debris_level": "HIGH",
                    "obstacles_count": "3",
                    "has_gates": "yes",
                    "include_haulaway": "true",
                    "include_blowing": "false",
                },
                db=db,
            )
            submission = db.get(IntakeSubmission, response["id"])

        self.assertIsNotNone(submission)
        self.assertEqual(submission.customer_name, "Input Test")
        self.assertEqual(submission.email, "mixedcase@example.com")
        self.assertEqual(submission.address, "123 Test Ln, Salem, OR 97301")
        self.assertEqual(submission.notes, "Backyard access through side gate.")
        self.assertEqual(submission.capture_device, "iphone_lidar")

        framed = json.loads(submission.framed_inputs_json)
        self.assertEqual(framed["material_type"], main_module._normalize_blower_material("MULCH"))
        self.assertEqual(framed["turf_condition"], "overgrown")
        self.assertEqual(framed["slope"], "steep")
        self.assertEqual(framed["debris_level"], "high")
        self.assertEqual(framed["obstacles_count"], 3)
        self.assertTrue(framed["has_gates"])
        self.assertTrue(framed["include_haulaway"])
        self.assertFalse(framed["include_blowing"])

    def test_create_intake_submission_rejects_oversized_upload(self) -> None:
        with self.SessionLocal() as db, mock.patch.object(main_module, "MAX_UPLOAD_BYTES", 4):
            with self.assertRaises(HTTPException) as exc:
                create_intake_submission(
                    payload={
                        "customer_name": "Big Upload",
                        "phone": "555-0000",
                        "address": "123 Test Ln, Salem, OR 97301",
                        "photos": [
                            self._payload_file("oversized.png", b"12345", "image/png"),
                        ],
                    },
                    db=db,
                )

        self.assertEqual(exc.exception.status_code, 413)
        self.assertEqual(exc.exception.detail, "File too large: oversized.png")

    def test_openai_health_diagnostics_includes_ca_bundle_without_crashing(self) -> None:
        fake_connection = mock.MagicMock()
        fake_connection.__enter__.return_value = object()
        fake_connection.__exit__.return_value = False

        fake_settings = SimpleNamespace(
            openai_vision_model="gpt-4.1",
            openai_api_key="test-key",
            openai_allow_insecure_ssl=False,
            openai_ca_bundle="/tmp/demo-ca.pem",
        )

        with mock.patch.object(
            main_module, "refresh_settings", return_value=fake_settings
        ), mock.patch.object(
            main_module, "_openai_health_payload", return_value={"ok": False, "detail": "Key rejected", "reason_code": "openai_auth_failed"}
        ), mock.patch.object(
            main_module, "_openai_proxy_diagnostics", return_value={"present": False, "detail": "No proxy", "keys": []}
        ), mock.patch.object(
            main_module.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("1.2.3.4", 443))],
        ), mock.patch.object(
            main_module.socket,
            "create_connection",
            return_value=fake_connection,
        ):
            diagnostics = main_module.openai_health_diagnostics()

        self.assertEqual(diagnostics["ca_bundle"], "/tmp/demo-ca.pem")
        self.assertTrue(diagnostics["key_present"])
        self.assertEqual(diagnostics["http"]["detail"], "Key rejected")
        self.assertTrue(diagnostics["dns"]["ok"])
        self.assertTrue(diagnostics["tcp"]["ok"])

    def test_ui_build_health_reports_staff_estimator_build_details(self) -> None:
        payload = ui_build_health()

        self.assertEqual(payload["status"], "ok")
        staff_estimator = payload["pages"]["staff_estimator"]
        self.assertEqual(staff_estimator["route"], "/staff-estimator")
        self.assertTrue(staff_estimator["exists"])
        self.assertTrue(staff_estimator["path"].endswith("app/static/estimator.html"))
        self.assertIn("Build ", staff_estimator["build_stamp"])
        self.assertRegex(staff_estimator["last_modified_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_estimate_job_preserves_extraction_meta_for_ui_health_state(self) -> None:
        with mock.patch.object(
            ai_estimator_module,
            "analyze_uploaded_images",
            return_value={
                "summary": "demo",
                "detected_tasks": [],
                "detected_zones": [],
                "zone_summary": "",
                "measurement_entries": [],
                "bed_groups": [],
                "combined_bed_area_sqft": None,
                "combined_bed_material_yards": None,
                "dimension_observations": {},
                "extraction_meta": {
                    "openai_configured": True,
                    "openai_used": False,
                    "openai_error": "openai_dns_failed",
                    "fallback_ocr_used": True,
                    "trusted_measurements_available": False,
                },
            },
        ):
            result = ai_estimator_module.estimate_job(
                lot_size_sqft="4500",
                edge_length="120",
                terrain_type="mixed",
                zip_code="97301",
                uploaded_images=[],
                job_notes=None,
                exclusions=None,
            )

        self.assertEqual(
            result["extraction_meta"],
            {
                "openai_configured": True,
                "openai_used": False,
                "openai_error": "openai_dns_failed",
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
            },
        )

    def test_estimate_job_does_not_add_photo_estimate_row_without_real_parsed_measurements(self) -> None:
        with mock.patch.object(
            ai_estimator_module,
            "analyze_uploaded_images",
            return_value={
                "summary": "demo",
                "detected_tasks": [],
                "detected_zones": [],
                "zone_summary": "",
                "measurement_entries": [],
                "bed_groups": [],
                "combined_bed_area_sqft": None,
                "combined_bed_material_yards": None,
                "dimension_observations": {},
                "extraction_meta": {
                    "openai_configured": True,
                    "openai_used": True,
                    "openai_error": None,
                    "fallback_ocr_used": False,
                    "trusted_measurements_available": False,
                },
            },
        ):
            result = ai_estimator_module.estimate_job(
                lot_size_sqft="4500",
                edge_length="120",
                terrain_type="mixed",
                zip_code="97301",
                uploaded_images=[
                    {"file_name": "starbucks-front.jpg"},
                    {"file_name": "starbucks-left.jpg"},
                ],
                job_notes=None,
                exclusions=None,
            )

        self.assertFalse(result["extraction_meta"]["trusted_measurements_available"])
        self.assertEqual(result["measurement_entries"], [])
        self.assertEqual(result["recommended_material_yards"], Decimal("0"))

    def test_estimate_job_skips_scene_geometry_fallback_when_measurement_reference_images_are_present(self) -> None:
        with mock.patch.object(
            ai_estimator_module,
            "analyze_uploaded_images",
            return_value={
                "summary": "demo",
                "detected_tasks": [],
                "detected_zones": [],
                "zone_summary": "",
                "measurement_entries": [],
                "bed_groups": [],
                "combined_bed_area_sqft": None,
                "combined_bed_material_yards": None,
                "dimension_observations": {},
                "extraction_meta": {
                    "openai_configured": True,
                    "openai_used": True,
                    "openai_error": None,
                    "fallback_ocr_used": False,
                    "trusted_measurements_available": False,
                },
            },
        ):
            result = ai_estimator_module.estimate_job(
                lot_size_sqft="4500",
                edge_length="120",
                terrain_type="mixed",
                zip_code="97301",
                uploaded_images=[{"file_name": "site-photo.jpg"}],
                measurement_reference_images=[{"file_name": "measurement-note.jpg"}],
                job_notes=None,
                exclusions=None,
            )

        self.assertEqual(result["measurement_entries"], [])
        self.assertEqual(float(result["recommended_material_yards"]), 0.0)
        self.assertEqual(result["dimension_observations"], {})

    def test_ai_preview_quote_returns_measurement_entries_for_staff_review(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("2.50"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [{"job_type": "mulch_refresh", "label": "Mulch Refresh", "confidence": 0.7, "evidence": ["photo"]}],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [
                {
                    "entry_type": "dimension_pair",
                    "raw_text": "Photo estimate",
                    "length_ft": 36.0,
                    "width_ft": 25.0,
                    "estimated_area_sqft": 900.0,
                    "estimated_material_yards": 5.56,
                    "confidence": 0.62,
                    "source_images": ["starbucks-front.jpg"],
                    "include": False,
                    "inferred_from_photo_estimate": True,
                }
            ],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": False,
                "trusted_measurements_available": False,
            },
            "missing_angle_estimate": {
                "available_angles": ["front"],
                "missing_angles": ["back", "left", "right"],
                "estimated_width_ft": 36.0,
                "estimated_depth_ft": 25.0,
                "estimated_area_sqft": 900.0,
                "confidence": 0.62,
                "basis": "Front/back cues present; depth inferred from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ), mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            result = main_module.ai_preview_quote(
                payload={
                    "photos": [{"file_name": "starbucks-front.jpg"}],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertIn("measurement_entries", result)
        self.assertEqual(len(result["measurement_entries"]), 1)
        self.assertEqual(result["measurement_entries"][0]["raw_text"], "Photo estimate")

    def test_ai_preview_quote_disables_site_media_note_detection_and_forwards_confirmed_measurements(self) -> None:
        confirmed_measurements = [
            {
                "entry_type": "dimension_pair",
                "raw_text": "91x22",
                "length_ft": 91,
                "width_ft": 22,
            }
        ]
        fake_ai_result = {
            "area_sqft": Decimal("2002"),
            "edge_length_ft": Decimal("226"),
            "recommended_material_yards": Decimal("12.36"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": confirmed_measurements,
            "measurement_parse": {"classification": "exact_measurement_note"},
            "bed_groups": [],
            "combined_bed_area_sqft": Decimal("2002"),
            "combined_bed_material_yards": Decimal("12.36"),
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": False,
                "trusted_measurements_available": True,
            },
            "missing_angle_estimate": {},
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ) as estimate_job_mock, mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            ai_preview_quote(
                payload={
                    "photos": [{"file_name": "yard-photo.jpg"}],
                    "frequency": "one_time",
                    "material_type": "mulch",
                    "measurement_entries": confirmed_measurements,
                },
                db=db,
            )

        self.assertEqual(
            estimate_job_mock.call_args.kwargs["confirmed_measurement_entries"],
            confirmed_measurements,
        )
        self.assertFalse(
            estimate_job_mock.call_args.kwargs["allow_site_media_measurement_reference_detection"]
        )

    def test_ai_preview_quote_passes_measurement_reference_images_to_estimator(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("2.50"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": False,
                "openai_error": None,
                "fallback_ocr_used": False,
                "trusted_measurements_available": False,
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": ["front", "back", "left", "right"],
                "estimated_width_ft": 30.0,
                "estimated_depth_ft": 20.0,
                "estimated_area_sqft": 600.0,
                "confidence": 0.52,
                "basis": "Limited angle coverage; dimensions inferred mainly from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ) as estimate_job_mock, mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            main_module.ai_preview_quote(
                payload={
                    "photos": [{"file_name": "site-photo.jpg"}],
                    "measurement_photos": [{"file_name": "measurement-note.jpg"}],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(
            estimate_job_mock.call_args.kwargs["measurement_reference_images"],
            [{
                "file_name": "measurement-note.jpg",
                "parse_mode": "auto",
                "category": "measurement_note",
            }],
        )

    def test_ai_preview_quote_forces_site_media_into_measurement_reference_mode(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("2.50"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": False,
                "openai_error": None,
                "fallback_ocr_used": False,
                "trusted_measurements_available": False,
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": ["front", "back", "left", "right"],
                "estimated_width_ft": 30.0,
                "estimated_depth_ft": 20.0,
                "estimated_area_sqft": 600.0,
                "confidence": 0.52,
                "basis": "Limited angle coverage; dimensions inferred mainly from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ) as estimate_job_mock, mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            main_module.ai_preview_quote(
                payload={
                    "photos": [{"file_name": "site-photo.jpg"}],
                    "force_measurement_reference_mode": True,
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(
            estimate_job_mock.call_args.kwargs["measurement_reference_images"],
            [{
                "file_name": "site-photo.jpg",
                "parse_mode": "force_measurement_note",
                "category": "site_media",
            }],
        )
        self.assertEqual(
            estimate_job_mock.call_args.kwargs["uploaded_images"],
            [],
        )

    def test_ai_preview_quote_routes_site_media_force_measurement_note_by_file_metadata(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("2.50"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": False,
                "openai_error": None,
                "fallback_ocr_used": False,
                "trusted_measurements_available": False,
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": ["front", "back", "left", "right"],
                "estimated_width_ft": 30.0,
                "estimated_depth_ft": 20.0,
                "estimated_area_sqft": 600.0,
                "confidence": 0.52,
                "basis": "Limited angle coverage; dimensions inferred mainly from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ) as estimate_job_mock, mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            main_module.ai_preview_quote(
                payload={
                    "photos": [{
                        "file_name": "site-photo.jpg",
                        "parse_mode": "force_measurement_note",
                        "category": "site_media",
                    }],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(
            estimate_job_mock.call_args.kwargs["measurement_reference_images"],
            [{
                "file_name": "site-photo.jpg",
                "parse_mode": "force_measurement_note",
                "category": "site_media",
            }],
        )
        self.assertEqual(estimate_job_mock.call_args.kwargs["uploaded_images"], [])

    def test_ai_preview_quote_force_scene_photo_skips_exact_probe(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("29.94"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {"estimated_area_sqft": Decimal("4848.78")},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": ["front", "back", "left", "right"],
                "estimated_width_ft": 76.6,
                "estimated_depth_ft": 63.3,
                "estimated_area_sqft": 4848.78,
                "confidence": 0.52,
                "basis": "Limited angle coverage; dimensions inferred mainly from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ) as estimate_job_mock, mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            main_module.ai_preview_quote(
                payload={
                    "photos": [{
                        "file_name": "StarbucksBB.jpg",
                        "parse_mode": "force_scene_photo",
                        "category": "site_media",
                    }],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(estimate_job_mock.call_count, 1)
        self.assertEqual(
            estimate_job_mock.call_args.kwargs["uploaded_images"],
            [{
                "file_name": "StarbucksBB.jpg",
                "parse_mode": "force_scene_photo",
                "category": "site_media",
            }],
        )
        self.assertEqual(estimate_job_mock.call_args.kwargs["measurement_reference_images"], [])

    def test_ai_preview_quote_does_not_backfill_material_yards_when_measurement_note_parse_fails(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("0"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
                "measurement_reference_images_present": True,
                "exact_measurement_parse_failed": True,
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": [],
                "estimated_width_ft": None,
                "estimated_depth_ft": None,
                "estimated_area_sqft": None,
                "confidence": 0.0,
                "basis": "",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ), mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            result = main_module.ai_preview_quote(
                payload={
                    "measurement_photos": [{"file_name": "measurement-note.jpg"}],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(result["provided_inputs"]["material_yards"], "0")

    def test_ai_preview_quote_does_not_backfill_material_yards_when_forced_site_media_note_parse_fails(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("0"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
                "measurement_reference_images_present": True,
                "exact_measurement_parse_failed": True,
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": [],
                "estimated_width_ft": None,
                "estimated_depth_ft": None,
                "estimated_area_sqft": None,
                "confidence": 0.0,
                "basis": "",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ), mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            result = main_module.ai_preview_quote(
                payload={
                    "photos": [{"file_name": "site-photo.jpg"}],
                    "force_measurement_reference_mode": True,
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(result["provided_inputs"]["material_yards"], "0")

    def test_ai_preview_quote_does_not_backfill_material_yards_from_auto_scene_photo_estimate(self) -> None:
        fake_ai_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("29.94"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [
                {
                    "entry_type": "dimension_pair",
                    "raw_text": "Photo estimate",
                    "length_ft": Decimal("76.6"),
                    "width_ft": Decimal("63.3"),
                    "confidence": Decimal("0.52"),
                    "source_images": ["StarbucksBB.jpg"],
                    "inferred_from_photo_estimate": True,
                }
            ],
            "measurement_parse": {
                "classification": "scene_photo_estimation",
                "rectangles": [],
                "manual_yards": [],
                "unclear_rows": [],
                "total_square_feet": 0.0,
                "computed_cubic_yards_at_2_in": 0.0,
                "manual_cubic_yards": 0.0,
                "grand_total_cubic_yards": 0.0,
                "should_use_geometry_fallback": True,
            },
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {"estimated_area_sqft": Decimal("4848.78")},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": ["front", "back", "left", "right"],
                "estimated_width_ft": 76.6,
                "estimated_depth_ft": 63.3,
                "estimated_area_sqft": 4848.78,
                "confidence": 0.52,
                "basis": "Limited angle coverage; dimensions inferred mainly from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=fake_ai_result,
        ), mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            result = main_module.ai_preview_quote(
                payload={
                    "photos": [{"file_name": "StarbucksBB.jpg"}],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(result["measurement_parse"]["classification"], "scene_photo_estimation")
        self.assertEqual(result["provided_inputs"]["material_yards"], "0")

    def test_ai_preview_quote_keeps_site_media_on_scene_photo_route(self) -> None:
        normal_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("29.94"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [
                {
                    "entry_type": "dimension_pair",
                    "raw_text": "Photo estimate",
                    "length_ft": Decimal("76.6"),
                    "width_ft": Decimal("63.3"),
                    "confidence": Decimal("0.52"),
                    "source_images": ["StarbucksBB.jpg"],
                    "inferred_from_photo_estimate": True,
                }
            ],
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {"estimated_area_sqft": Decimal("4848.78")},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
                "ocr_debug": [{"source_name": "StarbucksBB.jpg", "preview_text": "scene text", "dimension_row_candidates": 0}],
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": ["front", "back", "left", "right"],
                "estimated_width_ft": 76.6,
                "estimated_depth_ft": 63.3,
                "estimated_area_sqft": 4848.78,
                "confidence": 0.52,
                "basis": "Limited angle coverage; dimensions inferred mainly from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }
        exact_result = {
            **normal_result,
            "recommended_material_yards": Decimal("99.33"),
            "measurement_entries": [
                {"entry_type": "dimension_pair", "raw_text": "91x22", "length_ft": 91, "width_ft": 22, "confidence": 0.98, "source_images": ["StarbucksBB.jpg", "openai_vision"]},
                {"entry_type": "dimension_pair", "raw_text": "25x30", "length_ft": 25, "width_ft": 30, "confidence": 0.98, "source_images": ["StarbucksBB.jpg", "openai_vision"]},
            ],
            "combined_bed_area_sqft": Decimal("2752"),
            "combined_bed_material_yards": Decimal("16.99"),
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": True,
                "measurement_reference_images_present": True,
                "ocr_debug": [{"source_name": "StarbucksBB.jpg", "preview_text": "91 22\n25 30", "dimension_row_candidates": 2}],
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=normal_result,
        ) as estimate_job_mock, mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            result = main_module.ai_preview_quote(
                payload={
                    "photos": [{"file_name": "StarbucksBB.jpg"}],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(estimate_job_mock.call_count, 1)
        self.assertEqual(result["measurement_entries"][0]["raw_text"], "Photo estimate")
        self.assertFalse(result["extraction_meta"].get("measurement_reference_images_present"))

    def test_ai_preview_quote_does_not_replace_scene_geometry_with_failed_note_probe(self) -> None:
        normal_result = {
            "area_sqft": Decimal("4500"),
            "edge_length_ft": Decimal("120"),
            "recommended_material_yards": Decimal("29.94"),
            "estimated_labor_hours": Decimal("2.0"),
            "material_cost": Decimal("125.00"),
            "equipment_cost": Decimal("125.00"),
            "suggested_price": Decimal("275.00"),
            "recommended_crew_size": 2,
            "estimated_duration_hours": Decimal("1.0"),
            "crew_instructions": "General yard cleanup.",
            "primary_job_type": "mulch_refresh",
            "detected_tasks": [],
            "task_breakdown": [],
            "zone_summary": "",
            "detected_zones": [],
            "measurement_entries": [
                {
                    "entry_type": "dimension_pair",
                    "raw_text": "Photo estimate",
                    "length_ft": Decimal("76.6"),
                    "width_ft": Decimal("63.3"),
                    "confidence": Decimal("0.52"),
                    "source_images": ["StarbucksBB.jpg"],
                    "inferred_from_photo_estimate": True,
                }
            ],
            "measurement_parse": {
                "classification": "scene_photo_estimation",
                "rectangles": [],
                "manual_yards": [],
                "unclear_rows": [],
                "total_square_feet": 0.0,
                "computed_cubic_yards_at_2_in": 0.0,
                "manual_cubic_yards": 0.0,
                "grand_total_cubic_yards": 0.0,
                "should_use_geometry_fallback": True,
            },
            "bed_groups": [],
            "combined_bed_area_sqft": None,
            "combined_bed_material_yards": None,
            "dimension_observations": {"estimated_area_sqft": Decimal("4848.78")},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
                "ocr_debug": [{"source_name": "StarbucksBB.jpg", "preview_text": "scene text", "dimension_row_candidates": 0}],
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": ["front", "back", "left", "right"],
                "estimated_width_ft": 76.6,
                "estimated_depth_ft": 63.3,
                "estimated_area_sqft": 4848.78,
                "confidence": 0.52,
                "basis": "Limited angle coverage; dimensions inferred mainly from area.",
            },
            "measurement_defaults": {
                "material_depth_inches": Decimal("2.0"),
            },
        }
        exact_probe_failed = {
            **normal_result,
            "recommended_material_yards": Decimal("0"),
            "measurement_entries": [],
            "measurement_parse": {
                "classification": "failed_ocr_unreadable_note",
                "rectangles": [],
                "manual_yards": [],
                "unclear_rows": [{"raw": "totals only", "reason": "Contains numbers but did not parse cleanly as a rectangle or yard entry.", "source_name": "StarbucksBB.jpg"}],
                "total_square_feet": 0.0,
                "computed_cubic_yards_at_2_in": 0.0,
                "manual_cubic_yards": 0.0,
                "grand_total_cubic_yards": 0.0,
                "should_use_geometry_fallback": False,
            },
            "dimension_observations": {},
            "extraction_meta": {
                "openai_configured": True,
                "openai_used": True,
                "openai_error": None,
                "fallback_ocr_used": True,
                "trusted_measurements_available": False,
                "measurement_reference_images_present": True,
                "exact_measurement_parse_failed": True,
                "ocr_debug": [{"source_name": "StarbucksBB.jpg", "preview_text": "totals only", "dimension_row_candidates": 0}],
            },
            "missing_angle_estimate": {
                "available_angles": [],
                "missing_angles": [],
                "estimated_width_ft": None,
                "estimated_depth_ft": None,
                "estimated_area_sqft": None,
                "confidence": 0.0,
                "basis": "",
            },
        }

        with self.SessionLocal() as db, mock.patch.object(
            main_module,
            "estimate_job",
            return_value=normal_result,
        ), mock.patch.object(
            main_module,
            "_build_detected_task_items",
            return_value=[],
        ), mock.patch.object(
            main_module,
            "calculate_quote",
            return_value={
                "items": [],
                "subtotal": Decimal("0"),
                "zone_adjustment": Decimal("0"),
                "frequency_discount_percent": Decimal("0"),
                "discount_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total": Decimal("0"),
            },
        ):
            result = main_module.ai_preview_quote(
                payload={
                    "photos": [{"file_name": "StarbucksBB.jpg"}],
                    "frequency": "one_time",
                    "material_type": "mulch",
                },
                db=db,
            )

        self.assertEqual(result["measurement_parse"]["classification"], "scene_photo_estimation")
        self.assertEqual(result["provided_inputs"]["material_yards"], "0")
        self.assertEqual(result["measurement_entries"][0]["raw_text"], "Photo estimate")

    def test_create_quote_preserves_follow_up_date_on_job_and_lead(self) -> None:
        follow_up_date = (date.today() + timedelta(days=2)).isoformat()
        with self.SessionLocal() as db:
            data = create_quote(
                payload=QuoteCreate(
                    job={
                        "customer_name": "Follow Up Test",
                        "phone": "555-0202",
                        "email": "followup@example.com",
                        "address": "456 Market St, Salem, OR 97301",
                        "follow_up_date": follow_up_date,
                        "lead_status": "quoted",
                    },
                    items=[
                        QuoteItemInput(
                            name="Mulch Delivery",
                            quantity=5,
                            unit="yd",
                            base_price=100,
                            per_unit_price=25,
                            min_charge=150,
                        )
                    ],
                    frequency="one_time",
                    tax_rate=0,
                    zone_modifier_percent=0,
                ),
                db=db,
            )

            self.assertEqual(str(data["job"]["follow_up_date"]), follow_up_date)
            lead = db.query(Lead).filter(Lead.quote_id == data["id"]).one()
            self.assertEqual(str(lead.follow_up_date), follow_up_date)

    def test_hosted_workflow_regression_covers_intake_import_quote_media_and_pdf(self) -> None:
        with self.SessionLocal() as db:
            intake = create_intake_submission(
                payload={
                    "customer_name": "Hosted Workflow Test",
                    "phone": "555-0303",
                    "address": "123 Bark St, Salem, OR 97301",
                    "material_type": "mulch",
                    "capture_device": "iphone",
                    "photos": [
                        self._payload_file("front-yard.png", self.VALID_PNG_BYTES, "image/png"),
                    ],
                    "lidar_files": [
                        self._payload_file("site-scan.usdz", b"usdz-demo", "model/vnd.usdz+zip"),
                    ],
                },
                db=db,
            )
            submission_id = intake["id"]

            draft = build_ai_draft_from_intake(
                submission_id=submission_id,
                payload={
                    "measurement_entries": [
                        {"entry_type": "dimension_pair", "length_ft": 18, "width_ft": 24, "raw_text": "18 24"},
                        {"entry_type": "material_yards", "yards": 3, "raw_text": "3 yds"},
                    ]
                },
                db=db,
            )

            quote = create_quote(payload=self._quote_create_from_draft(draft), db=db)

            upload_result = upload_quote_media(
                quote_id=quote["id"],
                payload={
                    "media_kind": "exclusion_photo",
                    "capture_device": "staff_phone",
                    "files": [
                        self._payload_file("avoid-fence.png", self.VALID_PNG_BYTES, "image/png"),
                    ],
                },
                db=db,
            )
            import_result = import_intake_media_to_quote(
                quote_id=quote["id"],
                payload={"intake_submission_id": submission_id},
                db=db,
            )
            pdf_response = get_quote_pdf(quote["id"], db=db)

            self.assertEqual(upload_result["saved"], 1)
            self.assertEqual(import_result["saved"], 2)
            self.assertEqual(pdf_response.media_type, "application/pdf")
            self.assertTrue(pdf_response.body.startswith(b"%PDF"))
            self.assertIn('attachment; filename="quote-', pdf_response.headers["Content-Disposition"])

            stored_media = (
                db.query(QuoteMedia)
                .filter(QuoteMedia.quote_id == quote["id"])
                .order_by(QuoteMedia.id.asc())
                .all()
            )
            self.assertEqual(len(stored_media), 3)
            self.assertEqual([row.media_kind for row in stored_media], ["exclusion_photo", "photo", "lidar_scan"])
            self.assertEqual(stored_media[1].capture_device, "iphone")
            self.assertTrue(Path(stored_media[1].storage_path).exists())
            self.assertTrue(Path(stored_media[2].storage_path).exists())

            submission = db.get(IntakeSubmission, submission_id)
            self.assertIsNotNone(submission)
            self.assertEqual(submission.status, "quoted")

            event_names = [
                row.event_name
                for row in db.query(QuoteEvent).filter(QuoteEvent.quote_id == quote["id"]).order_by(QuoteEvent.id.asc())
            ]
            self.assertEqual(
                event_names,
                ["quote_saved", "media_uploaded", "intake_media_imported", "pdf_downloaded"],
            )

    def test_job_import_from_intake_only_copies_photos(self) -> None:
        with self.SessionLocal() as db:
            intake = create_intake_submission(
                payload={
                    "customer_name": "Job Import Test",
                    "phone": "555-0404",
                    "address": "456 Cedar Ave, Salem, OR 97301",
                    "material_type": "mulch",
                    "photos": [
                        self._payload_file("left-bed.png", self.VALID_PNG_BYTES, "image/png"),
                    ],
                    "lidar_files": [
                        self._payload_file("yard-scan.usdz", b"usdz-demo", "model/vnd.usdz+zip"),
                    ],
                },
                db=db,
            )

            quote = create_quote(
                payload=QuoteCreate(
                    job={
                        "customer_name": "Job Import Test",
                        "phone": "555-0404",
                        "address": "456 Cedar Ave, Salem, OR 97301",
                    },
                    items=[
                        QuoteItemInput(
                            name="Mulch Delivery",
                            quantity=3,
                            unit="yd",
                            base_price=100,
                            per_unit_price=35,
                            min_charge=100,
                        )
                    ],
                    frequency="one_time",
                    tax_rate=0,
                    zone_modifier_percent=0,
                ),
                db=db,
            )

            result = import_intake_media_to_job(
                job_id=quote["job"]["id"],
                payload={"intake_submission_id": intake["id"]},
                db=db,
            )

            self.assertEqual(result["saved"], 1)
            job_photos = db.query(JobPhoto).filter(JobPhoto.job_id == quote["job"]["id"]).all()
            self.assertEqual(len(job_photos), 1)
            self.assertEqual(job_photos[0].file_name, "left-bed.png")
            self.assertTrue(Path(job_photos[0].storage_path).exists())

    def test_uploaded_assets_upload_immediately_and_survive_draft_reload(self) -> None:
        with self.SessionLocal() as db:
            first = asyncio.run(
                upload_site_media_assets(
                    draft_token="draft-media-1",
                    parse_mode="force_scene_photo",
                    files=[self._upload_file("front-yard.jpg", self.VALID_PNG_BYTES, "image/jpeg")],
                    db=db,
                )
            )
            second = asyncio.run(
                upload_measurement_note_assets(
                    draft_token="draft-media-1",
                    parse_mode="force_measurement_note",
                    files=[self._upload_file("measurements.png", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )
            asyncio.run(
                upload_exclusion_assets(
                    draft_token="draft-media-1",
                    parse_mode="auto",
                    files=[self._upload_file("avoid-bed.png", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )

            self.assertEqual(first["assets"][0]["category"], "site_media")
            self.assertEqual(first["assets"][0]["parseMode"], "force_scene_photo")
            self.assertEqual(second["assets"][0]["category"], "measurement_note")
            self.assertEqual(second["assets"][0]["parseMode"], "force_measurement_note")

            draft_assets = list_uploaded_assets(draft_token="draft-media-1", db=db)
            self.assertEqual(len(draft_assets["assets"]), 3)
            self.assertEqual(
                [asset["filename"] for asset in draft_assets["assets"]],
                ["front-yard.jpg", "measurements.png", "avoid-bed.png"],
            )
            self.assertEqual(
                [asset["parseMode"] for asset in draft_assets["assets"]],
                ["force_scene_photo", "force_measurement_note", "auto"],
            )
            self.assertEqual(
                [asset["status"] for asset in draft_assets["assets"]],
                ["uploaded", "uploaded", "uploaded"],
            )

            content_response = uploaded_asset_content(asset_id=first["assets"][0]["id"], db=db)
            self.assertEqual(content_response.body, self.VALID_PNG_BYTES)

    def test_upload_dimensions_returns_measurement_lines_for_frontend(self) -> None:
        with self.SessionLocal() as db:
            with mock.patch.object(
                main_module,
                "estimate_job",
                return_value={
                    "measurement_entries": [
                        {
                            "entry_type": "dimension_pair",
                            "raw_text": "9x22",
                            "length_ft": 9,
                            "width_ft": 22,
                            "estimated_area_sqft": 198,
                            "estimated_material_yards": 1.22,
                        },
                        {
                            "entry_type": "dimension_pair",
                            "raw_text": "25x30",
                            "length_ft": 25,
                            "width_ft": 30,
                            "estimated_area_sqft": 750,
                            "estimated_material_yards": 4.63,
                        },
                    ],
                    "measurement_parse": {"classification": "exact_measurement_note"},
                    "extraction_meta": {"trusted_measurements_available": True},
                },
            ):
                result = asyncio.run(
                    upload_dimension_assets(
                        draft_token="draft-dimensions-1",
                        files=[self._upload_file("yard-note.jpg", self.VALID_PNG_BYTES, "image/jpeg")],
                        db=db,
                    )
                )

            self.assertEqual(len(result["assets"]), 1)
            self.assertEqual(result["measurements"], ["9x22", "25x30"])
            self.assertEqual(result["measurementsText"], "9x22\n25x30")
            self.assertEqual([row["raw"] for row in result["rows"]], ["9x22", "25x30"])
            self.assertIn("Measurements detected from yard-note.jpg", result["message"])
            self.assertEqual(
                result["measurement_entries"][0]["source_asset_id"],
                str(result["assets"][0]["id"]),
            )
            self.assertEqual(
                result["measurement_entries"][0]["source_filename"],
                "yard-note.jpg",
            )

            stored_asset = db.get(UploadedAsset, result["assets"][0]["id"])
            self.assertIsNotNone(stored_asset)
            self.assertEqual(stored_asset.upload_status, "ready")
            parser_result = json.loads(stored_asset.parse_result_json or "{}")
            self.assertEqual(parser_result.get("measurements"), ["9x22", "25x30"])

    def test_quick_entry_parser_keeps_valid_rows_when_some_lines_are_invalid(self) -> None:
        estimator_html = self.ESTIMATOR_HTML_PATH.read_text()
        start = estimator_html.index('function parseMeasurementDimensionLines(text, sourceLabel = "Pasted dimensions") {')
        end = estimator_html.index("function parsePastedMeasurementDimensions(text) {", start)
        parser_source = estimator_html[start:end]
        quick_entry_text = "22x40\n225x10\n18 x 12\nbad line\n\n"

        script = f"""
const assert = require("assert");
function roundMoney(value) {{
  const n = Number(value);
  return Math.round((Number.isFinite(n) ? n : 0) * 100) / 100;
}}
{parser_source}
const result = parseMeasurementDimensionLines({json.dumps(quick_entry_text)}, "Quick entry");
assert.strictEqual(result.entries.length, 3);
assert.strictEqual(result.invalidLines.length, 1);
assert.deepStrictEqual(result.entries.map((entry) => entry.raw_text), ["22x40", "225x10", "18 x 12"]);
assert.deepStrictEqual(result.invalidLines.map((line) => line.text), ["bad line"]);
console.log("quick-entry-parser-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("quick-entry-parser-ok", completed.stdout)

    def test_quick_entry_parser_splits_comma_separated_dimensions_on_one_line(self) -> None:
        estimator_html = self.ESTIMATOR_HTML_PATH.read_text()
        start = estimator_html.index('function parseMeasurementDimensionLines(text, sourceLabel = "Pasted dimensions") {')
        end = estimator_html.index("function parsePastedMeasurementDimensions(text) {", start)
        parser_source = estimator_html[start:end]
        quick_entry_text = "10X15, 12X30,\n"

        script = f"""
const assert = require("assert");
function roundMoney(value) {{
  const n = Number(value);
  return Math.round((Number.isFinite(n) ? n : 0) * 100) / 100;
}}
{parser_source}
const result = parseMeasurementDimensionLines({json.dumps(quick_entry_text)}, "Quick entry");
assert.strictEqual(result.entries.length, 2);
assert.strictEqual(result.invalidLines.length, 0);
assert.deepStrictEqual(result.entries.map((entry) => entry.raw_text), ["10X15", "12X30"]);
assert.deepStrictEqual(result.entries.map((entry) => [entry.length_ft, entry.width_ft]), [[10, 15], [12, 30]]);
console.log("quick-entry-comma-parser-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("quick-entry-comma-parser-ok", completed.stdout)

    def test_quick_entry_parser_preserves_known_good_comma_input_with_invalid_tail(self) -> None:
        estimator_html = self.ESTIMATOR_HTML_PATH.read_text()
        start = estimator_html.index('function parseMeasurementDimensionLines(text, sourceLabel = "Pasted dimensions") {')
        end = estimator_html.index("function parsePastedMeasurementDimensions(text) {", start)
        parser_source = estimator_html[start:end]
        quick_entry_text = "10X15, 12X30, bad line"

        script = f"""
const assert = require("assert");
function roundMoney(value) {{
  const n = Number(value);
  return Math.round((Number.isFinite(n) ? n : 0) * 100) / 100;
}}
{parser_source}
const result = parseMeasurementDimensionLines({json.dumps(quick_entry_text)}, "Quick entry");
assert.strictEqual(result.entries.length, 2);
assert.strictEqual(result.invalidLines.length, 1);
assert.deepStrictEqual(result.entries.map((entry) => entry.raw_text), ["10X15", "12X30"]);
assert.deepStrictEqual(result.entries.map((entry) => [entry.length_ft, entry.width_ft]), [[10, 15], [12, 30]]);
assert.deepStrictEqual(result.invalidLines, [{{ lineNumber: 1, text: "bad line" }}]);
console.log("quick-entry-known-good-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("quick-entry-known-good-ok", completed.stdout)

    def test_quick_entry_parser_supports_mixed_commas_newlines_and_trailing_tokens(self) -> None:
        estimator_html = self.ESTIMATOR_HTML_PATH.read_text()
        start = estimator_html.index('function parseMeasurementDimensionLines(text, sourceLabel = "Pasted dimensions") {')
        end = estimator_html.index("function parsePastedMeasurementDimensions(text) {", start)
        parser_source = estimator_html[start:end]
        quick_entry_text = "10X15, 12X30,\n18 x 12\nbad line\n"

        script = f"""
const assert = require("assert");
function roundMoney(value) {{
  const n = Number(value);
  return Math.round((Number.isFinite(n) ? n : 0) * 100) / 100;
}}
{parser_source}
const result = parseMeasurementDimensionLines({json.dumps(quick_entry_text)}, "Quick entry");
assert.strictEqual(result.entries.length, 3);
assert.strictEqual(result.invalidLines.length, 1);
assert.deepStrictEqual(result.entries.map((entry) => entry.raw_text), ["10X15", "12X30", "18 x 12"]);
assert.deepStrictEqual(result.invalidLines, [{{ lineNumber: 3, text: "bad line" }}]);
console.log("quick-entry-mixed-input-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("quick-entry-mixed-input-ok", completed.stdout)

    def test_quick_entry_replaces_stale_failed_image_state_for_demo_flow(self) -> None:
        barkboys_material_tables = self._estimator_const_source("BARKBOYS_MATERIAL_TABLES")
        frequency_discount_percents = self._estimator_const_source("FREQUENCY_DISCOUNT_PERCENTS")
        to_num = self._estimator_source("function toNum(value) {", "function roundMoney(value) {")
        round_money = self._estimator_source("function roundMoney(value) {", "function normalizePrimaryJobType(value) {")
        measurement_depth = self._estimator_source("function defaultMeasurementDepthInches() {", "function nullableRoundedNumber(value) {")
        nullable_number = self._estimator_source("function nullableRoundedNumber(value) {", "function normalizeMeasurementReviewEntries(entries = []) {")
        normalize_entries = self._estimator_source("function normalizeMeasurementReviewEntries(entries = []) {", "function measurementReviewStats(entries = measurementReviewEntries) {")
        measurement_source_state = self._estimator_source("function normalizeMeasurementSource(source = \"\") {", "function measurementReviewStats(entries = measurementReviewEntries) {")
        measurement_stats = self._estimator_source("function measurementReviewStats(entries = measurementReviewEntries) {", "function measurementEntrySummary(entries = measurementReviewEntries) {")
        measurement_summary = self._estimator_source("function measurementEntrySummary(entries = measurementReviewEntries) {", "function measurementIntakeAssets() {")
        measurement_primary_source = self._estimator_source("function measurementPrimarySourceFilename(entries = measurementReviewEntries) {", "function measurementFlagLabel(entry = {}) {")
        quick_entry_error = self._estimator_source("function quickEntryErrorText(invalidLines = []) {", "function syncQuickEntryText(value = \"\") {")
        sync_quick_entry = self._estimator_source("function syncQuickEntryText(value = \"\") {", "function normalizeMeasurementParseRecoveryMessage(message) {")
        parse_dimensions = self._estimator_source('function parseMeasurementDimensionLines(text, sourceLabel = "Pasted dimensions") {', "function parsePastedMeasurementDimensions(text) {")
        parse_quick_entry = self._estimator_source("function parseQuickEntry(text) {", "function isMeaningfulMeasurementRow(entry = {}) {")
        measurement_row_helpers = self._estimator_source("function isMeaningfulMeasurementRow(entry = {}) {", "function quickEntryErrorText(invalidLines = []) {")
        confirmed_apply = self._estimator_source("function applyConfirmedMeasurementsToInputs() {", "function clearUnattachedMediaMeasurementRows(reason = \"upload-start\") {")
        status_helpers = self._estimator_source("function handwrittenMeasurementRowsPresent(entries = measurementReviewEntries) {", "function buildRowsFromParser(parserResult = {}, fallbackAsset = null) {")
        live_asset_state = self._estimator_source("function liveAssetStateStatus(assets = parseLifecycleAssets()) {", "function updateInternalEstimateStatus() {")
        measured_override = self._estimator_source("function measuredAreaOverrideValue() {", "function syncMeasuredAreaOverrideDepth() {")
        normalized_blower_material = self._estimator_source("function normalizedBlowerMaterial(value) {", "function formatInputNumber(value) {")
        format_input = self._estimator_source("function formatInputNumber(value) {", "function renderMaterialOptions() {")
        material_assumption = self._estimator_source("function materialAssumption(materialType) {", "function barkboysPricingTables() {")
        barkboys_pricing = self._estimator_source("function barkboysPricingTables() {", "function suggestedMaterialYards() {")
        material_yards = self._estimator_source("function suggestedMaterialYards() {", 'function buildMaterialLineDefaults(materialType = $("material-type").value, materialYards = resolvedMaterialYards()) {')
        build_material_line = self._estimator_source('function buildMaterialLineDefaults(materialType = $("material-type").value, materialYards = resolvedMaterialYards()) {', "function buildPlacementLineDefaults(materialYards = resolvedMaterialYards()) {")
        normalize_quote_line = self._estimator_source("function normalizeQuoteLine(item = {}) {", "function updateQuoteSummaryRows(data = null) {")
        collect_items = self._estimator_source("function collectItems() {", "function readAiInputs() {")
        apply_best_input = self._estimator_source("function applyBestMaterialInputSource() {", "function materialInputSourceLabel(source) {")
        material_input_label = self._estimator_source("function materialInputSourceLabel(source) {", "function applyConfirmedMeasurementsToInputs() {")
        pricing_payload = self._estimator_source("function pricingPayload() {", "function localPreview(payload) {")
        local_preview = self._estimator_source("function localPreview(payload) {", "async function previewQuote() {")
        quick_entry_apply = self._estimator_source("function applyQuickEntryMeasurements() {", "function addManualMeasurementRow() {")
        build_material_estimate = self._estimator_source("function buildMaterialEstimatePreset() {", "function linkedIntakeById(id) {")

        script = f"""
const assert = require("assert");
{barkboys_material_tables}
{frequency_discount_percents}
const BLOWABLE_MATERIALS = new Set(Object.keys(BARKBOYS_MATERIAL_TABLES));
const DEFAULT_PRICING_ASSUMPTIONS = {{
  measurement_defaults: {{ material_depth_inches: 2 }},
  material_assumptions: [
    {{ material_type: "hemlock", label: "Hemlock", default_selected: true }}
  ]
}};
let pricingAssumptions = DEFAULT_PRICING_ASSUMPTIONS;
let measurementSource = "image";
let activeMeasurementRows = [];
let confirmedMeasurementRows = [];
let measurementReviewEntries = [
  {{ entry_type: "dimension_pair", include: true, raw_text: "10x15", length_ft: 10, width_ft: 15, source_type: "site-media", source_asset_id: "asset-1", source_filename: "failed-yard.jpg" }}
];
let measurementEditVisible = false;
let measurementDetailsVisible = false;
let quoteLines = [];
let deliveryCitySource = "auto";
let deliveryPriceOverride = false;
let deliveryMode = "auto";
let lastMaterialStatus = "";
let lastPreviewData = null;
let lastPreviewSource = "";
const uploadedMediaFiles = [
  {{ id: 1, category: "site_media", status: "error", filename: "failed-yard.jpg", storageKey: "failed-yard.jpg" }}
];
const itemsEl = {{ querySelectorAll: () => [] }};
const dom = {{
  "measurement-quick-entry": {{ value: "10X15, 12X30, bad line" }},
  "measurement-quick-entry-mode": {{ value: "replace" }},
  "measurement-paste-input": {{ value: "" }},
  "measurement-quick-entry-status": {{ textContent: "Image parsing did not produce usable rows. Paste dimensions here with commas or new lines to continue immediately." }},
  "measurement-intake-status": {{ textContent: "Image parsing did not produce usable measurements. Use Quick Entry below to continue now, or Replace Image to retry." }},
  "photo-estimate-status": {{ textContent: "Upload or parse failed" }},
  "measurement-review-summary": {{ textContent: "" }},
  "material-depth-inches": {{ value: "2" }},
  "material-yards": {{ value: "0.93" }},
  "material-type": {{ value: "hemlock" }},
  "material-estimate-mode": {{ value: "table_price" }},
  "frequency": {{ value: "one_time" }},
  "zone-modifier": {{ value: "0" }},
  "tax-rate": {{ value: "0" }},
  "lot-size": {{ value: "150" }},
  "edge-length": {{ value: "50" }},
}};
function $(id) {{
  if (!dom[id]) {{
    dom[id] = {{ value: "", textContent: "", className: "" }};
  }}
  return dom[id];
}}
function setStatus(id, message, tone) {{
  if (id === "material-estimate-status") {{
    lastMaterialStatus = message;
  }}
  const node = $(id);
  node.textContent = message;
  node.className = tone || "";
}}
function logMediaEvent() {{}}
function dedupeUploadedMediaRecords(items) {{
  return Array.isArray(items) ? items.slice() : [];
}}
function parseLifecycleAssets() {{
  return dedupeUploadedMediaRecords(uploadedMediaFiles).filter((item) => item.category !== "exclusion");
}}
function latestMeasurementIntakeAsset() {{
  return parseLifecycleAssets()[parseLifecycleAssets().length - 1] || null;
}}
function renderMeasurementReview() {{}}
function measurementWarningText() {{ return "Looks reasonable"; }}
function updateMeasuredAreaDifferenceStatus() {{}}
function syncMaterialPricingSelections() {{}}
function renderWorkflowGuide() {{}}
function queueBrowserDraftSave() {{}}
function renderLoadedMediaSummary() {{}}
function updateInternalEstimateStatus() {{}}
function updateUploadEstimateButtonState() {{}}
function areaBasedMaterialYards() {{ return 0; }}
function updateDeliveryFeeHint() {{}}
function allSelectedSiteMediaFiles() {{
  return parseLifecycleAssets().filter((item) => item.category === "site_media");
}}
function selectedMeasurementPhotos() {{ return []; }}
function selectedMeasurementReferenceFiles() {{ return []; }}
function selectedExclusionPhotos() {{ return []; }}
function renderMeasurementIntakeCard() {{
  if (measurementEntrySummary().included.length) {{
    setStatus("measurement-intake-status", "Measurements detected. Confirm the rows marked Use, then click Build Material Estimate below.", "ok");
  }}
}}
function updatePhotoSelectionStatus() {{
  renderMeasurementIntakeCard();
  const snapshot = liveAssetStateStatus(parseLifecycleAssets());
  if (snapshot) {{
    setStatus("photo-estimate-status", snapshot.message, snapshot.tone);
  }}
}}
function previewQuote() {{
  lastPreviewData = localPreview(pricingPayload());
}}
function commitMaterialEstimateRows(rows = []) {{
  quoteLines = (Array.isArray(rows) ? rows : []).map((item) => normalizeQuoteLine(item));
}}
{to_num}
{round_money}
{measurement_depth}
{nullable_number}
{normalize_entries}
{measurement_source_state}
{measurement_stats}
{measurement_summary}
{measurement_primary_source}
{quick_entry_error}
{sync_quick_entry}
{parse_dimensions}
{parse_quick_entry}
{measurement_row_helpers}
{measured_override}
{status_helpers}
{live_asset_state}
{confirmed_apply}
{normalized_blower_material}
{format_input}
{material_assumption}
{barkboys_pricing}
{material_yards}
{build_material_line}
{normalize_quote_line}
{collect_items}
{apply_best_input}
{material_input_label}
{pricing_payload}
{local_preview}
{quick_entry_apply}
{build_material_estimate}
assert.strictEqual(measurementEntrySummary().totalArea, 150);
assert.strictEqual($("material-yards").value, "0.93");
applyQuickEntryMeasurements();
const summary = measurementEntrySummary();
assert.strictEqual(summary.dimensionEntries.length, 2);
assert.strictEqual(summary.totalArea, 510);
assert.strictEqual(summary.totalYards, 3.15);
assert.strictEqual($("lot-size").value, "510");
assert.strictEqual($("material-yards").value, "3.15");
assert.strictEqual(
  $("measurement-quick-entry-status").textContent,
  "Quick Entry loaded 2 rows · 510 sq ft · 3.15 cu yd. 1 invalid line ignored (line 1: bad line)."
);
assert.strictEqual(
  $("measurement-intake-status").textContent,
  "Measurements detected. Confirm the rows marked Use, then click Build Material Estimate below."
);
assert.strictEqual(
  $("photo-estimate-status").textContent,
  "Measurements detected. Review the worksheet before building the quote."
);
buildMaterialEstimatePreset();
assert.strictEqual(quoteLines.length, 1);
assert.strictEqual(quoteLines[0].quantity, 3.15);
assert.strictEqual(
  lastMaterialStatus,
  "Material estimate built from confirmed measurements for hemlock: requested 3.15 yd, billed at the next BarkBoys table row of 4 yd."
);
assert.ok(lastPreviewData);
assert.strictEqual(lastPreviewData.total, 240);
console.log("quick-entry-stale-state-demo-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("quick-entry-stale-state-demo-ok", completed.stdout)

    def test_browser_draft_restore_reapplies_quick_entry_rows(self) -> None:
        restore_source = self._estimator_source("async function restoreBrowserDraftState() {", "function looksLikeHtmlResponse(text) {")

        script = r"""
const assert = require("assert");
const BROWSER_DRAFT_STORAGE_KEY = "barkboysEstimatorBrowserDraftV3";
const DRAFT_TOKEN_STORAGE_KEY = "barkboysEstimatorDraftToken";
let browserDraftRestoreInFlight = false;
let measurementReviewEntries = [];
let measurementSource = "";
let activeMeasurementRows = [];
let confirmedMeasurementRows = [];
let quoteLines = [];
let deliveryCitySource = "auto";
let deliveryPriceOverride = false;
let deliveryMode = "auto";
let lastPreviewData = null;
let lastPreviewSource = "";
let measurementInlineExpanded = false;
let measurementDetailsVisible = false;
let measurementEditVisible = false;
let appliedQuickEntry = 0;
let appliedPastedDimensions = 0;
let restoredPreview = 0;
let draftStatus = "";
const dom = {
  "measurement-quick-entry": { value: "" },
  "measurement-paste-input": { value: "" },
};
const storage = new Map();
storage.set(BROWSER_DRAFT_STORAGE_KEY, JSON.stringify({
  draftToken: "draft-123",
  fields: {
    "measurement-quick-entry": "10X15, 12X30, bad line"
  },
  checks: {},
  measurementReviewEntries: [],
  quoteLines: [],
  preview: null,
  ui: {}
}));
const window = {
  localStorage: {
    getItem(key) {
      return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
      storage.set(key, String(value));
    }
  }
};
function $(id) {
  if (!dom[id]) {
    dom[id] = { value: "", checked: false, textContent: "" };
  }
  return dom[id];
}
function normalizeMeasurementReviewEntries(entries = []) {
  return Array.isArray(entries) ? entries : [];
}
function normalizeMeasurementSource(source = "") {
  return String(source || "").trim();
}
function deriveMeasurementSourceFromEntries() {
  return "";
}
function syncActiveMeasurementState(source = measurementSource) {
  measurementSource = source || "";
  return {
    source: measurementSource,
    rows: activeMeasurementRows,
    confirmedRows: confirmedMeasurementRows,
  };
}
function normalizeQuoteLine(item = {}) {
  return item;
}
function renderQuoteLines() {}
function updateZipFieldStatus() {}
function renderMeasurementReview() {}
function updateMeasuredAreaDifferenceStatus() {}
function updateDeliveryFeeHint() {}
async function restoreDraftUploadedAssets() {}
function parseLifecycleAssets() {
  return [];
}
function measurementEntrySummary() {
  return { included: [] };
}
function applyQuickEntryMeasurements() {
  appliedQuickEntry += 1;
  return true;
}
function applyPastedDimensions() {
  appliedPastedDimensions += 1;
  return true;
}
async function previewQuote() {
  restoredPreview += 1;
}
function setDraftStatus(message) {
  draftStatus = message;
}
""" + restore_source + r"""
(async () => {
  const restored = await restoreBrowserDraftState();
  assert.strictEqual(restored, true);
  assert.strictEqual($("measurement-quick-entry").value, "10X15, 12X30, bad line");
  assert.strictEqual(appliedQuickEntry, 1);
  assert.strictEqual(appliedPastedDimensions, 0);
  assert.strictEqual(restoredPreview, 1);
  assert.strictEqual(draftStatus, "Draft restored");
  assert.strictEqual(browserDraftRestoreInFlight, false);
  console.log("browser-draft-quick-entry-restore-ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("browser-draft-quick-entry-restore-ok", completed.stdout)

    def test_browser_draft_restore_reapplies_quick_entry_over_stale_failed_image_state(self) -> None:
        to_num = self._estimator_source("function toNum(value) {", "function roundMoney(value) {")
        round_money = self._estimator_source("function roundMoney(value) {", "function normalizePrimaryJobType(value) {")
        measurement_depth = self._estimator_source("function defaultMeasurementDepthInches() {", "function nullableRoundedNumber(value) {")
        nullable_number = self._estimator_source("function nullableRoundedNumber(value) {", "function normalizeMeasurementReviewEntries(entries = []) {")
        normalize_entries = self._estimator_source("function normalizeMeasurementReviewEntries(entries = []) {", "function measurementReviewStats(entries = measurementReviewEntries) {")
        measurement_source_state = self._estimator_source("function normalizeMeasurementSource(source = \"\") {", "function measurementReviewStats(entries = measurementReviewEntries) {")
        measurement_stats = self._estimator_source("function measurementReviewStats(entries = measurementReviewEntries) {", "function measurementEntrySummary(entries = measurementReviewEntries) {")
        measurement_summary = self._estimator_source("function measurementEntrySummary(entries = measurementReviewEntries) {", "function measurementIntakeAssets() {")
        measurement_primary_source = self._estimator_source("function measurementPrimarySourceFilename(entries = measurementReviewEntries) {", "function measurementFlagLabel(entry = {}) {")
        quick_entry_error = self._estimator_source("function quickEntryErrorText(invalidLines = []) {", "function syncQuickEntryText(value = \"\") {")
        sync_quick_entry = self._estimator_source("function syncQuickEntryText(value = \"\") {", "function normalizeMeasurementParseRecoveryMessage(message) {")
        parse_dimensions = self._estimator_source('function parseMeasurementDimensionLines(text, sourceLabel = "Pasted dimensions") {', "function parsePastedMeasurementDimensions(text) {")
        parse_quick_entry = self._estimator_source("function parseQuickEntry(text) {", "function isMeaningfulMeasurementRow(entry = {}) {")
        measurement_row_helpers = self._estimator_source("function isMeaningfulMeasurementRow(entry = {}) {", "function quickEntryErrorText(invalidLines = []) {")
        format_input = self._estimator_source("function formatInputNumber(value) {", "function renderMaterialOptions() {")
        status_helpers = self._estimator_source("function handwrittenMeasurementRowsPresent(entries = measurementReviewEntries) {", "function buildRowsFromParser(parserResult = {}, fallbackAsset = null) {")
        live_asset_state = self._estimator_source("function liveAssetStateStatus(assets = parseLifecycleAssets()) {", "function updateInternalEstimateStatus() {")
        confirmed_apply = self._estimator_source("function applyConfirmedMeasurementsToInputs() {", "function clearUnattachedMediaMeasurementRows(reason = \"upload-start\") {")
        quick_entry_apply = self._estimator_source("function applyQuickEntryMeasurements() {", "function addManualMeasurementRow() {")
        restore_source = self._estimator_source("async function restoreBrowserDraftState() {", "function looksLikeHtmlResponse(text) {")

        script = f"""
const assert = require("assert");
const BROWSER_DRAFT_STORAGE_KEY = "barkboysEstimatorBrowserDraftV3";
const DRAFT_TOKEN_STORAGE_KEY = "barkboysEstimatorDraftToken";
let browserDraftRestoreInFlight = false;
let measurementSource = "image";
let activeMeasurementRows = [];
let confirmedMeasurementRows = [];
let measurementReviewEntries = [];
let quoteLines = [];
let deliveryCitySource = "auto";
let deliveryPriceOverride = false;
let deliveryMode = "auto";
let lastPreviewData = null;
let lastPreviewSource = "";
let measurementInlineExpanded = false;
let measurementDetailsVisible = false;
let measurementEditVisible = false;
let draftStatus = "";
const uploadedMediaFiles = [
  {{ id: 1, category: "site_media", status: "error", filename: "failed-yard.jpg", storageKey: "failed-yard.jpg" }}
];
const itemsEl = {{ querySelectorAll: () => [] }};
const dom = {{
  "measurement-quick-entry": {{ value: "" }},
  "measurement-quick-entry-mode": {{ value: "replace" }},
  "measurement-paste-input": {{ value: "" }},
  "measurement-quick-entry-status": {{ textContent: "Image parsing did not produce usable rows. Paste dimensions here with commas or new lines to continue immediately.", className: "warn" }},
  "measurement-intake-status": {{ textContent: "Image parsing did not produce usable measurements. Use Quick Entry below to continue now, or Replace Image to retry.", className: "warn" }},
  "photo-estimate-status": {{ textContent: "Upload or parse failed", className: "error" }},
  "measurement-review-summary": {{ textContent: "" }},
  "material-depth-inches": {{ value: "2" }},
  "material-yards": {{ value: "0.93" }},
  "material-type": {{ value: "hemlock" }},
  "lot-size": {{ value: "150" }},
  "edge-length": {{ value: "50" }},
}};
const storage = new Map();
storage.set(BROWSER_DRAFT_STORAGE_KEY, JSON.stringify({{
  draftToken: "draft-123",
  fields: {{
    "measurement-quick-entry": "10X15, 12X30, bad line",
    "measurement-quick-entry-mode": "replace",
    "measurement-paste-input": "",
    "material-depth-inches": "2",
    "material-yards": "0.93",
    "lot-size": "150",
    "edge-length": "50"
  }},
  checks: {{}},
  measurementSource: "image",
  measurementReviewEntries: [
    {{ entry_type: "dimension_pair", include: true, raw_text: "10x15", length_ft: 10, width_ft: 15, source_type: "site-media", source_asset_id: "asset-1", source_filename: "failed-yard.jpg" }}
  ],
  quoteLines: [],
  preview: null,
  ui: {{}}
}}));
const window = {{
  localStorage: {{
    getItem(key) {{
      return storage.has(key) ? storage.get(key) : null;
    }},
    setItem(key, value) {{
      storage.set(key, String(value));
    }}
  }}
}};
function $(id) {{
  if (!dom[id]) {{
    dom[id] = {{ value: "", checked: false, textContent: "", className: "" }};
  }}
  return dom[id];
}}
function setStatus(id, message, tone) {{
  const node = $(id);
  node.textContent = message;
  node.className = tone || "";
}}
function logMediaEvent() {{}}
function measurementWarningText() {{
  return "Looks reasonable";
}}
function dedupeUploadedMediaRecords(items) {{
  return Array.isArray(items) ? items.slice() : [];
}}
function parseLifecycleAssets() {{
  return dedupeUploadedMediaRecords(uploadedMediaFiles).filter((item) => item.category !== "exclusion");
}}
function latestMeasurementIntakeAsset() {{
  const assets = parseLifecycleAssets();
  return assets.length ? assets[assets.length - 1] : null;
}}
function normalizeQuoteLine(item = {{}}) {{
  return item;
}}
function renderQuoteLines() {{}}
function updateZipFieldStatus() {{}}
function renderMeasurementReview() {{}}
function updateMeasuredAreaDifferenceStatus() {{}}
function syncMaterialPricingSelections() {{}}
function renderWorkflowGuide() {{}}
function queueBrowserDraftSave() {{}}
function renderLoadedMediaSummary() {{}}
function updateInternalEstimateStatus() {{}}
function updateUploadEstimateButtonState() {{}}
function updateDeliveryFeeHint() {{}}
function areaBasedMaterialYards() {{
  return 0;
}}
function allSelectedSiteMediaFiles() {{
  return parseLifecycleAssets().filter((item) => item.category === "site_media");
}}
function selectedMeasurementPhotos() {{
  return [];
}}
function selectedMeasurementReferenceFiles() {{
  return [];
}}
function selectedExclusionPhotos() {{
  return [];
}}
function renderMeasurementIntakeCard() {{
  if (measurementEntrySummary().included.length) {{
    setStatus("measurement-intake-status", "Measurements detected. Confirm the rows marked Use, then click Build Material Estimate below.", "ok");
  }}
}}
function updatePhotoSelectionStatus() {{
  renderMeasurementIntakeCard();
  const snapshot = liveAssetStateStatus(parseLifecycleAssets());
  if (snapshot) {{
    setStatus("photo-estimate-status", snapshot.message, snapshot.tone);
  }}
}}
async function restoreDraftUploadedAssets() {{}}
async function previewQuote() {{
  lastPreviewData = {{ restored: true }};
}}
function setDraftStatus(message) {{
  draftStatus = message;
}}
{to_num}
{round_money}
{measurement_depth}
{nullable_number}
{normalize_entries}
{measurement_source_state}
{measurement_stats}
{measurement_summary}
{measurement_primary_source}
{quick_entry_error}
{sync_quick_entry}
{parse_dimensions}
{parse_quick_entry}
{measurement_row_helpers}
{format_input}
{status_helpers}
{live_asset_state}
{confirmed_apply}
{quick_entry_apply}
{restore_source}
(async () => {{
  const restored = await restoreBrowserDraftState();
  const summary = measurementEntrySummary();
  assert.strictEqual(restored, true);
  assert.strictEqual(currentMeasurementSource(), "quick-entry");
  assert.strictEqual(summary.dimensionEntries.length, 2);
  assert.strictEqual(summary.totalArea, 510);
  assert.strictEqual(summary.totalYards, 3.15);
  assert.strictEqual($("lot-size").value, "510");
  assert.strictEqual($("material-yards").value, "3.15");
  assert.strictEqual(
    $("measurement-quick-entry-status").textContent,
    "Quick Entry loaded 2 rows · 510 sq ft · 3.15 cu yd. 1 invalid line ignored (line 1: bad line)."
  );
  assert.strictEqual(
    $("measurement-intake-status").textContent,
    "Measurements detected. Confirm the rows marked Use, then click Build Material Estimate below."
  );
  assert.strictEqual(
    $("photo-estimate-status").textContent,
    "Measurements detected. Review the worksheet before building the quote."
  );
  assert.strictEqual(draftStatus, "Draft restored");
  console.log("browser-draft-stale-image-recovery-ok");
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("browser-draft-stale-image-recovery-ok", completed.stdout)

    def test_measurement_review_summary_matches_multiline_quick_entry_totals(self) -> None:
        estimator_html = self.ESTIMATOR_HTML_PATH.read_text()
        parser_start = estimator_html.index('function parseMeasurementDimensionLines(text, sourceLabel = "Pasted dimensions") {')
        parser_end = estimator_html.index("function parsePastedMeasurementDimensions(text) {", parser_start)
        parser_source = estimator_html[parser_start:parser_end]
        normalize_start = estimator_html.index("function normalizeMeasurementReviewEntries(entries = []) {")
        normalize_end = estimator_html.index("function measurementReviewStats(entries = measurementReviewEntries) {", normalize_start)
        normalize_source = estimator_html[normalize_start:normalize_end]
        summary_start = estimator_html.index("function measurementEntrySummary(entries = measurementReviewEntries) {")
        summary_end = estimator_html.index("function measurementIntakeAssets() {", summary_start)
        summary_source = estimator_html[summary_start:summary_end]
        quick_entry_text = "22x40\n225x10\n18 x 12\nbad line\n"

        script = f"""
const assert = require("assert");
function toNum(value) {{
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}}
function roundMoney(value) {{
  return Math.round((toNum(value) + Number.EPSILON) * 100) / 100;
}}
function measurementDepthInches() {{
  return 2;
}}
function nullableRoundedNumber(value) {{
  if (value === null || value === undefined || String(value).trim?.() === "") {{
    return null;
  }}
  const parsed = Number(value);
  return Number.isFinite(parsed) ? roundMoney(parsed) : null;
}}
function logMediaEvent() {{}}
let measurementReviewEntries = [];
{parser_source}
{normalize_source}
{summary_source}
const parsed = parseMeasurementDimensionLines({json.dumps(quick_entry_text)}, "Quick entry");
const summary = measurementEntrySummary(parsed.entries);
assert.deepStrictEqual(parsed.invalidLines, [{{ lineNumber: 4, text: "bad line" }}]);
assert.strictEqual(summary.dimensionEntries.length, 3);
assert.strictEqual(summary.totalArea, 3346);
assert.strictEqual(summary.totalYards, 20.65);
console.log("measurement-review-summary-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("measurement-review-summary-ok", completed.stdout)

    def test_build_material_estimate_prefers_confirmed_measurement_rows_over_stale_quick_entry_text(self) -> None:
        estimator_html = self.ESTIMATOR_HTML_PATH.read_text()
        apply_start = estimator_html.index("function applyBestMaterialInputSource() {")
        apply_end = estimator_html.index("function materialInputSourceLabel(source) {", apply_start)
        apply_source = estimator_html[apply_start:apply_end]
        label_start = estimator_html.index("function materialInputSourceLabel(source) {")
        label_end = estimator_html.index("function applyConfirmedMeasurementsToInputs() {", label_start)
        label_source = estimator_html[label_start:label_end]

        script = f"""
const assert = require("assert");
let confirmedApplied = 0;
let quickEntryApplied = 0;
let pastedApplied = 0;
let manualAreaApplied = 0;
let confirmedMeasurementRows = [];
function measurementEntrySummary() {{
  return {{ included: [{{ raw_text: "22x40" }}] }};
}}
function currentMeasurementSource() {{
  return "";
}}
function syncActiveMeasurementState() {{}}
function applyConfirmedMeasurementsToInputs() {{
  confirmedApplied += 1;
}}
function applyQuickEntryMeasurements() {{
  quickEntryApplied += 1;
  return true;
}}
function applyPastedDimensions() {{
  pastedApplied += 1;
  return true;
}}
function hasMeasuredAreaOverride() {{
  return false;
}}
function recalculateFromMeasuredAreaOverride() {{
  manualAreaApplied += 1;
}}
function $(id) {{
  if (id === "measurement-quick-entry") {{
    return {{ value: "10X15, 12X30, bad line" }};
  }}
  if (id === "measurement-paste-input") {{
    return {{ value: "22x40" }};
  }}
  return {{ value: "" }};
}}
{apply_source}
{label_source}
const source = applyBestMaterialInputSource();
assert.strictEqual(source, "confirmed_measurements");
assert.strictEqual(materialInputSourceLabel(source), "confirmed measurements");
assert.strictEqual(confirmedApplied, 1);
assert.strictEqual(quickEntryApplied, 0);
assert.strictEqual(pastedApplied, 0);
assert.strictEqual(manualAreaApplied, 0);
console.log("confirmed-measurements-preferred-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("confirmed-measurements-preferred-ok", completed.stdout)

    def test_build_material_estimate_and_review_totals_match_demo_numbers(self) -> None:
        barkboys_material_tables = self._estimator_const_source("BARKBOYS_MATERIAL_TABLES")
        frequency_discount_percents = self._estimator_const_source("FREQUENCY_DISCOUNT_PERCENTS")
        to_num = self._estimator_source("function toNum(value) {", "function roundMoney(value) {")
        round_money = self._estimator_source("function roundMoney(value) {", "function normalizePrimaryJobType(value) {")
        measurement_depth = self._estimator_source("function defaultMeasurementDepthInches() {", "function nullableRoundedNumber(value) {")
        nullable_number = self._estimator_source("function nullableRoundedNumber(value) {", "function normalizeMeasurementReviewEntries(entries = []) {")
        normalize_entries = self._estimator_source("function normalizeMeasurementReviewEntries(entries = []) {", "function measurementReviewStats(entries = measurementReviewEntries) {")
        measurement_summary = self._estimator_source("function measurementEntrySummary(entries = measurementReviewEntries) {", "function measurementIntakeAssets() {")
        measured_override = self._estimator_source("function measuredAreaOverrideValue() {", "function syncMeasuredAreaOverrideDepth() {")
        normalized_blower_material = self._estimator_source("function normalizedBlowerMaterial(value) {", "function formatInputNumber(value) {")
        format_input = self._estimator_source("function formatInputNumber(value) {", "function renderMaterialOptions() {")
        material_assumption = self._estimator_source("function materialAssumption(materialType) {", "function barkboysPricingTables() {")
        barkboys_pricing = self._estimator_source("function barkboysPricingTables() {", "function suggestedMaterialYards() {")
        material_yards = self._estimator_source("function suggestedMaterialYards() {", 'function buildMaterialLineDefaults(materialType = $("material-type").value, materialYards = resolvedMaterialYards()) {')
        build_material_line = self._estimator_source('function buildMaterialLineDefaults(materialType = $("material-type").value, materialYards = resolvedMaterialYards()) {', "function buildPlacementLineDefaults(materialYards = resolvedMaterialYards()) {")
        normalize_quote_line = self._estimator_source("function normalizeQuoteLine(item = {}) {", "function updateQuoteSummaryRows(data = null) {")
        collect_items = self._estimator_source("function collectItems() {", "function readAiInputs() {")
        apply_best_input = self._estimator_source("function applyBestMaterialInputSource() {", "function materialInputSourceLabel(source) {")
        material_input_label = self._estimator_source("function materialInputSourceLabel(source) {", "function applyConfirmedMeasurementsToInputs() {")
        pricing_payload = self._estimator_source("function pricingPayload() {", "function localPreview(payload) {")
        local_preview = self._estimator_source("function localPreview(payload) {", "async function previewQuote() {")
        build_material_estimate = self._estimator_source("function buildMaterialEstimatePreset() {", "function linkedIntakeById(id) {")

        script = f"""
const assert = require("assert");
{barkboys_material_tables}
{frequency_discount_percents}
const BLOWABLE_MATERIALS = new Set(Object.keys(BARKBOYS_MATERIAL_TABLES));
const DEFAULT_PRICING_ASSUMPTIONS = {{
  measurement_defaults: {{ material_depth_inches: 2 }},
  material_assumptions: [
    {{ material_type: "hemlock", label: "Hemlock", default_selected: true }}
  ]
}};
let pricingAssumptions = DEFAULT_PRICING_ASSUMPTIONS;
let measurementSource = "";
let activeMeasurementRows = [];
let confirmedMeasurementRows = [];
let measurementReviewEntries = [
  {{ entry_type: "dimension_pair", include: true, raw_text: "22x40", length_ft: 22, width_ft: 40 }},
  {{ entry_type: "dimension_pair", include: true, raw_text: "225x10", length_ft: 225, width_ft: 10 }},
  {{ entry_type: "dimension_pair", include: true, raw_text: "18 x 12", length_ft: 18, width_ft: 12 }},
];
let quoteLines = [];
let deliveryCitySource = "auto";
let deliveryPriceOverride = false;
let deliveryMode = "auto";
let lastStatus = "";
let previewData = null;
const itemsEl = {{ querySelectorAll: () => [] }};
const dom = {{
  "measurement-quick-entry": {{ value: "10X15, 12X30, bad line" }},
  "measurement-paste-input": {{ value: "22x40" }},
  "material-depth-inches": {{ value: "2" }},
  "material-yards": {{ value: "" }},
  "material-type": {{ value: "hemlock" }},
  "material-estimate-mode": {{ value: "table_price" }},
  "frequency": {{ value: "one_time" }},
  "zone-modifier": {{ value: "0" }},
  "tax-rate": {{ value: "0" }},
}};
function $(id) {{
  return dom[id] || {{ value: "", textContent: "" }};
}}
{to_num}
{round_money}
{measurement_depth}
{nullable_number}
function logMediaEvent() {{}}
{normalize_entries}
function currentMeasurementSource() {{
  return "";
}}
function syncActiveMeasurementState() {{}}
{measurement_summary}
{measured_override}
{normalized_blower_material}
{format_input}
{material_assumption}
{barkboys_pricing}
{material_yards}
{build_material_line}
{normalize_quote_line}
{collect_items}
{apply_best_input}
{material_input_label}
{pricing_payload}
{local_preview}
function applyConfirmedMeasurementsToInputs() {{}}
function applyQuickEntryMeasurements() {{
  throw new Error("stale quick entry should not be reparsed");
}}
function applyPastedDimensions() {{
  throw new Error("stale pasted dimensions should not be reapplied");
}}
function hasMeasuredAreaOverride() {{
  return false;
}}
function recalculateFromMeasuredAreaOverride() {{}}
function updateDeliveryFeeHint() {{}}
function setStatus(_id, message, _tone) {{
  lastStatus = message;
}}
function commitMaterialEstimateRows(rows = []) {{
  quoteLines = (Array.isArray(rows) ? rows : []).map((item) => normalizeQuoteLine(item));
}}
function previewQuote() {{
  previewData = localPreview(pricingPayload());
}}
{build_material_estimate}
const summary = measurementEntrySummary();
assert.strictEqual(summary.totalArea, 3346);
        assert.strictEqual(summary.totalYards, 20.65);
        buildMaterialEstimatePreset();
        assert.strictEqual(quoteLines.length, 1);
        assert.strictEqual(quoteLines[0].name, "Hemlock Material Estimate (1 load)");
        assert.strictEqual(quoteLines[0].description, "Load plan: 21 yd");
        assert.strictEqual(quoteLines[0].quantity, 20.65);
assert.strictEqual(
  lastStatus,
  "Material estimate built from confirmed measurements for hemlock: requested 20.65 yd, billed at the next BarkBoys table row of 21 yd."
);
assert.ok(previewData);
assert.strictEqual(previewData.items.length, 1);
assert.strictEqual(previewData.subtotal, 1090);
assert.strictEqual(previewData.total, 1090);
console.log("build-material-estimate-demo-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("build-material-estimate-demo-ok", completed.stdout)

    def test_build_material_estimate_persists_real_quote_line_for_save_payload(self) -> None:
        barkboys_material_tables = self._estimator_const_source("BARKBOYS_MATERIAL_TABLES")
        frequency_discount_percents = self._estimator_const_source("FREQUENCY_DISCOUNT_PERCENTS")
        to_num = self._estimator_source("function toNum(value) {", "function roundMoney(value) {")
        round_money = self._estimator_source("function roundMoney(value) {", "function normalizePrimaryJobType(value) {")
        measurement_depth = self._estimator_source("function defaultMeasurementDepthInches() {", "function nullableRoundedNumber(value) {")
        nullable_number = self._estimator_source("function nullableRoundedNumber(value) {", "function normalizeMeasurementReviewEntries(entries = []) {")
        normalize_entries = self._estimator_source("function normalizeMeasurementReviewEntries(entries = []) {", "function measurementReviewStats(entries = measurementReviewEntries) {")
        measurement_summary = self._estimator_source("function measurementEntrySummary(entries = measurementReviewEntries) {", "function measurementIntakeAssets() {")
        measured_override = self._estimator_source("function measuredAreaOverrideValue() {", "function syncMeasuredAreaOverrideDepth() {")
        normalized_blower_material = self._estimator_source("function normalizedBlowerMaterial(value) {", "function formatInputNumber(value) {")
        format_input = self._estimator_source("function formatInputNumber(value) {", "function renderMaterialOptions() {")
        material_assumption = self._estimator_source("function materialAssumption(materialType) {", "function barkboysPricingTables() {")
        barkboys_pricing = self._estimator_source("function barkboysPricingTables() {", "function suggestedMaterialYards() {")
        material_yards = self._estimator_source("function suggestedMaterialYards() {", 'function buildMaterialLineDefaults(materialType = $("material-type").value, materialYards = resolvedMaterialYards()) {')
        build_material_line = self._estimator_source('function buildMaterialLineDefaults(materialType = $("material-type").value, materialYards = resolvedMaterialYards()) {', "function buildPlacementLineDefaults(materialYards = resolvedMaterialYards()) {")
        normalize_quote_line = self._estimator_source("function normalizeQuoteLine(item = {}) {", "function updateQuoteSummaryRows(data = null) {")
        collect_items = self._estimator_source("function collectItems() {", "function readAiInputs() {")
        quote_lines_debug = self._estimator_source("function quoteLinesSnapshot(lines = quoteLines) {", "function readAiInputs() {")
        apply_best_input = self._estimator_source("function applyBestMaterialInputSource() {", "function materialInputSourceLabel(source) {")
        material_input_label = self._estimator_source("function materialInputSourceLabel(source) {", "function applyConfirmedMeasurementsToInputs() {")
        pricing_payload = self._estimator_source("function pricingPayload() {", "function localPreview(payload) {")
        local_preview = self._estimator_source("function localPreview(payload) {", "async function previewQuote() {")
        auto_line_helpers = self._estimator_source("function isMaterialEstimateLineName(name = \"\") {", "function requestedYardsFromMaterialUnit(unitText) {")
        commit_material_rows = self._estimator_source("function commitMaterialEstimateRows(rows = []) {", "function requestedYardsFromMaterialUnit(unitText) {")
        build_material_estimate = self._estimator_source("function buildMaterialEstimatePreset() {", "function linkedIntakeById(id) {")

        script = f"""
const assert = require("assert");
{barkboys_material_tables}
{frequency_discount_percents}
const BLOWABLE_MATERIALS = new Set(Object.keys(BARKBOYS_MATERIAL_TABLES));
const DEFAULT_PRICING_ASSUMPTIONS = {{
  measurement_defaults: {{ material_depth_inches: 2 }},
  material_assumptions: [
    {{ material_type: "hemlock", label: "Hemlock", default_selected: true }}
  ]
}};
let pricingAssumptions = DEFAULT_PRICING_ASSUMPTIONS;
let measurementSource = "";
let activeMeasurementRows = [];
let confirmedMeasurementRows = [];
let measurementReviewEntries = [
  {{ entry_type: "dimension_pair", include: true, raw_text: "100x100", length_ft: 100, width_ft: 100 }},
  {{ entry_type: "dimension_pair", include: true, raw_text: "80.68x75", length_ft: 80.68, width_ft: 75 }}
];
let quoteLines = [];
let deliveryCitySource = "auto";
let deliveryPriceOverride = false;
let deliveryMode = "auto";
let lastStatus = "";
let previewData = null;
const itemsEl = {{ querySelectorAll: () => quoteLines.map(() => null) }};
const dom = {{
  "material-depth-inches": {{ value: "2" }},
  "material-yards": {{ value: "" }},
  "material-type": {{ value: "hemlock" }},
  "material-estimate-mode": {{ value: "table_price" }},
  "frequency": {{ value: "one_time" }},
  "zone-modifier": {{ value: "0" }},
  "tax-rate": {{ value: "0" }},
}};
function $(id) {{
  return dom[id] || {{ value: "", textContent: "", className: "" }};
}}
function logMediaEvent() {{}}
function syncActiveMeasurementState() {{}}
function currentMeasurementSource() {{
  return "";
}}
function applyConfirmedMeasurementsToInputs() {{}}
function applyQuickEntryMeasurements() {{
  throw new Error("quick entry should not run");
}}
function applyPastedDimensions() {{
  throw new Error("pasted dimensions should not run");
}}
function hasMeasuredAreaOverride() {{
  return false;
}}
function recalculateFromMeasuredAreaOverride() {{}}
function updateDeliveryFeeHint() {{}}
function renderQuoteLines() {{}}
function syncMaterialInputsFromItems() {{}}
function setStatus(_id, message, _tone) {{
  lastStatus = message;
}}
{to_num}
{round_money}
{measurement_depth}
{nullable_number}
{normalize_entries}
{measurement_summary}
{measured_override}
{normalized_blower_material}
{format_input}
{material_assumption}
{barkboys_pricing}
{material_yards}
{build_material_line}
{normalize_quote_line}
{collect_items}
{quote_lines_debug}
function logQuoteLinesState() {{}}
{apply_best_input}
{material_input_label}
{pricing_payload}
{local_preview}
{auto_line_helpers}
{commit_material_rows}
function previewQuote() {{
  previewData = localPreview(pricingPayload());
}}
{build_material_estimate}

const summary = measurementEntrySummary();
assert.strictEqual(summary.totalArea, 16051);
assert.strictEqual(summary.totalYards, 99.08);
buildMaterialEstimatePreset();
const payload = pricingPayload();
assert.ok(Array.isArray(quoteLines));
assert.strictEqual(quoteLines.length, 1);
assert.strictEqual(payload.items.length, 1);
assert.strictEqual(payload.items[0].name, quoteLines[0].name);
assert.ok(/Hemlock Material Estimate/.test(payload.items[0].name));
assert.ok(payload.items[0].description.includes("Load plan:"));
assert.ok(payload.items[0].base_price > 0);
assert.ok(payload.items[0].quantity > 0);
assert.ok(previewData);
assert.strictEqual(previewData.items.length, 1);
assert.ok(previewData.subtotal > 0);
assert.ok(previewData.total > 0);
assert.strictEqual(lastStatus.includes("Material estimate built from"), true);
console.log("material-line-persisted-ok");
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("material-line-persisted-ok", completed.stdout)

    def test_quote_item_description_persists_through_save_and_reload(self) -> None:
        with self.SessionLocal() as db:
            payload = QuoteCreate(
                job={
                    "customer_name": "James Shingleton",
                    "phone": "5033832948",
                    "email": "stjames22@gmail.com",
                    "address": "5525 Wigeon St SE",
                    "zip_code": "97302",
                    "terrain_type": "mixed",
                    "primary_job_type": "mulch refresh",
                    "sales_rep": "BarkBoys Sales",
                    "lead_status": "quoted",
                    "source": "Referral / Public Estimator",
                },
                items=[
                    {
                        "name": "Hemlock Material Estimate (5 loads)",
                        "description": "Load plan: 22.5 + 22.5 + 22.5 + 22.5 + 9.19 yd",
                        "quantity": "99.19",
                        "unit": "cu yd",
                        "base_price": "5200.00",
                        "per_unit_price": "0",
                        "min_charge": "5200.00",
                    }
                ],
                frequency="one_time",
                tax_rate="0",
                zone_modifier_percent="0",
                uploaded_assets=[],
                intake_submission_id=None,
            )

            saved = create_quote(payload=payload, db=db)

            self.assertEqual(saved["items"][0]["unit"], "cu yd")
            self.assertEqual(saved["items"][0]["description"], "Load plan: 22.5 + 22.5 + 22.5 + 22.5 + 9.19 yd")

            reloaded = get_quote(quote_id=saved["id"], db=db)
            self.assertEqual(
                reloaded["items"][0]["description"],
                "Load plan: 22.5 + 22.5 + 22.5 + 22.5 + 9.19 yd",
            )

    def test_review_save_totals_are_non_zero_when_material_estimate_line_exists(self) -> None:
        with self.SessionLocal() as db:
            payload = QuoteCreate(
                job={
                    "customer_name": "Worksheet Demo",
                    "phone": "555-9090",
                    "email": "demo@example.com",
                    "address": "123 Bark St, Salem, OR 97301",
                    "zip_code": "97301",
                },
                items=[
                    {
                        "name": "Hemlock Material Estimate (21 yd)",
                        "description": "Load plan: 21 yd",
                        "quantity": "20.65",
                        "unit": "cu yd",
                        "base_price": "2250.00",
                        "per_unit_price": "0",
                        "min_charge": "2250.00",
                    }
                ],
                frequency="one_time",
                tax_rate="0",
                zone_modifier_percent="0",
                uploaded_assets=[],
                intake_submission_id=None,
            )

            saved = create_quote(payload=payload, db=db)
            self.assertEqual(len(saved["items"]), 1)
            self.assertGreater(Decimal(str(saved["items"][0]["line_total"])), Decimal("0"))
            self.assertGreater(Decimal(str(saved["subtotal"])), Decimal("0"))
            self.assertGreater(Decimal(str(saved["total"])), Decimal("0"))

            reloaded = get_quote(quote_id=saved["id"], db=db)
            self.assertEqual(len(reloaded["items"]), 1)
            self.assertGreater(Decimal(str(reloaded["items"][0]["line_total"])), Decimal("0"))
            self.assertGreater(Decimal(str(reloaded["subtotal"])), Decimal("0"))
            self.assertGreater(Decimal(str(reloaded["total"])), Decimal("0"))

    def test_upload_dimensions_returns_helpful_message_when_no_rows_detected(self) -> None:
        with self.SessionLocal() as db:
            with mock.patch.object(
                main_module,
                "estimate_job",
                return_value={
                    "measurement_entries": [],
                    "measurement_parse": {"classification": "scene_photo_estimation"},
                    "extraction_meta": {"trusted_measurements_available": False},
                },
            ):
                result = asyncio.run(
                    upload_dimension_assets(
                        draft_token="draft-dimensions-2",
                        files=[self._upload_file("yard-photo.jpg", self.VALID_PNG_BYTES, "image/jpeg")],
                        db=db,
                    )
                )

            self.assertEqual(result["measurements"], [])
            self.assertEqual(result["measurementsText"], "")
            self.assertIn("No measurements detected", result["message"])

            stored_asset = db.get(UploadedAsset, result["assets"][0]["id"])
            self.assertIsNotNone(stored_asset)
            self.assertEqual(stored_asset.upload_status, "ready")
            self.assertIsNone(stored_asset.error_message)

    def test_upload_dimensions_surfaces_openai_dns_failure_message(self) -> None:
        with self.SessionLocal() as db:
            with mock.patch.object(
                main_module,
                "estimate_job",
                return_value={
                    "measurement_entries": [],
                    "measurement_parse": {"classification": "failed_ocr_unreadable_note"},
                    "extraction_meta": {
                        "openai_configured": True,
                        "openai_used": False,
                        "openai_error": "openai_dns_failed",
                        "fallback_ocr_used": False,
                        "trusted_measurements_available": False,
                    },
                },
            ):
                result = asyncio.run(
                    upload_dimension_assets(
                        draft_token="draft-dimensions-dns",
                        files=[self._upload_file("yard-note.jpg", self.VALID_PNG_BYTES, "image/jpeg")],
                        db=db,
                    )
                )

            self.assertEqual(result["measurements"], [])
            self.assertIn("cannot resolve api.openai.com", result["message"])
            self.assertEqual(result["measurement_parse"]["classification"], "failed_ocr_unreadable_note")

    def test_parse_uploaded_measurement_asset_marks_ready_and_persists_source_metadata(self) -> None:
        with self.SessionLocal() as db:
            upload_result = asyncio.run(
                upload_measurement_note_assets(
                    draft_token="draft-parse-note",
                    parse_mode="force_measurement_note",
                    files=[self._upload_file("measurements.png", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )
            asset_id = upload_result["assets"][0]["id"]

            with mock.patch.object(
                main_module,
                "parse_handwritten_measurement_rows_from_uploaded_image",
                return_value=(
                    [
                        {"raw": "91x22", "length": 91, "width": 22},
                        {"raw": "25x30", "length": 25, "width": 30},
                    ],
                    None,
                ),
            ):
                result = parse_uploaded_measurement_asset(asset_id=asset_id, db=db)

            self.assertEqual(result["asset"]["status"], "ready")
            self.assertEqual(len(result["rows"]), 2)
            self.assertEqual(result["asset"]["parserResult"]["source_asset_id"], str(asset_id))
            self.assertEqual(result["asset"]["parserResult"]["source_filename"], "measurements.png")
            self.assertEqual(
                result["asset"]["parserResult"]["measurement_entries"][0]["source_asset_id"],
                str(asset_id),
            )
            self.assertEqual(
                result["asset"]["parserResult"]["measurement_entries"][0]["source_filename"],
                "measurements.png",
            )

            stored_asset = db.get(UploadedAsset, asset_id)
            self.assertIsNotNone(stored_asset)
            self.assertEqual(stored_asset.upload_status, "ready")
            stored_parser_result = json.loads(stored_asset.parse_result_json or "{}")
            self.assertEqual(stored_parser_result.get("source_asset_id"), str(asset_id))
            self.assertEqual(stored_parser_result.get("source_filename"), "measurements.png")

    def test_large_uploaded_asset_returns_explicit_error(self) -> None:
        oversized = b"x" * (main_module.MAX_UPLOAD_BYTES + 1)
        with self.SessionLocal() as db:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    upload_site_media_assets(
                        draft_token="draft-too-large",
                        parse_mode="auto",
                        files=[self._upload_file("huge-photo.jpg", oversized, "image/jpeg")],
                        db=db,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("File too large", str(ctx.exception.detail))

    def test_saved_quote_uses_uploaded_asset_metadata_only_and_preserves_parse_modes(self) -> None:
        with self.SessionLocal() as db:
            upload_result = asyncio.run(
                upload_site_media_assets(
                    draft_token="draft-save-1",
                    parse_mode="force_scene_photo",
                    files=[
                        self._upload_file("front-yard.jpg", self.VALID_PNG_BYTES, "image/jpeg"),
                        self._upload_file("scan.usdz", b"usdz-demo", "model/vnd.usdz+zip"),
                    ],
                    db=db,
                )
            )
            note_result = asyncio.run(
                upload_measurement_note_assets(
                    draft_token="draft-save-1",
                    parse_mode="force_measurement_note",
                    files=[self._upload_file("measurements.png", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )

            payload = QuoteCreate(
                job={
                    "customer_name": "Immediate Upload Test",
                    "phone": "555-1010",
                    "address": "123 Bark St, Salem, OR 97301",
                },
                items=[
                    QuoteItemInput(
                        name="Hemlock Delivery",
                        quantity=5,
                        unit="yd",
                        base_price=200,
                        per_unit_price=30,
                        min_charge=200,
                    )
                ],
                frequency="one_time",
                tax_rate=0,
                zone_modifier_percent=0,
                uploaded_assets=[
                    UploadedAssetRef(
                        id=upload_result["assets"][0]["id"],
                        category="site_media",
                        parseMode="force_scene_photo",
                        parserResult={"measurement_entries": [], "measurement_parse": {"classification": "scene_photo_estimation"}},
                        status="ready",
                    ),
                    UploadedAssetRef(
                        id=upload_result["assets"][1]["id"],
                        category="site_media",
                        parseMode="force_scene_photo",
                        parserResult={"measurement_entries": [], "measurement_parse": {"classification": "scene_photo_estimation"}},
                        status="ready",
                    ),
                    UploadedAssetRef(
                        id=note_result["assets"][0]["id"],
                        category="measurement_note",
                        parseMode="force_measurement_note",
                        parserResult={
                            "measurement_entries": [
                                {"entry_type": "dimension_pair", "length_ft": 14, "width_ft": 20, "estimated_area_sqft": 280}
                            ],
                            "measurement_parse": {"classification": "exact_measurement_note"},
                        },
                        status="ready",
                    ),
                ],
            )

            quote = create_quote(payload=payload, db=db)
            reloaded = get_quote(quote_id=quote["id"], db=db)

            self.assertEqual(len(reloaded["media"]), 3)
            self.assertEqual(
                [(item["filename"], item["category"], item["parseMode"], item["status"]) for item in reloaded["media"]],
                [
                    ("front-yard.jpg", "site_media", "force_scene_photo", "ready"),
                    ("scan.usdz", "site_media", "force_scene_photo", "ready"),
                    ("measurements.png", "measurement_note", "force_measurement_note", "ready"),
                ],
            )
            self.assertEqual(
                reloaded["media"][2]["parserResult"]["measurement_parse"]["classification"],
                "exact_measurement_note",
            )

            stored_assets = db.query(UploadedAsset).order_by(UploadedAsset.id.asc()).all()
            self.assertTrue(all(asset.quote_id == quote["id"] for asset in stored_assets))
            self.assertEqual(
                [asset.parse_mode for asset in stored_assets],
                ["force_scene_photo", "force_scene_photo", "force_measurement_note"],
            )
            self.assertEqual(
                [asset.upload_status for asset in stored_assets],
                ["ready", "ready", "ready"],
            )

            stored_media = db.query(QuoteMedia).filter(QuoteMedia.quote_id == quote["id"]).order_by(QuoteMedia.id.asc()).all()
            self.assertEqual(
                [(row.file_name, row.parse_mode, row.media_kind, row.upload_status) for row in stored_media],
                [
                    ("front-yard.jpg", "force_scene_photo", "photo", "ready"),
                    ("scan.usdz", "force_scene_photo", "lidar_scan", "ready"),
                    ("measurements.png", "force_measurement_note", "measurement_note", "ready"),
                ],
            )
            self.assertIn("exact_measurement_note", stored_media[2].parse_result_json or "")

    def test_ai_preview_uses_persisted_uploaded_asset_parse_mode_when_payload_omits_it(self) -> None:
        with self.SessionLocal() as db:
            upload_result = asyncio.run(
                upload_site_media_assets(
                    draft_token="draft-parse-mode",
                    parse_mode="force_measurement_note",
                    files=[self._upload_file("site-note.jpg", self.VALID_PNG_BYTES, "image/jpeg")],
                    db=db,
                )
            )

            calls = []

            def fake_estimate_job(**kwargs):
                calls.append(
                    {
                        "uploaded_images": list(kwargs.get("uploaded_images") or []),
                        "measurement_reference_images": list(kwargs.get("measurement_reference_images") or []),
                    }
                )
                return {
                    "estimated_labor_hours": Decimal("2.0"),
                    "material_cost": Decimal("25.0"),
                    "equipment_cost": Decimal("10.0"),
                    "suggested_price": Decimal("100.0"),
                    "recommended_crew_size": 2,
                    "estimated_duration_hours": Decimal("1.5"),
                    "crew_instructions": "Review note",
                    "primary_job_type": "mulch_refresh",
                    "detected_tasks": [],
                    "task_breakdown": [],
                    "measurement_entries": [],
                    "measurement_parse": {"classification": "failed_ocr_unreadable_note"},
                    "bed_groups": [],
                    "combined_bed_area_sqft": Decimal("0"),
                    "combined_bed_material_yards": Decimal("0"),
                    "recommended_material_yards": Decimal("0"),
                    "dimension_observations": {},
                    "missing_angle_estimate": {},
                    "detected_zones": [],
                    "zone_summary": "",
                    "extraction_meta": {"exact_measurement_parse_failed": True},
                }

            with mock.patch.object(main_module, "estimate_job", side_effect=fake_estimate_job):
                ai_preview_quote(
                    payload={
                        "uploaded_assets": [
                            {"id": upload_result["assets"][0]["id"], "category": "site_media"},
                        ],
                        "frequency": "one_time",
                        "tax_rate": "0",
                        "zone_modifier_percent": "0",
                        "material_type": "mulch",
                    },
                    db=db,
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["uploaded_images"], [])
            self.assertEqual(len(calls[0]["measurement_reference_images"]), 1)
            self.assertEqual(calls[0]["measurement_reference_images"][0]["file_name"], "site-note.jpg")

    def test_ai_preview_persists_parser_snapshot_back_to_uploaded_asset(self) -> None:
        with self.SessionLocal() as db:
            upload_result = asyncio.run(
                upload_measurement_note_assets(
                    draft_token="draft-parser-snapshot",
                    parse_mode="force_measurement_note",
                    files=[self._upload_file("measurements.png", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )

            response = ai_preview_quote(
                payload={
                    "uploaded_assets": [
                        {"id": upload_result["assets"][0]["id"], "category": "measurement_note", "parseMode": "force_measurement_note"},
                    ],
                    "frequency": "one_time",
                    "tax_rate": "0",
                    "zone_modifier_percent": "0",
                    "material_type": "mulch",
                    "measurement_entries": [
                        {"entry_type": "dimension_pair", "length_ft": 14, "width_ft": 20, "raw_text": "14x20"},
                    ],
                },
                db=db,
            )

            draft_assets = list_uploaded_assets(draft_token="draft-parser-snapshot", db=db)
            restored = draft_assets["assets"][0]

            self.assertEqual(restored["parseMode"], "force_measurement_note")
            self.assertEqual(restored["status"], "ready")
            self.assertIsInstance(restored["parserResult"], dict)
            self.assertEqual(
                restored["parserResult"]["measurement_parse"]["classification"],
                response["measurement_parse"]["classification"],
            )
            self.assertEqual(
                restored["parserResult"]["measurement_entries"],
                response["measurement_entries"],
            )

    def test_quote_save_and_reload_preserve_zip_plus_four(self) -> None:
        with self.SessionLocal() as db:
            quote = create_quote(
                payload=QuoteCreate(
                    job={
                        "customer_name": "ZIP Test",
                        "phone": "555-2020",
                        "address": "5525 Wigeon St SE",
                        "zip_code": "97306-1234",
                    },
                    items=[
                        QuoteItemInput(
                            name="Hemlock Delivery",
                            quantity=3,
                            unit="yd",
                            base_price=150,
                            per_unit_price=30,
                            min_charge=150,
                        )
                    ],
                    frequency="one_time",
                    tax_rate=0,
                    zone_modifier_percent=0,
                ),
                db=db,
            )

            self.assertEqual(quote["job"]["zip_code"], "97306-1234")

            reloaded = get_quote(quote_id=quote["id"], db=db)
            self.assertEqual(reloaded["job"]["zip_code"], "97306-1234")
            self.assertIn("ZIP Code: 97306-1234", reloaded["text_quote"])

    def test_quote_save_rejects_invalid_zip_code(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaises(HTTPException) as ctx:
                create_quote(
                    payload=QuoteCreate(
                        job={
                            "customer_name": "Bad ZIP Test",
                            "phone": "555-3030",
                            "address": "123 Bark St",
                            "zip_code": "97A06",
                        },
                        items=[
                            QuoteItemInput(
                                name="Hemlock Delivery",
                                quantity=3,
                                unit="yd",
                                base_price=150,
                                per_unit_price=30,
                                min_charge=150,
                            )
                        ],
                        frequency="one_time",
                        tax_rate=0,
                        zone_modifier_percent=0,
                    ),
                    db=db,
                )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("ZIP Code must be 12345 or 12345-6789", str(ctx.exception.detail))

    def test_crm_quote_search_matches_saved_zip_code(self) -> None:
        with self.SessionLocal() as db:
            create_quote(
                payload=QuoteCreate(
                    job={
                        "customer_name": "Search ZIP Test",
                        "phone": "555-4040",
                        "address": "500 Pine St",
                        "zip_code": "97306-1234",
                    },
                    items=[
                        QuoteItemInput(
                            name="Hemlock Delivery",
                            quantity=2,
                            unit="yd",
                            base_price=120,
                            per_unit_price=25,
                            min_charge=120,
                        )
                    ],
                    frequency="one_time",
                    tax_rate=0,
                    zone_modifier_percent=0,
                ),
                db=db,
            )

            rows = list_quotes_crm(limit=20, status_filter=None, search="97306-1234", db=db)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["zip_code"], "97306-1234")

    def test_handwritten_note_dimension_list_extracts_full_area(self) -> None:
        note_text = "\n".join(
            [
                "9x22",
                "25x30",
                "30x14",
                "60x14",
                "35x18",
                "20x20",
                "6x33",
                "14x20",
                "51x13",
                "50x45",
                "15x230",
                "55x35",
                "100x7",
                "110x12",
                "16x15",
            ]
        )

        entries = _extract_measurement_entries(note_text, [])
        dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
        total_area = sum(float(entry["estimated_area_sqft"]) for entry in dimension_entries)

        self.assertEqual(len(dimension_entries), 15)
        self.assertAlmostEqual(total_area, 14264.0, places=2)

    def test_line_based_measurement_parser_accepts_space_separated_pairs(self) -> None:
        note_text = "\n".join(
            [
                "14 20",
                "25 30",
                "4 yds",
            ]
        )

        entries = _extract_measurement_entries(note_text, ["note.jpg"])
        dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
        yard_entries = [entry for entry in entries if entry.get("entry_type") == "material_yards"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertEqual(keys, {(14.0, 20.0), (25.0, 30.0)})
        self.assertEqual(len(yard_entries), 1)
        self.assertEqual(float(yard_entries[0]["yards"]), 4.0)

    def test_measurement_parser_recovers_dense_multi_pair_ocr_line(self) -> None:
        note_text = "91 22 25 30 30 14 60 14"

        entries = _extract_measurement_entries(note_text, ["note.jpg"])
        dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertEqual(keys, {(91.0, 22.0), (25.0, 30.0), (30.0, 14.0), (60.0, 14.0)})

    def test_measurement_parser_recovers_vertical_single_value_ocr_stack(self) -> None:
        note_text = "\n".join(["91", "22", "25", "30", "30", "14", "60", "14"])

        entries = _extract_measurement_entries(note_text, ["note.jpg"])
        dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertEqual(keys, {(91.0, 22.0), (25.0, 30.0), (30.0, 14.0), (60.0, 14.0)})

    def test_analyze_uploaded_images_uses_recovered_dense_ocr_rows_without_flag_when_clear(self) -> None:
        fake_image = {"file_name": "measurement-sheet.jpg", "raw": b"demo"}
        fake_path = Path("/tmp/measurement-sheet.jpg")

        fake_settings = SimpleNamespace(
            allow_fallback_handwritten_measurement_ocr=False,
            openai_api_key="test-key",
        )

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_decode_image_to_path",
            return_value=fake_path,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_ocr_candidate_paths",
            return_value=[fake_path],
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_vision_measurement_entries",
            return_value=([], "openai_dns_failed"),
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_vision_ocr_text",
            return_value={str(fake_path): "91 22 25 30 30 14 60 14"},
        ):
            result = analyze_uploaded_images([fake_image], notes=None)

        dimension_entries = [entry for entry in result["measurement_entries"] if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertEqual(keys, {(91.0, 22.0), (25.0, 30.0), (30.0, 14.0), (60.0, 14.0)})
        self.assertTrue(result["extraction_meta"]["trusted_measurements_available"])

    def test_analyze_uploaded_images_uses_recovered_vertical_ocr_rows_without_flag_when_clear(self) -> None:
        fake_image = {"file_name": "measurement-sheet.jpg", "raw": b"demo"}
        fake_path = Path("/tmp/measurement-sheet.jpg")

        fake_settings = SimpleNamespace(
            allow_fallback_handwritten_measurement_ocr=False,
            openai_api_key="test-key",
        )

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_decode_image_to_path",
            return_value=fake_path,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_ocr_candidate_paths",
            return_value=[fake_path],
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_vision_measurement_entries",
            return_value=([], "openai_dns_failed"),
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_vision_ocr_text",
            return_value={str(fake_path): "91\n22\n25\n30\n30\n14\n60\n14"},
        ):
            result = analyze_uploaded_images([fake_image], notes=None)

        dimension_entries = [entry for entry in result["measurement_entries"] if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertEqual(keys, {(91.0, 22.0), (25.0, 30.0), (30.0, 14.0), (60.0, 14.0)})
        self.assertTrue(result["extraction_meta"]["trusted_measurements_available"])

    def test_analyze_uploaded_images_uses_openai_text_recovery_from_ocr_text(self) -> None:
        fake_image = {"file_name": "measurement-sheet.jpg", "raw": b"demo"}
        fake_path = Path("/tmp/measurement-sheet.jpg")

        fake_settings = SimpleNamespace(
            allow_fallback_handwritten_measurement_ocr=False,
            openai_api_key="test-key",
        )

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_decode_image_to_path",
            return_value=fake_path,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_ocr_candidate_paths",
            return_value=[fake_path],
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_vision_measurement_entries",
            return_value=([], "openai_dns_failed"),
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_vision_ocr_text",
            return_value={str(fake_path): "91\n22\n25\n30\n30\n14\n60\n14"},
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_text_measurement_entries",
            return_value=(
                [
                    {"entry_type": "dimension_pair", "raw_text": "91x22", "length_ft": 91, "width_ft": 22, "confidence": 0.98, "source_images": ["measurement-sheet.jpg", "openai_vision"]},
                    {"entry_type": "dimension_pair", "raw_text": "25x30", "length_ft": 25, "width_ft": 30, "confidence": 0.98, "source_images": ["measurement-sheet.jpg", "openai_vision"]},
                ],
                None,
            ),
        ):
            result = analyze_uploaded_images([fake_image], notes=None)

        dimension_entries = [entry for entry in result["measurement_entries"] if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertIn((91.0, 22.0), keys)
        self.assertIn((25.0, 30.0), keys)
        self.assertTrue(result["extraction_meta"]["openai_used"])
        self.assertTrue(result["extraction_meta"]["trusted_measurements_available"])

    def test_analyze_uploaded_images_marks_exact_measurement_parse_failed_when_reference_images_have_no_rows(self) -> None:
        fake_image = {"file_name": "measurement-sheet.jpg", "raw": b"demo"}
        fake_path = Path("/tmp/measurement-sheet.jpg")

        fake_settings = SimpleNamespace(
            allow_fallback_handwritten_measurement_ocr=False,
            openai_api_key="test-key",
            openai_vision_model="gpt-4.1",
        )

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_decode_image_to_path",
            return_value=fake_path,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_ocr_candidate_paths",
            return_value=[fake_path],
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_vision_measurement_entries",
            return_value=([], "openai_missing_measurement_lines"),
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_vision_ocr_text",
            return_value={str(fake_path): "totals 12 only no usable pairs"},
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_text_measurement_entries",
            return_value=([], "openai_missing_measurement_lines"),
        ):
            result = analyze_uploaded_images([], notes=None, measurement_reference_images=[fake_image])

        self.assertFalse(result["measurement_entries"])
        self.assertTrue(result["extraction_meta"]["measurement_reference_images_present"])
        self.assertTrue(result["extraction_meta"]["exact_measurement_parse_failed"])
        self.assertTrue(result["extraction_meta"]["ocr_debug"])
        self.assertEqual(result["extraction_meta"]["ocr_debug"][0]["source_name"], "measurement-sheet.jpg")
        self.assertIn("totals 12 only", result["extraction_meta"]["ocr_debug"][0]["preview_text"])
        self.assertEqual(result["measurement_parse"]["classification"], "failed_ocr_unreadable_note")
        self.assertFalse(result["measurement_parse"]["should_use_geometry_fallback"])
        self.assertTrue(result["measurement_parse"]["unclear_rows"])

    def test_analyze_uploaded_images_auto_promotes_note_like_site_media_into_measurement_reference_mode(self) -> None:
        fake_image = {"file_name": "measurement-note.jpg", "raw": b"demo"}
        fake_path = Path("/tmp/measurement-note.jpg")

        fake_settings = SimpleNamespace(
            allow_fallback_handwritten_measurement_ocr=False,
            openai_api_key="test-key",
            openai_vision_model="gpt-4.1",
        )

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_decode_image_to_path",
            return_value=fake_path,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_ocr_candidate_paths",
            return_value=[fake_path],
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_vision_measurement_entries",
            return_value=([], "openai_missing_measurement_lines"),
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_vision_ocr_text",
            return_value={str(fake_path): "91\n22\n25\n30"},
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_text_measurement_entries",
            return_value=(
                [
                    {"entry_type": "dimension_pair", "raw_text": "91x22", "length_ft": 91, "width_ft": 22, "confidence": 0.98, "source_images": ["measurement-note.jpg", "openai_vision"]},
                    {"entry_type": "dimension_pair", "raw_text": "25x30", "length_ft": 25, "width_ft": 30, "confidence": 0.98, "source_images": ["measurement-note.jpg", "openai_vision"]},
                ],
                None,
            ),
        ):
            result = analyze_uploaded_images([fake_image], notes=None)

        dimension_entries = [entry for entry in result["measurement_entries"] if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertEqual(keys, {(91.0, 22.0), (25.0, 30.0)})
        self.assertTrue(result["extraction_meta"]["measurement_reference_images_present"])
        self.assertTrue(result["extraction_meta"]["trusted_measurements_available"])
        self.assertTrue(result["extraction_meta"]["ocr_debug"])
        self.assertEqual(result["extraction_meta"]["ocr_debug"][0]["dimension_row_candidates"], 2)
        self.assertEqual(result["measurement_parse"]["classification"], "exact_measurement_note")
        self.assertFalse(result["measurement_parse"]["should_use_geometry_fallback"])

    def test_analyze_uploaded_images_marks_scene_photo_estimation_when_text_is_not_note_like(self) -> None:
        fake_image = {"file_name": "yard-photo.jpg", "raw": b"demo"}
        fake_path = Path("/tmp/yard-photo.jpg")

        fake_settings = SimpleNamespace(
            allow_fallback_handwritten_measurement_ocr=False,
            openai_api_key="test-key",
        )

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_decode_image_to_path",
            return_value=fake_path,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_ocr_candidate_paths",
            return_value=[fake_path],
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_vision_measurement_entries",
            return_value=([], "openai_dns_failed"),
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_vision_ocr_text",
            return_value={str(fake_path): "mulch trees lawn front bed access gate"},
        ):
            result = analyze_uploaded_images([fake_image], notes=None)

        self.assertEqual(result["measurement_parse"]["classification"], "scene_photo_estimation")
        self.assertTrue(result["measurement_parse"]["should_use_geometry_fallback"])

    def test_analyze_uploaded_images_uses_trusted_fallback_ocr_when_enabled(self) -> None:
        fake_image = {"file_name": "handwritten-note.jpg", "raw": b"demo"}
        fake_path = Path("/tmp/handwritten-note.jpg")

        fake_settings = SimpleNamespace(
            allow_fallback_handwritten_measurement_ocr=True,
            openai_api_key="test-key",
        )

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_decode_image_to_path",
            return_value=fake_path,
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_ocr_candidate_paths",
            return_value=[fake_path],
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_vision_measurement_entries",
            return_value=([], "openai_dns_failed"),
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_vision_ocr_text",
            return_value={str(fake_path): "14 20\n25 30"},
        ):
            result = analyze_uploaded_images([fake_image], notes=None)

        dimension_entries = [entry for entry in result["measurement_entries"] if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertEqual(keys, {(14.0, 20.0), (25.0, 30.0)})
        self.assertTrue(result["extraction_meta"]["fallback_ocr_used"])
        self.assertTrue(result["extraction_meta"]["trusted_measurements_available"])
        self.assertEqual(result["extraction_meta"]["openai_error"], "openai_dns_failed")

    def test_normalize_openai_measurement_rows_accepts_rows_schema(self) -> None:
        entries, error = _normalize_openai_measurement_rows(
            {
                "rows": [
                    {"length": 91, "width": 22},
                    {"length": 25, "width": 30, "notes": "messy but readable"},
                ]
            },
            "measurement-note.jpg",
        )

        self.assertIsNone(error)
        self.assertEqual(len(entries), 2)
        self.assertEqual((entries[0]["length_ft"], entries[0]["width_ft"]), (91.0, 22.0))
        self.assertEqual((entries[1]["length_ft"], entries[1]["width_ft"]), (25.0, 30.0))
        self.assertEqual(entries[1]["notes"], "messy but readable")

    def test_openai_vision_measurement_entries_retries_when_first_pass_is_empty(self) -> None:
        fake_path = Path("/tmp/measurement-sheet.jpg")

        with mock.patch.object(
            ai_photo_analysis_module,
            "_image_to_data_url",
            return_value="data:image/jpeg;base64,abc",
        ), mock.patch.object(
            ai_photo_analysis_module,
            "_openai_response_measurement_entries",
            side_effect=[
                ([], "openai_missing_measurement_lines", {"code": "openai_missing_measurement_lines"}),
                (
                    [
                        {
                            "entry_type": "dimension_pair",
                            "raw_text": "91x22",
                            "length_ft": 91,
                            "width_ft": 22,
                            "confidence": 0.98,
                            "source_images": ["measurement-sheet.jpg", "openai_vision"],
                        }
                    ],
                    None,
                    None,
                ),
            ],
        ) as mocked_response:
            entries, error, error_info = ai_photo_analysis_module._openai_vision_measurement_entries(
                [fake_path],
                "measurement-sheet.jpg",
            )

        self.assertIsNone(error)
        self.assertIsNone(error_info)
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["length_ft"], entries[0]["width_ft"]), (91.0, 22.0))
        self.assertEqual(mocked_response.call_count, 2)

    def test_openai_response_measurement_entries_uses_structured_output_and_rows_schema(self) -> None:
        fake_settings = SimpleNamespace(
            openai_api_key="test-key",
            openai_vision_model="gpt-4.1",
            openai_ca_bundle="",
            openai_allow_insecure_ssl=False,
        )
        captured: dict[str, object] = {}
        def fake_sdk_call(*, payload, parser_branch, log_reference):
            captured["payload"] = payload
            captured["parser_branch"] = parser_branch
            captured["log_reference"] = log_reference
            response = {"output_text": json.dumps({"rows": [{"raw": "9x22", "length": 9, "width": 22}]})}
            return response, None, json.dumps(response)

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch(
            "app.ai_photo_analysis._openai_responses_payload_via_sdk",
            side_effect=fake_sdk_call,
        ):
            entries, error, error_info = ai_photo_analysis_module._openai_response_measurement_entries(
                [{"type": "input_text", "text": "Extract rows"}],
                "measurement-note.jpg",
            )

        self.assertIsNone(error)
        self.assertIsNone(error_info)
        self.assertEqual(captured["parser_branch"], "worksheet_measurement")
        self.assertEqual(captured["log_reference"], "measurement-note.jpg")
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["length_ft"], entries[0]["width_ft"]), (9.0, 22.0))
        body = captured["payload"]
        self.assertEqual(body["model"], "gpt-4.1")
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["text"]["format"]["name"], "handwritten_measurement_rows")

    def test_parse_handwritten_measurement_rows_from_image_url_uses_structured_output(self) -> None:
        starbucks_rows = [
            {"raw": "91x22", "length": 91, "width": 22},
            {"raw": "25x30", "length": 25, "width": 30},
            {"raw": "30x14", "length": 30, "width": 14},
            {"raw": "60x14", "length": 60, "width": 14},
            {"raw": "35x18", "length": 35, "width": 18},
            {"raw": "6x33", "length": 6, "width": 33},
            {"raw": "14x20", "length": 14, "width": 20},
            {"raw": "51x13", "length": 51, "width": 13},
            {"raw": "50x45", "length": 50, "width": 45},
            {"raw": "15x230", "length": 15, "width": 230},
            {"raw": "55x35", "length": 55, "width": 35},
            {"raw": "20x20", "length": 20, "width": 20},
            {"raw": "100x7", "length": 100, "width": 7},
            {"raw": "110x12", "length": 110, "width": 12},
            {"raw": "16x15", "length": 16, "width": 15},
        ]
        fake_settings = SimpleNamespace(
            openai_api_key="test-key",
            openai_vision_model="gpt-4.1",
            openai_ca_bundle="",
            openai_allow_insecure_ssl=False,
        )
        captured: dict[str, object] = {}
        def fake_sdk_call(*, payload, parser_branch, log_reference):
            captured["payload"] = payload
            captured["parser_branch"] = parser_branch
            captured["log_reference"] = log_reference
            response = {"output_text": json.dumps({"rows": starbucks_rows})}
            return response, None, json.dumps(response)

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch(
            "app.ai_photo_analysis._openai_responses_payload_via_sdk",
            side_effect=fake_sdk_call,
        ):
            rows, error = ai_photo_analysis_module.parse_handwritten_measurement_rows_from_image_url(
                "https://example.com/StarbucksBB.jpg?signature=test"
            )

        self.assertIsNone(error)
        self.assertEqual(captured["parser_branch"], "handwritten_measurement")
        self.assertEqual(captured["log_reference"], "https://example.com/StarbucksBB.jpg?signature=test")
        self.assertEqual(len(rows), 15)
        self.assertEqual(rows[0], {"raw": "91x22", "length": 91, "width": 22})
        self.assertEqual(
            {row["raw"] for row in rows},
            {
                "91x22",
                "25x30",
                "30x14",
                "60x14",
                "35x18",
                "6x33",
                "14x20",
                "51x13",
                "50x45",
                "15x230",
                "55x35",
                "20x20",
                "100x7",
                "110x12",
                "16x15",
            },
        )

        body = captured["payload"]
        self.assertEqual(body["model"], "gpt-4.1")
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["text"]["format"]["name"], "handwritten_measurement_rows")
        self.assertEqual(body["input"][0]["content"][1]["type"], "input_image")
        self.assertEqual(
            body["input"][0]["content"][1]["image_url"],
            "https://example.com/StarbucksBB.jpg?signature=test",
        )

    def test_parse_handwritten_measurement_rows_from_image_url_retries_when_first_pass_is_empty(self) -> None:
        fake_settings = SimpleNamespace(
            openai_api_key="test-key",
            openai_vision_model="gpt-4.1",
            openai_ca_bundle="",
            openai_allow_insecure_ssl=False,
        )
        request_bodies: list[dict] = []

        responses = iter([
            ({"output_text": json.dumps({"rows": []})}, None, json.dumps({"output_text": json.dumps({"rows": []})})),
            (
                {"output_text": json.dumps({"rows": [{"raw": "91x22", "length": 91, "width": 22}]})},
                None,
                json.dumps({"output_text": json.dumps({"rows": [{"raw": "91x22", "length": 91, "width": 22}]})}),
            ),
        ])

        def fake_sdk_call(*, payload, parser_branch, log_reference):
            request_bodies.append(payload)
            return next(responses)

        with mock.patch.object(
            ai_photo_analysis_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch(
            "app.ai_photo_analysis._openai_responses_payload_via_sdk",
            side_effect=fake_sdk_call,
        ):
            rows, error = ai_photo_analysis_module.parse_handwritten_measurement_rows_from_image_url(
                "https://example.com/StarbucksBB.jpg?signature=test"
            )

        self.assertIsNone(error)
        self.assertEqual(rows, [{"raw": "91x22", "length": 91, "width": 22}])
        self.assertEqual(len(request_bodies), 2)
        self.assertIn("Be tolerant of messy handwriting.", request_bodies[1]["input"][0]["content"][0]["text"])

    def test_openai_network_classifier_treats_linux_dns_error_as_dns_failure(self) -> None:
        reason = ai_photo_analysis_module._classify_openai_network_error("[Errno -3] Temporary failure in name resolution")

        self.assertEqual(reason, "openai_dns_failed")

    def test_openai_health_payload_treats_linux_dns_error_as_dns_failure(self) -> None:
        fake_settings = SimpleNamespace(
            openai_api_key="test-key",
            openai_vision_model="gpt-4.1",
            openai_ca_bundle="",
            openai_allow_insecure_ssl=False,
        )
        failing_request = urllib.error.URLError(OSError("[Errno -3] Temporary failure in name resolution"))

        with mock.patch.object(
            main_module,
            "refresh_settings",
            return_value=fake_settings,
        ), mock.patch.object(
            main_module,
            "runtime_openai_api_key",
            return_value="test-key",
        ), mock.patch.object(
            main_module.urllib.request,
            "urlopen",
            side_effect=failing_request,
        ):
            payload = main_module._openai_health_payload()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["detail"], "DNS failure")
        self.assertEqual(payload["reason_code"], "openai_dns_failed")

    def test_quick_entry_mixed_lines_accepts_valid_rows_and_skips_invalid_ones(self) -> None:
        def parse_quick_entry(text: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            rows: list[dict[str, object]] = []
            invalid_lines: list[dict[str, object]] = []
            for line_number, raw_line in enumerate(io.StringIO(text).read().splitlines(), start=1):
                line = raw_line.strip()
                if not line:
                    continue
                match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", line, flags=re.IGNORECASE)
                if not match:
                    invalid_lines.append({"lineNumber": line_number, "text": line})
                    continue
                rows.append(
                    {
                        "entry_type": "dimension_pair",
                        "include": True,
                        "raw_text": line,
                        "length_ft": float(match.group(1)),
                        "width_ft": float(match.group(2)),
                        "confidence": 1,
                        "source_images": ["Quick entry"],
                    }
                )
            return rows, invalid_lines

        rows, invalid_lines = parse_quick_entry("22x40\n225x10\n18 x 12\nbad line\n")

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [(row["length_ft"], row["width_ft"]) for row in rows],
            [(22.0, 40.0), (225.0, 10.0), (18.0, 12.0)],
        )
        self.assertEqual(invalid_lines, [{"lineNumber": 4, "text": "bad line"}])

    def test_parse_handwritten_test_returns_rows(self) -> None:
        rows = [{"raw": "91x22", "length": 91, "width": 22}]

        with mock.patch.object(
            main_module,
            "parse_handwritten_measurement_rows_from_image_url",
            return_value=(rows, None),
        ):
            response = parse_handwritten_test({"imageUrl": "https://example.com/StarbucksBB.jpg"})

        self.assertEqual(response, {"rows": rows})

    def test_parse_handwritten_test_rejects_non_fetchable_url(self) -> None:
        with self.assertRaises(HTTPException) as exc:
            parse_handwritten_test({"imageUrl": "file:///tmp/StarbucksBB.jpg"})

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("signed or publicly fetchable", str(exc.exception.detail))

    def test_parse_handwritten_test_surfaces_openai_failure_reason(self) -> None:
        with mock.patch.object(
            main_module,
            "parse_handwritten_measurement_rows_from_image_url",
            return_value=([], "openai_dns_failed"),
        ):
            with self.assertRaises(HTTPException) as exc:
                parse_handwritten_test({"imageUrl": "https://example.com/StarbucksBB.jpg"})

        self.assertEqual(exc.exception.status_code, 502)
        self.assertEqual(exc.exception.detail["reason_code"], "openai_dns_failed")
        self.assertIn("cannot resolve api.openai.com", exc.exception.detail["message"])

    def test_use_uploaded_asset_as_measurement_note_reclassifies_site_media_asset(self) -> None:
        with self.SessionLocal() as db:
            upload_result = asyncio.run(
                upload_site_media_assets(
                    draft_token="draft-reclassify",
                    parse_mode="auto",
                    files=[self._upload_file("StarbucksBB.jpg", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )

            asset_id = upload_result["assets"][0]["id"]
            response = use_uploaded_asset_as_measurement_note(asset_id=asset_id, db=db)

            self.assertEqual(response["asset"]["category"], "measurement_note")
            self.assertEqual(response["asset"]["parseMode"], "force_measurement_note")
            self.assertEqual(response["asset"]["status"], "uploaded")

            stored = db.query(UploadedAsset).filter(UploadedAsset.id == asset_id).first()
            self.assertIsNotNone(stored)
            self.assertEqual(stored.category, "measurement_note")
            self.assertEqual(stored.media_kind, "measurement_note")
            self.assertEqual(stored.parse_mode, "force_measurement_note")

    def test_parse_measurement_note_returns_rows_for_uploaded_note_asset(self) -> None:
        rows = [{"raw": "91x22", "length": 91, "width": 22}]
        with self.SessionLocal() as db:
            upload_result = asyncio.run(
                upload_measurement_note_assets(
                    draft_token="draft-note-parse",
                    parse_mode="force_measurement_note",
                    files=[self._upload_file("StarbucksBB.jpg", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )
            asset_id = upload_result["assets"][0]["id"]

            with mock.patch.object(
                main_module,
                "parse_handwritten_measurement_rows_from_uploaded_image",
                return_value=(rows, None),
            ):
                response = parse_measurement_note({"assetId": str(asset_id)}, db=db)

            self.assertEqual(response, {"rows": rows})

    def test_measurement_note_panel_upload_returns_uploaded_note_asset(self) -> None:
        with self.SessionLocal() as db:
            response = asyncio.run(
                upload_measurement_note_panel_assets(
                    draft_token=None,
                    parse_mode="force_measurement_note",
                    files=[self._upload_file("StarbucksBB.jpg", self.VALID_PNG_BYTES, "image/png")],
                    db=db,
                )
            )

        self.assertEqual(len(response["assets"]), 1)
        self.assertEqual(response["assets"][0]["category"], "measurement_note")
        self.assertEqual(response["assets"][0]["parseMode"], "force_measurement_note")

    def test_select_best_ocr_text_prefers_more_complete_measurement_read(self) -> None:
        partial_text = "\n".join(
            [
                "25x30",
                "30x14",
                "60x14",
                "35x18",
                "15x230",
            ]
        )
        full_text = "\n".join(
            [
                "9x22",
                "25x30",
                "30x14",
                "60x14",
                "35x18",
                "20x20",
                "6x33",
                "14x20",
                "51x13",
                "50x45",
                "15x230",
                "55x35",
                "100x7",
                "110x12",
                "16x15",
            ]
        )

        self.assertEqual(_select_best_ocr_text([partial_text, full_text]), full_text)

    def test_merge_ocr_measurement_entries_keeps_large_rows_from_secondary_candidate(self) -> None:
        primary_text = "\n".join(
            [
                "200x20",
                "150x20",
                "100x20",
                "90x20",
                "80x20",
                "70x20",
                "60x20",
            ]
        )
        secondary_text = "\n".join(
            [
                "200x20",
                "150x20",
                "100x20",
                "90x20",
                "14x20",
                "110x12",
            ]
        )

        entries = _merge_ocr_measurement_entries([primary_text, secondary_text], ["note.jpg"])
        dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}
        areas = sum(float(entry["estimated_area_sqft"]) for entry in dimension_entries)
        recovered_rows = {
            (float(entry["length_ft"]), float(entry["width_ft"]))
            for entry in dimension_entries
            if entry.get("recovered_from_alternate") is True
        }

        self.assertIn((14.0, 20.0), keys)
        self.assertIn((110.0, 12.0), keys)
        self.assertIn((14.0, 20.0), recovered_rows)
        self.assertIn((110.0, 12.0), recovered_rows)
        self.assertAlmostEqual(areas, 16600.0, places=2)

    def test_merge_ocr_measurement_entries_prefers_best_candidate_for_near_duplicate_rows(self) -> None:
        best_text = "\n".join(
            [
                "55x35",
                "110x12",
                "30x14",
            ]
        )
        noisy_text = "\n".join(
            [
                "55x36",
                "110x12",
            ]
        )

        entries = _merge_ocr_measurement_entries([best_text, noisy_text], ["note.jpg"])
        dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
        keys = {(float(entry["length_ft"]), float(entry["width_ft"])) for entry in dimension_entries}

        self.assertIn((55.0, 35.0), keys)
        self.assertNotIn((55.0, 36.0), keys)
        self.assertIn((110.0, 12.0), keys)
        self.assertIn((30.0, 14.0), keys)

    def test_merge_ocr_measurement_entries_preserves_best_candidate_area(self) -> None:
        best_text = "\n".join(
            [
                "91x22",
                "25x30",
                "30x14",
                "60x14",
                "35x18",
                "4x65",
                "6x33",
                "14x20",
                "51x13",
                "50x45",
                "15x230",
                "55x35",
                "20x20",
                "100x7",
                "110x12",
                "16x15",
            ]
        )
        noisy_text = "\n".join(
            [
                "91x22",
                "25x30",
                "30x14",
                "60x14",
                "35x18",
                "6x33",
                "14x20",
                "51x13",
                "50x45",
                "15x230",
                "55x36",
                "20x20",
                "100x7",
                "16x15",
            ]
        )

        entries = _merge_ocr_measurement_entries([best_text, noisy_text], ["note.jpg"])
        dimension_entries = [entry for entry in entries if entry.get("entry_type") == "dimension_pair"]
        total_area = sum(float(entry["estimated_area_sqft"]) for entry in dimension_entries)

        self.assertAlmostEqual(total_area, 16328.0, places=2)


if __name__ == "__main__":
    unittest.main()
