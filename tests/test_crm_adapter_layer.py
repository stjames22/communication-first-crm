import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base
import app.main as main_module
from modules.communication_crm import crm_adapters
from modules.communication_crm.models import CrmContact, CrmConversation, CrmMessage


class CrmAdapterLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "adapter.db"
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

    def test_inbound_message_uses_local_adapter(self) -> None:
        with mock.patch.dict(os.environ, {"COMMUNICATION_CRM_ADAPTER": "local"}, clear=False):
            response = self.client.post(
                "/api/inbound/message",
                json={"channel": "sms", "from": "+15035551000", "message": "Need a service quote."},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        with self.SessionLocal() as db:
            adapter = crm_adapters.get_crm_adapter(db)
            self.assertIsInstance(adapter, crm_adapters.LocalCRMAdapter)
            contact = db.query(CrmContact).filter(CrmContact.id == payload["contact_id"]).one()
            conversation = db.query(CrmConversation).filter(CrmConversation.id == payload["conversation_id"]).one()
            self.assertEqual(contact.mobile_phone, "+15035551000")
            self.assertEqual(conversation.contact_id, contact.id)
            self.assertEqual(db.query(CrmMessage).filter(CrmMessage.contact_id == contact.id).count(), 2)

    def test_duplicate_contacts_prevented_by_normalized_phone_match(self) -> None:
        with mock.patch.dict(os.environ, {"COMMUNICATION_CRM_ADAPTER": "local"}, clear=False):
            first = self.client.post(
                "/api/inbound/message",
                json={"channel": "sms", "from": "(503) 555-1001", "message": "Need pricing."},
            )
            second = self.client.post(
                "/api/inbound/message",
                json={"channel": "sms", "from": "+1 503 555 1001", "message": "Following up."},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["contact_id"], second.json()["contact_id"])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(CrmContact).filter(CrmContact.mobile_phone == "+15035551001").count(), 1)
            self.assertEqual(db.query(CrmConversation).count(), 1)

    def test_adapter_can_be_swapped_by_config(self) -> None:
        with mock.patch.dict(os.environ, {"COMMUNICATION_CRM_ADAPTER": "barkboys"}, clear=False):
            response = self.client.post(
                "/api/inbound/message",
                json={"channel": "sms", "from": "+15035551002", "message": "Can you quote mulch delivery?"},
            )
            self.assertEqual(response.status_code, 200)
            with self.SessionLocal() as db:
                adapter = crm_adapters.get_crm_adapter(db)
                self.assertIsInstance(adapter, crm_adapters.BarkBoysCRMAdapter)
                context = adapter.get_contact_context(response.json()["contact_id"])
                self.assertEqual(context["adapter"], "barkboys")
                self.assertIn("todo", context["barkboys"])

    def test_barkboys_adapter_scaffold_loads_without_breaking_workspace_api(self) -> None:
        with mock.patch.dict(os.environ, {"COMMUNICATION_CRM_ADAPTER": "barkboys"}, clear=False):
            inbound = self.client.post(
                "/api/twilio/sms/inbound",
                data={
                    "From": "+15035551003",
                    "To": "+15035550000",
                    "Body": "Need help with a quote.",
                    "MessageSid": "SM-adapter-barkboys-1",
                },
            )
            inbox = self.client.get("/crm/api/conversations")

        self.assertEqual(inbound.status_code, 200)
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.json()[0]["mobile_phone"], "+15035551003")


if __name__ == "__main__":
    unittest.main()
