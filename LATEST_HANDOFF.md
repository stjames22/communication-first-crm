# BarkBoys Latest Handoff

Date: 2026-03-26 10:50 AM PDT
Workspace: `/Users/mac/Documents/New project/barkboys`

## Current Status

- BarkBoys is now deployed on Railway and serving the live hosted app.
- The hosted staff worksheet is loading successfully.
- The hosted AI badge is green and showing:
  - `AI Ready: gpt-4.1 connected`
- Local BarkBoys worksheet is also currently showing:
  - `AI Ready: gpt-4.1 connected (custom CA bundle)`
- GitHub is up to date and the local repo is clean.
- Cloudflare R2 bucket setup was completed for hosted media storage.
- Local AI diagnostics crash was fixed.
- Local Docker test flow now passes OpenAI env variables through when Docker is available.
- Local input/workflow regression coverage was expanded.
- Local morning startup path is now simpler via a dedicated launcher.

## What Was Completed Today

### 1. GitHub + Railway deployment path

- Initialized the local repo and pushed BarkBoys to GitHub:
  - `https://github.com/stjames22/Barkboys-estimator`
- Added Railway deploy files at the repo root:
  - `Dockerfile`
  - `railway.toml`
  - `.dockerignore`
  - `.env.example`
- BarkBoys now deploys from the repo root instead of requiring a manual `backend` subdirectory setup.

### 2. Hosted configuration improvements

- Added Railway-friendly env defaults:
  - `DATABASE_URL` fallback now works automatically when `GS_DATABASE_URL` is not set
  - `RAILWAY_PUBLIC_DOMAIN` now acts as a CORS fallback when `GS_CORS_ORIGINS` is not set
- Added root env hints so Railway can suggest variables from the repo root.
- Added a Railway-specific env template:
  - `backend/RAILWAY_ENV.example`

### 3. S3-compatible storage support

- Added a storage abstraction for local disk and S3-compatible backends:
  - `backend/app/storage.py`
- Rewired upload/import flows to use the storage layer instead of assuming local filesystem-only paths.
- Rewired OCR/PDF paths so hosted object storage can be materialized to temp files when a real path is needed.
- Cloudflare R2 was chosen as the S3-compatible provider.
- Confirmed bucket name:
  - `barkboys`

### 4. Hosted BarkBoys app configuration

- Railway app service was created and deployed successfully.
- Railway public domain was generated.
- Railway variables were configured for:
  - BarkBoys app secrets
  - OpenAI
  - Cloudflare R2
- OpenAI key was rotated after earlier exposure and the live hosted app now reports healthy AI connectivity.

### 5. Local reliability improvements

- Fixed local AI diagnostics crash caused by missing `ca_bundle` binding in `/health/openai/diagnostics`.
- Added local regression tests for:
  - intake input validation
  - intake normalization
  - oversized upload rejection
  - intake to quote to PDF workflow
  - AI diagnostics response stability
- Added local helper:
  - `backend/UPDATE_OPENAI_KEY.command`
- Added local morning launcher:
  - `backend/START_BARKBOYS.command`
- Purpose of the helper:
  - prompt for a new OpenAI API key
  - save it into `backend/.env`
  - reduce morning restart friction when a key needs rotation or replacement
- Purpose of the launcher:
  - start BarkBoys locally without prompting when a key is already saved
  - only prompt for key setup when no local key exists
  - detect and stop an older local server already bound to port `8000`
  - allow admin-only key rotation with:
    - `./START_BARKBOYS.command --update-key`

## Important Files Changed

- `/Users/mac/Documents/New project/barkboys/Dockerfile`
- `/Users/mac/Documents/New project/barkboys/railway.toml`
- `/Users/mac/Documents/New project/barkboys/.dockerignore`
- `/Users/mac/Documents/New project/barkboys/.env.example`
- `/Users/mac/Documents/New project/barkboys/backend/README.md`
- `/Users/mac/Documents/New project/barkboys/backend/DEPLOYMENT.md`
- `/Users/mac/Documents/New project/barkboys/backend/RAILWAY_ENV.example`
- `/Users/mac/Documents/New project/barkboys/backend/app/settings.py`
- `/Users/mac/Documents/New project/barkboys/backend/app/main.py`
- `/Users/mac/Documents/New project/barkboys/backend/app/storage.py`
- `/Users/mac/Documents/New project/barkboys/backend/app/ai_photo_analysis.py`
- `/Users/mac/Documents/New project/barkboys/backend/app/quote_output.py`
- `/Users/mac/Documents/New project/barkboys/backend/docker-compose.yml`
- `/Users/mac/Documents/New project/barkboys/backend/START_BARKBOYS.command`
- `/Users/mac/Documents/New project/barkboys/backend/UPDATE_OPENAI_KEY.command`
- `/Users/mac/Documents/New project/barkboys/backend/tests/test_regressions.py`

