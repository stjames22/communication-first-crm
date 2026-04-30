import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base
import app.main as main_module
from modules.communication_crm.models import CrmContact, CrmConversation, CrmMessage


class FrontDeskFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "front-desk.db"
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

    def test_low_risk_inbound_creates_contact_thread_summary_and_auto_reply(self) -> None:
        response = self.client.post(
            "/api/inbound/message",
            json={
                "channel": "sms",
                "from": "+15551234567",
                "message": "Can I get a quote for mulch delivery this week?",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["auto_replied"])
        self.assertEqual(payload["summary"]["intent"], "quote request")
        self.assertEqual(payload["summary"]["service"], "mulch delivery")
        self.assertEqual(payload["summary"]["next_action"], "collect address")

        detail = self.client.get(f"/crm/api/conversations/{payload['conversation_id']}")
        self.assertEqual(detail.status_code, 200)
        thread = detail.json()
        self.assertEqual(thread["conversation"]["front_desk_summary"]["intent"], "quote request")
        self.assertEqual([message["direction"] for message in thread["messages"]], ["inbound", "outbound"])
        self.assertEqual(thread["messages"][1]["delivery_status"], "auto_replied")

        with self.SessionLocal() as db:
            self.assertEqual(db.query(CrmContact).count(), 1)
            self.assertEqual(db.query(CrmConversation).count(), 1)
            self.assertEqual(db.query(CrmMessage).count(), 2)

    def test_review_risk_inbound_does_not_auto_reply(self) -> None:
        response = self.client.post(
            "/api/inbound/message",
            json={
                "channel": "sms",
                "from": "+15557654321",
                "message": "I want to cancel my policy and talk about a claim.",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["auto_replied"])
        self.assertEqual(payload["risk"]["risk"], "review")

        detail = self.client.get(f"/crm/api/conversations/{payload['conversation_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual([message["direction"] for message in detail.json()["messages"]], ["inbound"])


if __name__ == "__main__":
    unittest.main()
