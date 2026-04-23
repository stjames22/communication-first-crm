from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from .db import Base


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    date_observed = Column(Date, nullable=False)
    type = Column(String(64), nullable=False)
    indicator = Column(Text, nullable=False)
    impact = Column(Text, nullable=False)
    source = Column(String(128), nullable=False)
    relevance = Column(String(32), nullable=False)
    score = Column(Float, nullable=False)
    reviewer = Column(String(32), nullable=False)
    reviewed = Column(Boolean, nullable=False, default=False)
    include = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=True)
    customer_name = Column(String(128), nullable=True)
    email = Column(String(256), nullable=False, index=True)
    company = Column(String(128), nullable=True)
    phone = Column(String(64), nullable=True)
    address = Column(String(256), nullable=True)
    sales_rep = Column(String(128), nullable=True)
    follow_up_date = Column(Date, nullable=True)
    quote_amount = Column(Numeric(10, 2), nullable=True)
    quote_id = Column(Integer, ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True, index=True)
    job_notes = Column(Text, nullable=True)
    status = Column(String(32), nullable=True, default="new")
    message = Column(Text, nullable=True)
    request_sample = Column(Boolean, nullable=False, default=False)
    page_url = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String(128), nullable=False)
    phone = Column(String(64), nullable=True)
    email = Column(String(256), nullable=True, index=True)
    address = Column(String(256), nullable=False)
    zip_code = Column(String(16), nullable=True)
    area_sqft = Column(Numeric(12, 2), nullable=True)
    terrain_type = Column(String(32), nullable=True)
    primary_job_type = Column(String(64), nullable=True)
    detected_tasks_json = Column(Text, nullable=True)
    sales_rep = Column(String(128), nullable=True)
    follow_up_date = Column(Date, nullable=True)
    lead_status = Column(String(32), nullable=True)
    notes = Column(Text, nullable=True)
    internal_notes = Column(Text, nullable=True)
    exclusions = Column(Text, nullable=True)
    crew_instructions = Column(Text, nullable=True)
    estimated_labor_hours = Column(Numeric(10, 2), nullable=True)
    material_cost = Column(Numeric(10, 2), nullable=True)
    equipment_cost = Column(Numeric(10, 2), nullable=True)
    suggested_price = Column(Numeric(10, 2), nullable=True)
    source = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    quotes = relationship("Quote", back_populates="job", cascade="all, delete-orphan")
    photos = relationship("JobPhoto", back_populates="job", cascade="all, delete-orphan")


class JobPhoto(Base):
    __tablename__ = "job_photos"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=False, default=0)
    storage_path = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job = relationship("Job", back_populates="photos")


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    frequency = Column(String(32), nullable=False, default="monthly")
    tax_rate = Column(Numeric(5, 2), nullable=False, default=0)
    zone_modifier_percent = Column(Numeric(5, 2), nullable=False, default=0)
    frequency_discount_percent = Column(Numeric(5, 2), nullable=False, default=0)
    subtotal = Column(Numeric(10, 2), nullable=False)
    zone_adjustment = Column(Numeric(10, 2), nullable=False, default=0)
    discount_amount = Column(Numeric(10, 2), nullable=False, default=0)
    tax_amount = Column(Numeric(10, 2), nullable=False, default=0)
    total = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job = relationship("Job", back_populates="quotes")
    items = relationship("QuoteItem", back_populates="quote", cascade="all, delete-orphan")
    media = relationship("QuoteMedia", back_populates="quote", cascade="all, delete-orphan")


class QuoteItem(Base):
    __tablename__ = "quote_items"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), nullable=False)
    unit = Column(String(32), nullable=False, default="each")
    base_price = Column(Numeric(10, 2), nullable=False, default=0)
    per_unit_price = Column(Numeric(10, 2), nullable=False, default=0)
    min_charge = Column(Numeric(10, 2), nullable=False, default=0)
    line_total = Column(Numeric(10, 2), nullable=False)

    quote = relationship("Quote", back_populates="items")


class QuoteEvent(Base):
    __tablename__ = "quote_events"

    id = Column(Integer, primary_key=True, index=True)
    event_name = Column(String(64), nullable=False, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class QuoteMedia(Base):
    __tablename__ = "quote_media"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=False, default=0)
    media_kind = Column(String(32), nullable=False, default="photo")
    parse_mode = Column(String(32), nullable=False, default="auto")
    parse_result_json = Column(Text, nullable=True)
    upload_status = Column(String(32), nullable=False, default="uploaded")
    error_message = Column(Text, nullable=True)
    capture_device = Column(String(64), nullable=True)
    storage_path = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    quote = relationship("Quote", back_populates="media")


class UploadedAsset(Base):
    __tablename__ = "uploaded_assets"

    id = Column(Integer, primary_key=True, index=True)
    draft_token = Column(String(64), nullable=False, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    file_name = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=False, default=0)
    category = Column(String(32), nullable=False, default="site_media")
    media_kind = Column(String(32), nullable=False, default="photo")
    parse_mode = Column(String(32), nullable=False, default="auto")
    parse_result_json = Column(Text, nullable=True)
    upload_status = Column(String(32), nullable=False, default="uploaded")
    error_message = Column(Text, nullable=True)
    storage_path = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IntakeSubmission(Base):
    __tablename__ = "intake_submissions"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String(128), nullable=False)
    phone = Column(String(64), nullable=True)
    email = Column(String(256), nullable=True, index=True)
    address = Column(String(256), nullable=False)
    notes = Column(Text, nullable=True)
    capture_device = Column(String(64), nullable=True)
    framed_inputs_json = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="new")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    media = relationship("IntakeMedia", back_populates="submission", cascade="all, delete-orphan")


class IntakeMedia(Base):
    __tablename__ = "intake_media"

    id = Column(Integer, primary_key=True, index=True)
    intake_submission_id = Column(
        Integer,
        ForeignKey("intake_submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=False, default=0)
    media_kind = Column(String(32), nullable=False, default="photo")
    storage_path = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    submission = relationship("IntakeSubmission", back_populates="media")
