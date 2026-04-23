# BarkBoys Web Deployment

BarkBoys is already a web app:

- FastAPI serves the HTML pages and JSON APIs
- Docker support is already included
- Postgres is already supported through `GS_DATABASE_URL`

## Recommended hosted shape

For shared testing and ongoing development, use:

- 1 web service for the FastAPI app
- 1 managed Postgres database
- 1 persistent disk/volume mounted for uploads

This keeps the current code structure mostly unchanged.

## Required environment variables

Minimum:

```env
GS_DATABASE_URL=postgresql+psycopg2://...
GS_UPLOADS_PATH=/app/uploads
GS_API_KEY=replace-this
GS_CORS_ORIGINS=https://your-app.example.com
GS_ESTIMATOR_USER=demo
GS_ESTIMATOR_PASSWORD=change-this
OPENAI_API_KEY=your_openai_api_key_here
GS_OPENAI_VISION_MODEL=gpt-4.1
GS_ALLOW_FALLBACK_HANDWRITTEN_MEASUREMENT_OCR=1
```

Optional:

```env
GS_OPENAI_CA_BUNDLE=/app/certs/trusted-root.pem
GS_OPENAI_ALLOW_INSECURE_SSL=1
```

Only use `GS_OPENAI_ALLOW_INSECURE_SSL=1` for temporary demo troubleshooting on a trusted network.

## Storage

You now have two supported storage modes:

### Option 1: S3-compatible object storage

Recommended for shared testing and long-lived hosted environments.

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
- OCR/PDF flows will download objects to temp files when local paths are needed.
- Existing local file records can still be read.

### Option 2: Persistent disk

Still valid for simple hosted testing:

```env
GS_STORAGE_BACKEND=local
GS_UPLOADS_PATH=/app/uploads
```

Without object storage or a persistent disk, customer uploads and quote media will disappear on redeploy or restart.

## Database

For hosted testing, switch from SQLite to Postgres:

```env
GS_DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
```

The container startup already runs:

- `python -m scripts.init_db`
- `python -m scripts.seed_demo`
- `uvicorn app.main:app ...`

## Suggested first deployment

### Option A: Render

Good fit if you want:

- Docker deploy from GitHub
- managed Postgres
- persistent disk
- simple shared preview URL

Typical setup:

1. Push the repo to GitHub.
2. Create a Postgres database.
3. Create a web service from the `backend` Dockerfile.
4. Either connect S3-compatible storage or mount a persistent disk at `/app/uploads`.
5. Add the environment variables above.
6. Deploy and test `/health`, `/public-estimator`, and `/staff-estimator`.

### Option B: Railway

Good fit if you want:

- fast Docker-based setup
- managed Postgres
- persistent volume support
- quick internal testing

Typical setup:

1. Push the repo to GitHub.
2. Create a Railway project.
3. Add Postgres.
4. Deploy the `backend` service from the Dockerfile.
5. Either connect S3-compatible storage or mount a volume at `/app/uploads`.
6. Set the same environment variables.

Railway-specific notes for this repo:

- Deploy from the `barkboys` repo root, not from `backend` directly.
- Railway config-as-code lives in [railway.toml](/Users/mac/Documents/New%20project/barkboys/railway.toml).
- The Railway root Docker build uses [Dockerfile](/Users/mac/Documents/New%20project/barkboys/Dockerfile).
- BarkBoys now accepts Railway's `DATABASE_URL` automatically when `GS_DATABASE_URL` is not set.
- If `GS_CORS_ORIGINS` is not set, BarkBoys falls back to `RAILWAY_PUBLIC_DOMAIN` automatically.
- Railway variable suggestions can now come from the root [`.env.example`](/Users/mac/Documents/New%20project/barkboys/.env.example).
- If you use S3-compatible storage, you do not need a Railway volume.

## Post-deploy smoke test

After the first deploy:

1. Open `/health`
2. Log into `/staff-estimator`
3. Open `/public-estimator`
4. Submit a small intake with photos
5. Confirm uploads persist after a redeploy
6. Confirm the AI health badge and OpenAI diagnostics look correct
