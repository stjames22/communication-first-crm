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
- `POST /api/twilio/sms/inbound`
- `POST /api/twilio/sms/send`
- `GET /api/twilio/sms/status`
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
- `POST /api/wp/lead`
- `POST /api/wp/traffic-event`
- `POST /api/wp/easy-link-click`
- `GET /api/wp/dashboard-summary`

## WordPress Sync

The WordPress plugin in `wordpress-plugin/northwestern-traffic-crm` can sync website lead forms, Easy Link clicks, and optional visit events into this CRM.

1. Set `GS_API_KEY` on the CRM server.
2. Install the WordPress plugin ZIP.
3. In WordPress Admin, open `Traffic CRM -> Settings`.
4. Enable `Sync leads and Easy Links to CRM API`.
5. Enter the CRM base URL, such as `https://crm.example.com`.
6. Enter the same API key used in `GS_API_KEY`.

Lead submissions become CRM contacts/messages. Easy Link clicks and traffic events are stored as website events for dashboard reporting.

## Twilio SMS

Set these environment variables for live SMS:

```bash
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=+15551234567
TWILIO_VALIDATE_SIGNATURES=true
COMMUNICATION_DEMO_MODE=false
OPENAI_API_KEY=optional
```

Twilio Console webhook for inbound messages:

```text
https://YOUR_PUBLIC_APP_URL/api/twilio/sms/inbound
```

Local simulator, no public webhook required:

```bash
python scripts/simulate_twilio_sms.py --from +15035550123 --body "Hi, I need help with a quote"
```

When Twilio credentials are missing, outbound replies are stored in demo mode with a warning instead of sending a real SMS.

## Notes

The repository still contains some legacy backend endpoints for compatibility with older tests and saved data. The current product direction is the communication CRM workspace at `/crm/workspace`.
