# Codex Map

## Purpose

Communication-first CRM v1 for staff to manage contacts, shared SMS, calls, notes, tasks, quotes, service sites, and one unified contact activity timeline.

## Stack

- Backend: Express + TypeScript
- Database: Postgres
- API style: REST
- Frontend: static HTML/CSS/vanilla JS in `public/`
- Runtime entry: `src/server.ts`

FastAPI is not part of v1. Migration can be discussed later, but do not let it block workflow work.

## Main Folders

- `src/routes/`: REST route handlers and request validation
- `src/services/`: business logic and provider abstractions
- `src/lib/`: shared helpers like DB and normalization
- `db/schema.sql`: relational schema
- `scripts/`: local setup and smoke-test utilities
- `public/`: browser app
- `docs/`: orientation, task list, testing checklist, decisions

## Key Routes

- CRM: `/contacts`, `/contacts/:id`, `/contacts/:id/timeline`, `/contacts/:id/sites`, `/tasks`
- Duplicate lookup: `/contacts/duplicates/search`
- Inbox: `/conversations`, `/conversations/:id/messages`
- Webhooks: `/webhooks/sms/*`, `/webhooks/:provider/sms/*`, `/webhooks/calls/*`, `/webhooks/:provider/calls/*`
- Calls: `/calls`, `/calls/outbound`, `/calls/:id/disposition`
- Quotes: `/quotes`, `/quotes/:id/versions`, `/quotes/:id/send-sms`, `/quotes/:id/send-email`, `/quotes/:id/accept`, `/quotes/:id/decline`
- Admin: `/settings/templates`, `/settings/phone-routing`, `/settings/integration-settings`
- Dev seed: `/api/dev/seed-demo`

All primary browser routes also have `/api/...` aliases.

## Key Services

- `contact_service`: contacts, lead shells, service sites, notes
- `duplicate_service`: phone/name/address duplicate lookup
- `conversation_service`: shared inbox threads
- `message_service`: provider-neutral SMS persistence and timeline activity
- `message_provider` + `sms_provider_registry`: provider adapter contract and placeholders
- `integration_service`: active provider selection, provider normalization, settings
- `call_service`: call logging, status, dispositions
- `quote_service`: quote creation, versions, send/accept/decline lifecycle
- `activity_service`: unified contact timeline
- `task_service`: follow-up tasks

## Database Setup

Local `.env` should contain:

```bash
DATABASE_URL=postgres://mac@localhost:5432/communication_first_crm
ALLOW_DEMO_SEED=true
HOST=127.0.0.1
PORT=3000
```

Run:

```bash
npm run db:setup
npm run db:seed
```

## Run Locally

```bash
npm install
npm run db:setup
npm run db:seed
npm run dev:run
```

Open `http://127.0.0.1:3000/`.

## Test

```bash
npm run build
npm run test:workflow
```

`test:workflow` expects the app to be running on `http://127.0.0.1:3000`.

## Current Constraints

- Auth is scaffold-only and not production secure.
- Provider integrations are adapter placeholders; do not store real secrets in code.
- Keep Express/TypeScript v1 stable. Do not migrate frameworks during workflow work.
- Do not add features while doing test-only passes.
- Prefer small diffs and task-local file reads.
