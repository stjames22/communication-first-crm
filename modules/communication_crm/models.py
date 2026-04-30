from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class CrmContact(Base):
    __tablename__ = "crm_contacts"

    id = Column(String(36), primary_key=True, default=_uuid)
    display_name = Column(String(160), nullable=False)
    mobile_phone = Column(String(64), nullable=False, unique=True, index=True)
    email = Column(String(256), nullable=True)
    status = Column(String(32), nullable=False, default="lead", index=True)
    source = Column(String(80), nullable=True)
    assigned_user = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    sites = relationship("CrmServiceSite", back_populates="contact", cascade="all, delete-orphan")
    conversations = relationship("CrmConversation", back_populates="contact", cascade="all, delete-orphan")
    activities = relationship("CrmActivity", back_populates="contact", cascade="all, delete-orphan")
    tasks = relationship("CrmTask", back_populates="contact", cascade="all, delete-orphan")
    quotes = relationship("CrmQuote", back_populates="contact", cascade="all, delete-orphan")


class CrmServiceSite(Base):
    __tablename__ = "crm_service_sites"

    id = Column(String(36), primary_key=True, default=_uuid)
    contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String(80), nullable=False, default="Primary")
    address_line_1 = Column(String(256), nullable=False)
    city = Column(String(128), nullable=False)
    state = Column(String(32), nullable=False)
    zip = Column(String(24), nullable=False)
    delivery_zone = Column(String(80), nullable=True)
    site_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    contact = relationship("CrmContact", back_populates="sites")


class CrmConversation(Base):
    __tablename__ = "crm_conversations"

    id = Column(String(36), primary_key=True, default=_uuid)
    contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_type = Column(String(32), nullable=False, default="sms")
    status = Column(String(32), nullable=False, default="open")
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    unread_count = Column(Integer, nullable=False, default=0)
    assigned_user = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    contact = relationship("CrmContact", back_populates="conversations")
    messages = relationship("CrmMessage", back_populates="conversation", cascade="all, delete-orphan")


class CrmMessage(Base):
    __tablename__ = "crm_messages"

    id = Column(String(36), primary_key=True, default=_uuid)
    conversation_id = Column(String(36), ForeignKey("crm_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    direction = Column(String(16), nullable=False)
    channel = Column(String(32), nullable=False, default="sms")
    provider_message_id = Column(String(160), nullable=True, unique=True)
    body = Column(Text, nullable=False)
    delivery_status = Column(String(32), nullable=False, default="queued")
    sent_by_user = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("CrmConversation", back_populates="messages")


class CrmCall(Base):
    __tablename__ = "crm_calls"

    id = Column(String(36), primary_key=True, default=_uuid)
    contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(String(36), ForeignKey("crm_conversations.id", ondelete="SET NULL"), nullable=True)
    provider_call_id = Column(String(160), nullable=True, unique=True)
    direction = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False, default="logged")
    from_number = Column(String(64), nullable=False)
    to_number = Column(String(64), nullable=False)
    duration_seconds = Column(Integer, nullable=True)
    disposition = Column(String(80), nullable=True)
    notes = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)


