# BarkBoys Estimator Product (GrowthSignal API)

FastAPI backend with a testable BarkBoys estimator UI.

## What is included
- `/staff-estimator` staff-only estimator UI for quote building
- `/staff-crm` staff quote vault (CRM-lite) for saved quote search/re-open
- `/public-estimator` public intake page for customer photo submissions
- `/quote-templates` default service templates
- `/quotes/preview` live estimator totals (no DB save)
- `/quotes/ai-preview` demo AI draft from framed photo/site inputs
- `/quotes` save quote
- `/quotes/crm` list/search saved quote summaries for CRM-lite
- `/intake-submissions` create customer intake with photos/LiDAR
- `/intake-submissions/{id}/ai-draft` convert intake into draft quote payload
- `/quotes/{id}/media` upload site photos/LiDAR scan files
- `/quotes/{id}/text` text-ready output
- `/quotes/{id}/pdf` PDF export
- Optional Basic Auth gate for staff estimator page
- Event tracking in `quote_events`:
  - `preview_used`
  - `quote_saved`
  - `pdf_downloaded`

## One-click local test (recommended, Docker/Postgres)
Double-click this file in Finder:
- `START_TEST.command`

It will:
1. Start Docker services
2. Build API container
3. Open the estimator URL automatically

Test credentials:
- Estimator login: `demo` / `demo123`