## Git Status

- Current branch:
  - `main`
- Current deploy-related commits:
  - `a8587c8 Improve Railway defaults and root env hints`
  - `4aed691 Add Railway environment template`
  - `409c9b4 Prepare BarkBoys for Railway deployment`
- Local working tree is no longer the same as the prior handoff.
- New local changes include reliability and support improvements around OpenAI startup and diagnostics.

## Verification Completed

- `./.venv/bin/python -m py_compile app/main.py app/settings.py app/storage.py app/ai_photo_analysis.py app/quote_output.py tests/test_regressions.py`
- `./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
- Test count at last verification:
  - `24` tests passing
- Hosted app verified in browser:
  - Railway service online
  - generated public domain working
  - `/demo` loads
  - `/staff-estimator` loads
  - hosted AI badge shows `AI Ready: gpt-4.1 connected`
- Local app verified in browser:
  - `/staff-estimator` loads
  - local AI badge shows `AI Ready: gpt-4.1 connected (custom CA bundle)`

## Hosted Config Notes

- Railway service name:
  - `Barkboys-estimator`
- Cloudflare R2 bucket:
  - `barkboys`
- R2 region value used by BarkBoys:
  - `auto`
- Important hosted env variables include:
  - `GS_STORAGE_BACKEND=s3`
  - `GS_UPLOADS_PREFIX=barkboys`
  - `GS_S3_BUCKET=barkboys`
  - `GS_S3_REGION=auto`
  - `GS_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
  - `GS_S3_ACCESS_KEY_ID=...`
  - `GS_S3_SECRET_ACCESS_KEY=...`
  - `GS_API_KEY=...`
  - `GS_ESTIMATOR_USER=...`
  - `GS_ESTIMATOR_PASSWORD=...`
  - `OPENAI_API_KEY=...`

## Remaining Live Checks

- We confirmed hosted page load and AI readiness.
- We have not yet fully documented a successful end-to-end hosted workflow using the live app for:
  - file upload
  - AI measurement parse
  - quote save
  - PDF generation
  - public estimator upload flow

## Recommended Next Check

Run one real hosted workflow on the Railway deployment:

1. Open the live `/staff-estimator`
2. Upload a sample note or site image
3. Click `Parse Measurements`
4. Confirm:
   - AI badge stays green
   - upload succeeds
   - no storage error appears
   - parsed measurement workflow returns results
5. Save a quote
6. Test PDF generation
7. Test `/public-estimator` with one simple upload

## Remaining Risks

- Hosted R2 storage appears configured, but the most important remaining proof is a successful real hosted upload + parse + save cycle.
- Local OpenAI health still depends on a valid key in `backend/.env`; if the key is rotated/revoked, local AI will fall back to the red badge until the key is updated.
- If hosted uploads fail, first check:
  - `GS_S3_BUCKET`
  - `GS_S3_REGION`
  - `GS_S3_ENDPOINT_URL`
  - `GS_S3_ACCESS_KEY_ID`
  - `GS_S3_SECRET_ACCESS_KEY`
- If hosted AI fails, first check:
  - `OPENAI_API_KEY`
  - Railway variable save/deploy state
  - the app’s AI troubleshooting panel
- Earlier exposed OpenAI key should still be treated as compromised and left revoked.

## If Starting Fresh Tomorrow

Local repo:

```bash
cd '/Users/mac/Documents/New project/barkboys'
git status
git log --oneline --decorate -3
```

Local backend run:

```bash
cd '/Users/mac/Documents/New project/barkboys/backend'
./START_BARKBOYS.command
```

If local AI shows `Missing API key` or `Key rejected`:

```bash
cd '/Users/mac/Documents/New project/barkboys/backend'
./UPDATE_OPENAI_KEY.command
./START_LOCAL.command
```

What this does:
- prompts for a replacement OpenAI API key
- writes the key into `backend/.env`
- avoids having to remember shell export commands

Preferred morning routine:

```bash
open -a Terminal '/Users/mac/Documents/New project/barkboys/backend/START_BARKBOYS.command'
```

Admin-only key update routine:

```bash
cd '/Users/mac/Documents/New project/barkboys/backend'
./START_BARKBOYS.command --update-key
```

Hosted app:

- Open the Railway service for `Barkboys-estimator`
- Open the generated Railway public domain
- Test:
  - `/health`
  - `/staff-estimator`
  - `/public-estimator`

## Short Version

