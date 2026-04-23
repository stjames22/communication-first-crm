from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Literal, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except ImportError:  # pragma: no cover
    from pydantic import BaseModel, Field
    ConfigDict = None


class ORMModel(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(from_attributes=True)
    else:
        class Config:
            orm_mode = True


class SignalBase(ORMModel):
    date_observed: date
    type: str = Field(min_length=1, max_length=64)
    indicator: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=128)
    relevance: str = Field(min_length=1, max_length=32)
    score: float = Field(ge=0, le=10)
    reviewer: str = Field(min_length=1, max_length=32)
    reviewed: bool = False
    include: bool = True


class SignalCreate(SignalBase):
    pass


class SignalUpdate(SignalBase):
    pass


class SignalOut(SignalBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ImportResult(ORMModel):
    created: int
    errors: List[dict]


class LeadCreate(ORMModel):
    name: Optional[str] = None
    customer_name: Optional[str] = None
    email: str
    company: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    sales_rep: Optional[str] = None
    follow_up_date: Optional[date] = None
    quote_amount: Optional[Decimal] = None
    job_notes: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    request_sample: bool = False
    page_url: Optional[str] = None
    website: Optional[str] = None


class LeadOut(LeadCreate):
    id: int
    created_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None


class LeadUpdate(ORMModel):
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    sales_rep: Optional[str] = None
    follow_up_date: Optional[date] = None
    quote_amount: Optional[Decimal] = None
    job_notes: Optional[str] = None
    status: Optional[str] = None


FrequencyType = Literal["one_time", "weekly", "biweekly", "monthly"]


class JobBase(ORMModel):
    customer_name: str = Field(min_length=1, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=64)
    email: Optional[str] = Field(default=None, max_length=256)
    address: str = Field(min_length=1, max_length=256)
    zip_code: Optional[str] = Field(default=None, max_length=16)
    area_sqft: Optional[Decimal] = None
    terrain_type: Optional[str] = Field(default=None, max_length=32)
    primary_job_type: Optional[str] = Field(default=None, max_length=64)
    detected_tasks: Optional[List[dict[str, Any]]] = None
    sales_rep: Optional[str] = Field(default=None, max_length=128)
    follow_up_date: Optional[date] = None
    lead_status: Optional[str] = Field(default=None, max_length=32)
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    exclusions: Optional[str] = None
    crew_instructions: Optional[str] = None
    estimated_labor_hours: Optional[Decimal] = None
    material_cost: Optional[Decimal] = None
    equipment_cost: Optional[Decimal] = None
    suggested_price: Optional[Decimal] = None
    source: Optional[str] = Field(default=None, max_length=128)


class JobCreate(JobBase):
    pass


class JobOut(JobBase):
    id: int
    created_at: Optional[datetime] = None


class QuoteItemInput(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    quantity: Decimal = Field(default=0, ge=0)
    unit: str = Field(default="each", min_length=1, max_length=32)
    base_price: Decimal = Field(default=0, ge=0)
    per_unit_price: Decimal = Field(default=0, ge=0)
    min_charge: Decimal = Field(default=0, ge=0)


class QuoteItemOut(QuoteItemInput):
    id: int
    line_total: Decimal = Field(ge=0)


class QuoteItemPreviewOut(QuoteItemInput):
    line_total: Decimal = Field(ge=0)


class UploadedAssetRef(ORMModel):
    id: int
    category: Optional[str] = None
    parseMode: Optional[str] = "auto"
    parserResult: Optional[dict] = None
    status: Optional[str] = "uploaded"
    error: Optional[str] = None


class UploadedAssetOut(ORMModel):
    id: int
    url: str
    storageKey: str
    filename: str
    mimeType: Optional[str] = None
    size: int
    category: str
    createdAt: Optional[datetime] = None
    media_kind: Optional[str] = None
    parseMode: Optional[str] = "auto"
    parserResult: Optional[dict] = None
    status: Optional[str] = "uploaded"
    error: Optional[str] = None
    capture_device: Optional[str] = None


class QuoteCreate(ORMModel):
    job: JobCreate
    items: List[QuoteItemInput] = Field(min_items=1)
    frequency: FrequencyType = "monthly"
    tax_rate: Decimal = Field(default=0, ge=0, le=100)
    zone_modifier_percent: Decimal = Field(default=0, ge=-100, le=100)
    uploaded_assets: List[UploadedAssetRef] = Field(default_factory=list)
    intake_submission_id: Optional[int] = None


class QuotePricingOut(ORMModel):
    subtotal: Decimal = Field(ge=0)
    zone_adjustment: Decimal
    frequency_discount_percent: Decimal = Field(ge=0)
    discount_amount: Decimal = Field(ge=0)
    tax_rate: Decimal = Field(ge=0)
    tax_amount: Decimal = Field(ge=0)
    total: Decimal = Field(ge=0)


class QuoteOut(QuotePricingOut):
    id: int
    created_at: Optional[datetime] = None
    frequency: FrequencyType
    zone_modifier_percent: Decimal
    job: JobOut
    items: List[QuoteItemOut]
    text_quote: str
    media: List[UploadedAssetOut] = Field(default_factory=list)
    job_photos: List[UploadedAssetOut] = Field(default_factory=list)


class QuoteTextOut(ORMModel):
    quote_id: int
    text_quote: str


class QuotePreviewCreate(ORMModel):
    items: List[QuoteItemInput] = Field(min_items=1)
    frequency: FrequencyType = "monthly"
    tax_rate: Decimal = Field(default=0, ge=0, le=100)
    zone_modifier_percent: Decimal = Field(default=0, ge=-100, le=100)


class QuotePreviewOut(QuotePricingOut):
    frequency: FrequencyType
    zone_modifier_percent: Decimal
    items: List[QuoteItemPreviewOut]
