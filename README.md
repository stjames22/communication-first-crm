# Communication First CRM

FastAPI app for a communication-first CRM used by service businesses.

The main product is the CRM workspace:

- Recent conversations
- Contacts
- Contact timeline
- Inbound message intake
- Replies
- Internal notes
- Follow-ups
- Quote/proposal handoff

## Run Locally

```bash
npm install
npm run build
npm test
PORT=4174 npm start
```

Open:

```text
http://127.0.0.1:4174/crm/workspace
```

Health check:

```bash
curl -fsS http://127.0.0.1:4174/api/health
```

## Demo Flow

1. Open `/crm/workspace`.
2. Click `Load Demo Data`.
3. Select a recent conversation.
4. Review the contact timeline.
5. Add a note.
6. Reply to the conversation.
7. Start a quote/proposal handoff and confirm `contact_id` is preserved.

## CRM API

- `POST /api/inbound-message`
- `GET /api/conversations/recent`
- `GET /api/conversations/{contact_id}`
- `POST /api/reply`
- `GET /api/contacts/{contact_id}/timeline`
- `POST /api/contacts/{contact_id}/start-quote`
- `GET /crm/api/dashboard`
- `GET /crm/api/contacts`
- `GET /crm/api/conversations`
- `POST /crm/api/contacts/{contact_id}/notes`
- `POST /crm/api/dev/seed-demo`

## Notes

The repository still contains some legacy backend endpoints for compatibility with older tests and saved data. The current product direction is the communication CRM workspace at `/crm/workspace`.