class CrmQuote(Base):
    __tablename__ = "crm_quotes"

    id = Column(String(36), primary_key=True, default=_uuid)
    contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    service_site_id = Column(String(36), ForeignKey("crm_service_sites.id", ondelete="SET NULL"), nullable=True)
    quote_number = Column(String(80), nullable=False, unique=True)
    title = Column(String(180), nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    delivery_total = Column(Numeric(12, 2), nullable=False, default=0)
    tax_total = Column(Numeric(12, 2), nullable=False, default=0)
    grand_total = Column(Numeric(12, 2), nullable=False, default=0)
    current_version_id = Column(String(36), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    contact = relationship("CrmContact", back_populates="quotes")
    versions = relationship("CrmQuoteVersion", back_populates="quote", cascade="all, delete-orphan")


class CrmQuoteVersion(Base):
    __tablename__ = "crm_quote_versions"

    id = Column(String(36), primary_key=True, default=_uuid)
    quote_id = Column(String(36), ForeignKey("crm_quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    pricing_snapshot_json = Column(Text, nullable=False, default="{}")
    notes = Column(Text, nullable=True)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    delivery_total = Column(Numeric(12, 2), nullable=False, default=0)
    tax_total = Column(Numeric(12, 2), nullable=False, default=0)
    grand_total = Column(Numeric(12, 2), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    quote = relationship("CrmQuote", back_populates="versions")
    line_items = relationship("CrmQuoteLineItem", back_populates="version", cascade="all, delete-orphan")


class CrmQuoteLineItem(Base):
    __tablename__ = "crm_quote_line_items"

    id = Column(String(36), primary_key=True, default=_uuid)
    quote_version_id = Column(String(36), ForeignKey("crm_quote_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    item_type = Column(String(40), nullable=False, default="service")
    name = Column(String(180), nullable=False)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(12, 2), nullable=False, default=1)
    unit = Column(String(32), nullable=False, default="each")
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    total_price = Column(Numeric(12, 2), nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    source_reference = Column(String(160), nullable=True)

    version = relationship("CrmQuoteVersion", back_populates="line_items")


class CrmActivity(Base):
    __tablename__ = "crm_activities"

    id = Column(String(36), primary_key=True, default=_uuid)
    contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    related_type = Column(String(64), nullable=False)
    related_id = Column(String(64), nullable=True)
    activity_type = Column(String(80), nullable=False)
    title = Column(String(180), nullable=False)
    body = Column(Text, nullable=True)
    actor_user = Column(String(128), nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    contact = relationship("CrmContact", back_populates="activities")


class CrmTask(Base):
    __tablename__ = "crm_tasks"

    id = Column(String(36), primary_key=True, default=_uuid)
    contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_user = Column(String(128), nullable=True)
    title = Column(String(220), nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="open", index=True)
    priority = Column(String(32), nullable=False, default="normal")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    contact = relationship("CrmContact", back_populates="tasks")


class CrmExternalLink(Base):
    __tablename__ = "crm_external_links"

    id = Column(String(36), primary_key=True, default=_uuid)
    internal_type = Column(String(64), nullable=False)
    internal_id = Column(String(64), nullable=False)
    external_system = Column(String(80), nullable=False)
    external_id = Column(String(160), nullable=False)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CrmLeadSignal(Base):
    __tablename__ = "crm_lead_signals"

    id = Column(String(36), primary_key=True, default=_uuid)
    source_type = Column(String(40), nullable=False, index=True)
    source_url = Column(String(512), nullable=True)
    area_location = Column(String(160), nullable=True)
    raw_text = Column(Text, nullable=False)
    lead_score = Column(Integer, nullable=False, default=0, index=True)
    lead_type = Column(String(64), nullable=False, default="unknown", index=True)
    urgency = Column(String(24), nullable=False, default="low", index=True)
    location_detected = Column(String(160), nullable=True)
    intent_summary = Column(Text, nullable=False)
    suggested_reply = Column(Text, nullable=False)
    recommended_action = Column(String(32), nullable=False, default="watch", index=True)
    matched_keywords_json = Column(Text, nullable=False, default="[]")
    status = Column(String(32), nullable=False, default="new", index=True)
    attached_contact_id = Column(String(36), ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CrmWebsiteEvent(Base):
    __tablename__ = "crm_website_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    event_type = Column(String(40), nullable=False, index=True)
    source_system = Column(String(80), nullable=False, default="wordpress", index=True)
    page_url = Column(String(512), nullable=True)
    page_title = Column(String(255), nullable=True)
    referrer = Column(String(512), nullable=True)
    link_key = Column(String(120), nullable=True, index=True)
    link_label = Column(String(190), nullable=True)
    campaign = Column(String(120), nullable=True, index=True)
    destination_url = Column(String(512), nullable=True)
    visitor_id_hash = Column(String(128), nullable=True, index=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


Index("crm_external_links_internal_idx", CrmExternalLink.internal_type, CrmExternalLink.internal_id)
Index("crm_external_links_external_idx", CrmExternalLink.external_system, CrmExternalLink.external_id)
Index("crm_website_events_type_created_idx", CrmWebsiteEvent.event_type, CrmWebsiteEvent.created_at)


def create_crm_tables(bind) -> None:
    Base.metadata.create_all(
        bind=bind,
        tables=[
            CrmContact.__table__,
            CrmServiceSite.__table__,
            CrmConversation.__table__,
            CrmMessage.__table__,
            CrmCall.__table__,
            CrmQuote.__table__,
            CrmQuoteVersion.__table__,
            CrmQuoteLineItem.__table__,
            CrmActivity.__table__,
            CrmTask.__table__,
            CrmExternalLink.__table__,
            CrmLeadSignal.__table__,
            CrmWebsiteEvent.__table__,
        ],
    )
