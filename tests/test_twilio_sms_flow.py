import base64
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base
import app.main as main_module
from modules.communication_crm.models import CrmContact, CrmConversation, CrmMessage


class TwilioSmsFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "twilio-sms.db"
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

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main_module.app.dependency_overrides[main_module.get_db] = override_get_db
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        main_module.app.dependency_overrides.pop(main_module.get_db, None)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_twilio_inbound_creates_single_contact_thread_and_summary(self) -> None:
        response = self.client.post(
            "/api/twilio/sms/inbound",
            data={
                "From": "(503) 555-0123",
                "To": "+15035550000",
                "Body": "Hi, I need help with a quote for mulch.",
                "MessageSid": "SM-inbound-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("<Response>", response.text)

        duplicate = self.client.post(
            "/api/twilio/sms/inbound",
            data={
                "From": "+15035550123",
                "To": "+15035550000",
                "Body": "Following up on the same quote.",
                "MessageSid": "SM-inbound-2",
            },
        )
        self.assertEqual(duplicate.status_code, 200)

        with self.SessionLocal() as db:
            self.assertEqual(db.query(CrmContact).count(), 1)
            self.assertEqual(db.query(CrmConversation).count(), 1)
            self.assertEqual(db.query(CrmMessage).filter(CrmMessage.direction == "inbound").count(), 2)
            contact = db.query(CrmContact).one()
            self.assertEqual(contact.mobile_phone, "+15035550123")

        inbox = self.client.get("/crm/api/conversations")
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(len(inbox.json()), 1)
        self.assertEqual(inbox.json()[0]["front_desk_summary"]["intent"], "quote request")

    def test_twilio_outbound_missing_credentials_stores_demo_message(self) -> None:
        inbound = self.client.post(
            "/api/twilio/sms/inbound",
            data={
                "From": "+15035550124",
                "To": "+15035550000",
                "Body": "Can you text me a quote?",
                "MessageSid": "SM-inbound-3",
            },
        )
        self.assertEqual(inbound.status_code, 200)
        conversation_id = self.client.get("/crm/api/conversations").json()[0]["id"]

        with mock.patch.dict(
            "os.environ",
            {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": "", "TWILIO_PHONE_NUMBER": ""},
            clear=False,
        ):
            sent = self.client.post(
                "/api/twilio/sms/send",
                json={"conversation_id": conversation_id, "body": "Yes, I can help."},
            )

        self.assertEqual(sent.status_code, 200)
        payload = sent.json()
        self.assertEqual(payload["mode"], "demo")
        self.assertEqual(payload["message"]["delivery_status"], "demo")

        detail = self.client.get(f"/crm/api/conversations/{conversation_id}")
        self.assertEqual([item["direction"] for item in detail.json()["messages"]], ["inbound", "outbound"])

    def test_twilio_signature_validation_when_token_configured(self) -> None:
        token = "test-token"
        url = "http://testserver/api/twilio/sms/inbound"
        form = {
            "From": "+15035550125",
            "To": "+15035550000",
            "Body": "Need a quote.",
            "MessageSid": "SM-signed-1",
        }
        signed = url + "".join(f"{key}{form[key]}" for key in sorted(form))
        signature = base64.b64encode(hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()).decode()

        with mock.patch.dict("os.environ", {"TWILIO_AUTH_TOKEN": token, "TWILIO_VALIDATE_SIGNATURES": "true"}, clear=False):
            bad = self.client.post("/api/twilio/sms/inbound", data=form, headers={"X-Twilio-Signature": "bad"})
            good = self.client.post("/api/twilio/sms/inbound", data=form, headers={"X-Twilio-Signature": signature})

        self.assertEqual(bad.status_code, 403)
        self.assertEqual(good.status_code, 200)


if __name__ == "__main__":
    unittest.main()