- BarkBoys is now live on Railway.
- GitHub, Railway, OpenAI, and Cloudflare R2 are wired together.
- Hosted BarkBoys is loading and showing `AI Ready: gpt-4.1 connected`.
- Local BarkBoys is also currently healthy and has a simpler startup path via `./START_BARKBOYS.command`, with admin-only key rotation separated from normal startup.
- Main remaining work is not deployment anymore; it is one fully successful hosted upload/parse/save/PDF verification pass.

## Update 2026-03-31 16:42 PDT

### What Was Completed Today

- Rebuilt the worksheet state flow locally so Quick Entry is a real source of truth.
- Added a shared `applyMeasurementRows(...)` path in `app/static/estimator.html`.
- Added `parseQuickEntry(text)` with comma + newline support, uppercase/lowercase `x`, spaces around `x`, and invalid-token reporting.
- Draft/browser restore now reapplies restored Quick Entry text instead of only restoring textarea contents.
- Non-image active sources now suppress stale image-failure messaging.
- `Build Material Estimate` now prefers current confirmed measurement rows over stale textbox values.

### Local Verification That Passed

- Targeted regressions passed for:
  - stale failed-image state replaced by Quick Entry
  - browser draft restore reapplies Quick Entry
  - browser draft restore with stale failed-image state
  - build-material path prefers confirmed measurement rows
  - review/material totals for the demo numbers
- Full local regression suite passed:
  - `./.venv/bin/python -m unittest tests.test_regressions`
  - result: `Ran 87 tests ... OK`
- Inline estimator script syntax check passed with `node --check`.

### Exact Local Demo Status After The Fix

Known-good Quick Entry input:

```text
10X15, 12X30, bad line
```

Expected local status text:

```text
Quick Entry loaded 2 rows · 510 sq ft · 3.15 cu yd. 1 invalid line ignored (line 1: bad line).
Measurements detected. Confirm the rows marked Use, then click Build Material Estimate below.
Measurements detected. Review the worksheet before building the quote.
Material estimate built from confirmed measurements for hemlock: requested 3.15 yd, billed at the next BarkBoys table row of 4 yd.
```

### Current Unfinished Problem

- Production behavior is still not proven to match local behavior.
- The live app was reported to still show:
  - Quick Entry text visible
  - image parse failure text still visible
  - Measurement Review at `0 areas`
  - no `Quick Entry loaded ...` status
- That means one of these is still true in production:
  - production is serving an older build
  - `/staff-estimator` is redirecting to `/staff-login` and the wrong HTML was inspected
  - the patched estimator HTML is served, but a runtime JS/init-order issue prevents the Quick Entry reapply path from running

### Production Truth Verified So Far

- Local route wiring is correct:
  - `/staff-estimator` is served by `app.main.staff_estimator_page`
  - it renders `_render_staff_estimator_page()`
  - that reads `backend/app/static/estimator.html`
- The estimator script is inline in `app/static/estimator.html`, not an external JS bundle.
- Root Railway deploy config points to the repo-root Dockerfile:
  - `barkboys/railway.toml`
  - `barkboys/Dockerfile`
- Root Dockerfile copies `backend/` into `/app`, so the deployed image should contain `backend/app/static/estimator.html`.

### What Blocked Final Production Verification Today

- This Mac again could not resolve the Railway host from the shell:
  - `curl: (6) Could not resolve host: barkboys-estimator-production.up.railway.app`
- Because of that, shell-side production HTML inspection was not completed.
- The earlier production diagnosis turn was also interrupted before finishing the live response inspection.

### Important Current Repo State

Local git status at stop time:

- modified: `backend/app/static/estimator.html`
- modified: `backend/tests/test_regressions.py`

These worksheet-source-of-truth fixes are local and tested, but not yet proven deployed live.

### First Steps Tomorrow

1. Verify what production `/staff-estimator` actually returns:
   - `curl -I -L https://barkboys-estimator-production.up.railway.app/staff-estimator`
   - inspect whether it is redirecting to `/staff-login`
   - inspect whether returned HTML contains:
     - `BarkBoys Internal Sales Worksheet`
     - `parseQuickEntry`
     - `applyMeasurementRows`
     - `Quick Entry loaded`
2. If production HTML does not include the patched code:
   - push current local changes
   - redeploy Railway
   - verify build stamp changes
3. If production HTML does include the patched code:
   - diagnose the first runtime JS/state-init failure in the live page
   - fix only that failure
4. Re-test the live demo path with:

```text
10X15, 12X30, bad line
```

Required production result:

- 2 rows loaded
- 510 sq ft
- about 3.15 cu yd
- no image-failure text
- Measurement Review shows 2 areas
- materials reflect current rows
- Review and Save no longer stays at `$0.00` after estimate build
