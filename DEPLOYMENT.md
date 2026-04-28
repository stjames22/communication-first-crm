# Communication First CRM Deployment

The app is a FastAPI service with a simple CRM workspace at:

```text
/crm/workspace
```

## Local Verification

```bash
npm install
npm run build
npm test
PORT=4174 npm start
curl -fsS http://127.0.0.1:4174/api/health
```

## Runtime

- The server reads `PORT` from the environment.
- SQLite is used by default for local development.
- `/api/health` returns the service health payload.
- `POST /crm/api/dev/seed-demo` loads generic service-business CRM demo data.

## Product Smoke Test

1. Open `/crm/workspace`.
2. Load demo data.
3. Open a recent conversation.
4. Add a note.
5. Send a reply.
6. Start a quote/proposal handoff.
7. Confirm the handoff keeps the selected `contact_id`.
