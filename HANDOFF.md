# Handoff

## Current Status

Demo-ready v1 scaffold is running on Express + TypeScript + Postgres. Local Postgres 16 is installed via Homebrew, `.env` points to `communication_first_crm`, schema setup runs, and sample data can be seeded.

## What Works

- App starts at `http://127.0.0.1:3000/`.
- `/health` returns `database: "ok"` when Postgres is running.
- Schema setup works with `npm run db:setup`.
- Demo data seeds with `npm run db:seed`.
- Shared inbox, contacts, calls, quotes, admin surfaces are wired to REST APIs.
- Provider abstraction placeholders exist for Twilio, RingCentral, Telnyx, and generic webhook providers.
- Duplicate lookup endpoint and UI warning flow are implemented.

## Current Blockers

- Auth is scaffold-only and not production secure.
- There is no full automated test suite yet.
- Provider integrations are placeholders and need real credentials/webhook validation before production use.

## Next Recommended Step

Run `npm run test:workflow`, then manually walk `docs/TESTING_CHECKLIST.md` against seeded data and log only true blockers in `docs/TASKS.md`.

## Last Verified Command

```bash
npm run build
npm run test:workflow
```
