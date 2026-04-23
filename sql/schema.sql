CREATE TABLE IF NOT EXISTS signals (
  id SERIAL PRIMARY KEY,
  date_observed DATE NOT NULL,
  type VARCHAR(64) NOT NULL,
  indicator TEXT NOT NULL,
  impact TEXT NOT NULL,
  source VARCHAR(128) NOT NULL,
  relevance VARCHAR(32) NOT NULL,
  score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 10),
  reviewer VARCHAR(32) NOT NULL,
  reviewed BOOLEAN NOT NULL DEFAULT FALSE,
  include BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS leads (
  id SERIAL PRIMARY KEY,
  name VARCHAR(128),
  customer_name VARCHAR(128),
  email VARCHAR(256) NOT NULL,
  company VARCHAR(128),
  phone VARCHAR(64),
  address VARCHAR(256),
  sales_rep VARCHAR(128),
  follow_up_date DATE,
  quote_amount NUMERIC(10,2),
  quote_id INTEGER,
  job_notes TEXT,
  status VARCHAR(32) DEFAULT 'new',
  message TEXT,
  request_sample BOOLEAN NOT NULL DEFAULT FALSE,
  page_url VARCHAR(256),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS leads_email_idx ON leads (email);
CREATE INDEX IF NOT EXISTS leads_status_idx ON leads (status);
CREATE INDEX IF NOT EXISTS leads_quote_id_idx ON leads (quote_id);

CREATE TABLE IF NOT EXISTS jobs (
  id SERIAL PRIMARY KEY,
  customer_name VARCHAR(128) NOT NULL,
  phone VARCHAR(64),
  email VARCHAR(256),
  address VARCHAR(256) NOT NULL,
  zip_code VARCHAR(16),
  area_sqft NUMERIC(12,2),
  terrain_type VARCHAR(32),
  primary_job_type VARCHAR(64),
  detected_tasks_json TEXT,
  sales_rep VARCHAR(128),
  follow_up_date DATE,
  lead_status VARCHAR(32),
  notes TEXT,
  exclusions TEXT,
  crew_instructions TEXT,
  estimated_labor_hours NUMERIC(10,2),
  material_cost NUMERIC(10,2),
  equipment_cost NUMERIC(10,2),
  suggested_price NUMERIC(10,2),
  source VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS jobs_email_idx ON jobs (email);

CREATE TABLE IF NOT EXISTS job_photos (
  id SERIAL PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  file_name VARCHAR(255) NOT NULL,
  content_type VARCHAR(128),
  file_size INTEGER NOT NULL DEFAULT 0,
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS job_photos_job_id_idx ON job_photos (job_id);

CREATE TABLE IF NOT EXISTS quotes (
  id SERIAL PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  frequency VARCHAR(32) NOT NULL DEFAULT 'monthly',
  tax_rate NUMERIC(5,2) NOT NULL DEFAULT 0,
  zone_modifier_percent NUMERIC(5,2) NOT NULL DEFAULT 0,
  frequency_discount_percent NUMERIC(5,2) NOT NULL DEFAULT 0,
  subtotal NUMERIC(10,2) NOT NULL,
  zone_adjustment NUMERIC(10,2) NOT NULL DEFAULT 0,
  discount_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
  tax_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
  total NUMERIC(10,2) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS quotes_job_id_idx ON quotes (job_id);

CREATE TABLE IF NOT EXISTS quote_items (
  id SERIAL PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  name VARCHAR(128) NOT NULL,
  quantity NUMERIC(10,2) NOT NULL,
  unit VARCHAR(32) NOT NULL DEFAULT 'each',
  base_price NUMERIC(10,2) NOT NULL DEFAULT 0,
  per_unit_price NUMERIC(10,2) NOT NULL DEFAULT 0,
  min_charge NUMERIC(10,2) NOT NULL DEFAULT 0,
  line_total NUMERIC(10,2) NOT NULL
);

CREATE INDEX IF NOT EXISTS quote_items_quote_id_idx ON quote_items (quote_id);

CREATE TABLE IF NOT EXISTS quote_media (
  id SERIAL PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  file_name VARCHAR(255) NOT NULL,
  content_type VARCHAR(128),
  file_size INTEGER NOT NULL DEFAULT 0,
  media_kind VARCHAR(32) NOT NULL DEFAULT 'photo',
  capture_device VARCHAR(64),
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS quote_media_quote_id_idx ON quote_media (quote_id);
CREATE INDEX IF NOT EXISTS quote_media_kind_idx ON quote_media (media_kind);

CREATE TABLE IF NOT EXISTS intake_submissions (
  id SERIAL PRIMARY KEY,
  customer_name VARCHAR(128) NOT NULL,
  phone VARCHAR(64),
  email VARCHAR(256),
  address VARCHAR(256) NOT NULL,
  notes TEXT,
  capture_device VARCHAR(64),
  framed_inputs_json TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'new',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS intake_submissions_email_idx ON intake_submissions (email);
CREATE INDEX IF NOT EXISTS intake_submissions_status_idx ON intake_submissions (status);

CREATE TABLE IF NOT EXISTS intake_media (
  id SERIAL PRIMARY KEY,
  intake_submission_id INTEGER NOT NULL REFERENCES intake_submissions(id) ON DELETE CASCADE,
  file_name VARCHAR(255) NOT NULL,
  content_type VARCHAR(128),
  file_size INTEGER NOT NULL DEFAULT 0,
  media_kind VARCHAR(32) NOT NULL DEFAULT 'photo',
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS intake_media_submission_id_idx ON intake_media (intake_submission_id);

CREATE TABLE IF NOT EXISTS quote_events (
  id SERIAL PRIMARY KEY,
  event_name VARCHAR(64) NOT NULL,
  quote_id INTEGER REFERENCES quotes(id) ON DELETE SET NULL,
  metadata_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS quote_events_event_name_idx ON quote_events (event_name);
CREATE INDEX IF NOT EXISTS quote_events_quote_id_idx ON quote_events (quote_id);