Test URL:
- [http://localhost:8000/staff-estimator](http://localhost:8000/staff-estimator)
- [http://localhost:8000/staff-crm](http://localhost:8000/staff-crm)
- [http://localhost:8000/public-estimator](http://localhost:8000/public-estimator)

## Manual Docker startup
```bash
cd backend
docker compose up --build -d
```

## Railway Deploy

This repo is now prepared for Railway deployment from the `barkboys` repo root.

Recommended Railway setup:

1. Push `barkboys` to GitHub
2. Create a Railway project from that repo
3. Add a PostgreSQL service
4. Generate a public domain for the web service
5. Set environment variables on the app service:

```env
GS_STORAGE_BACKEND=s3
GS_UPLOADS_PREFIX=barkboys
GS_S3_BUCKET=your-bucket
GS_S3_REGION=us-west-2
GS_S3_ENDPOINT_URL=https://s3.amazonaws.com
GS_S3_ACCESS_KEY_ID=your-access-key
GS_S3_SECRET_ACCESS_KEY=your-secret-key
GS_API_KEY=replace-this
GS_ESTIMATOR_USER=demo
GS_ESTIMATOR_PASSWORD=change-this
OPENAI_API_KEY=your_openai_api_key_here
GS_CORS_ORIGINS=https://your-generated-domain.up.railway.app
```

Notes:
- Railway Postgres provides `DATABASE_URL`, and BarkBoys now accepts that automatically if `GS_DATABASE_URL` is not set.
- If `GS_CORS_ORIGINS` is not set on Railway, BarkBoys now falls back to `RAILWAY_PUBLIC_DOMAIN` automatically.
- Root deploy files live in `barkboys/Dockerfile`, `barkboys/.dockerignore`, and `barkboys/railway.toml`.
- Root Railway variable suggestions now live in `barkboys/.env.example`.
- Healthcheck path is `/health`.

## Local startup (SQLite, no Docker)
```bash
cd backend
./START_LOCAL.command
```

Simpler morning launcher:

```bash
cd backend
./START_BARKBOYS.command
```

Notes:
- The launcher reuses an existing healthy `.venv` and only installs dependencies when they are missing.
- First run still needs internet access unless you copy in a prebuilt `.venv` from another Mac with the same Python version.
- `START_BARKBOYS.command` is the easiest daily entry point because it starts BarkBoys quietly when a key is already saved.
- It only prompts for an OpenAI API key when no key is saved in `backend/.env`.
- It now also detects an older local process already using port `8000` and offers to stop it before starting a fresh instance.
- Admin-only key rotation is separate and intentional:

```bash
cd backend
./START_BARKBOYS.command --update-key
```

Then open:
- [http://localhost:8000/staff-estimator](http://localhost:8000/staff-estimator)
- [http://localhost:8000/staff-crm](http://localhost:8000/staff-crm)
- [http://localhost:8000/public-estimator](http://localhost:8000/public-estimator)

## Fast OpenAI Key Update
If the AI badge shows `Missing API key` or `Key rejected`, run:

```bash
cd backend
./UPDATE_OPENAI_KEY.command
./START_LOCAL.command
```

Notes:
- The updater prompts for the key and writes it into `backend/.env`.
- `START_LOCAL.command` reads `backend/.env` automatically on each launch.
- If BarkBoys is already running when you change the key, fully stop it and start it again before rechecking the AI badge or retrying uploads.
- This is the easiest repeatable local recovery path for tomorrow morning and future demos.
- If you want one file instead of two commands, use `./START_BARKBOYS.command`.

## Demo readiness for handwritten notes
- `START_LOCAL.command` and `START_DEMO.command` now print an `OpenAI status` line before the app launches.
- Those launchers also enable the trusted local Apple Vision OCR fallback for handwritten dimension rows.
- If the status is `AI Ready`, handwritten note parsing should be available.
- If the status is `AI Error - DNS failure`, this Mac cannot resolve `api.openai.com`; check Wi-Fi, VPN, proxy, DNS, or content-filter settings.
- If the status is `AI Error - Key rejected`, run `./UPDATE_OPENAI_KEY.command` or update `OPENAI_API_KEY` in `backend/.env`, then fully restart BarkBoys.
- If OpenAI is unavailable during the demo, use the `Measurement Review` paste box or `Add Manual Row` controls so the quote can still be built from confirmed dimensions.

## Field test on another device (same Wi-Fi)
- Start the app with `./START_LOCAL.command`, `./START_DEMO.command`, or `./START_TEST.command`
- The launcher prints a `Field test URL on same Wi-Fi` using this Mac's LAN IP
- Open that URL on the BarkBoys phone/tablet browser
- If the device cannot connect, allow incoming connections for Terminal/Python or Docker Desktop in macOS Firewall
- For reliable handwritten note extraction, create `backend/.env` with:

```env
OPENAI_API_KEY=your_openai_api_key_here
GS_OPENAI_VISION_MODEL=gpt-4.1
```

## Package for Demo Transfer (Mac to Mac)
To create a shareable demo zip (includes current `barkboys_estimator.db` and `uploads`, excludes `.venv`):

1. In Finder, open `backend`
2. Double-click `PACKAGE_DEMO.command`
3. A zip like `Barkboys-demo-ready-YYYY-MM-DD-HHMMSS.zip` is created in `backend`

On the target MacBook Air:
1. Copy that zip
2. Unzip it
3. Open the unzipped `backend` folder
4. Double-click `START_DEMO.command`

Demo note:
- `START_DEMO.command` reuses any working `.venv`. If the target Mac does not already have the required Python packages, the first launch still needs internet access to install them.

## Service templates
Templates are stored in:
- `app/data/service_templates.json`

## Seed data
Startup seeds one demo quote if none exists via:
- `python -m scripts.seed_demo`

## Photo/LiDAR uploads
- Estimator UI supports on-site device selection (including iPhone LiDAR) and file capture.
- Uploaded files can be stored either:
  - locally under `backend/uploads/...`
  - in S3-compatible object storage when `GS_STORAGE_BACKEND=s3`
- Supported capture types:
  - Photos: `image/*`, `.heic`, `.heif`
  - LiDAR scans: `.usdz`, `.ply`, `.obj`, `.las`, `.laz`, `.zip`

## S3-Compatible Storage

To move uploads out of the local filesystem, set:

```env
GS_STORAGE_BACKEND=s3
GS_UPLOADS_PREFIX=barkboys
GS_S3_BUCKET=your-bucket
GS_S3_REGION=us-west-2
GS_S3_ENDPOINT_URL=https://s3.amazonaws.com
GS_S3_ACCESS_KEY_ID=your-access-key
GS_S3_SECRET_ACCESS_KEY=your-secret-key
```

Notes:
- Works with AWS S3 and S3-compatible providers.
- Existing local records still read normally.
- New uploads/imported media will be written through the configured storage backend.

## AI Demo Estimate From Photos
- Use estimator button: `Generate AI Draft From Photos`
- Framed inputs requested in UI:
  - Lot size, edge length, turf condition, slope
  - Obstacles count, debris level, gates, haul-away, blowing
  - Front/back/left/right site photos for highest confidence
- Endpoint:
  - `POST /quotes/ai-preview` (multipart form)
- Output:
  - Suggested line items + pricing
  - Confidence score
  - Missing photo-angle checklist
  - Staff review recommendation

## Customer Upload Flow
- Public page for customers:
  - `/public-estimator`
- Submission endpoint:
  - `POST /intake-submissions` (multipart, no API key)
- Low-friction defaults:
  - iPhone/LiDAR is optional; any phone photos work
  - Minimal required fields: name, address, phone-or-email, photos
  - Required quick selectors: material needed, delivery truck type, placement method (blown-in/conveyor/dumped)
  - Customer disclosure is shown on page: estimate is based on customer input and reviewed by staff for accuracy
  - Returns instant rough estimate range in submit response
- Staff review list:
  - `GET /intake-submissions`
- One-click intake conversion:
  - `POST /intake-submissions/{id}/ai-draft`
  - Estimator UI: `Load Intake As Draft Quote`
- Customer uploads are stored under:
  - `backend/uploads/intake-<id>/`

## Route Compatibility
- Legacy staff route still works: `/estimator`
- Legacy public route still works: `/customer-upload`

## MVP success criteria for pilot
- Staff can generate a quote in < 2 minutes
- Preview and final saved totals match expected pricing
- PDF download works for saved quotes

See pilot run sheet:
- `PILOT_TEST_PLAN.md`
