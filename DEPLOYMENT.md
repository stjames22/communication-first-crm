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
- `COMMUNICATION_DEMO_MODE=true` keeps local demo seeding available. Set it to `false` for live deployments.
- `GS_API_KEY` protects the CRM, Lead Monitor, and WordPress sync APIs when set.

## Twilio SMS Runtime

Configure:

```bash
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=+15551234567
TWILIO_VALIDATE_SIGNATURES=true
COMMUNICATION_DEMO_MODE=false
OPENAI_API_KEY=optional
```

Endpoints:

- Inbound Twilio webhook: `POST /api/twilio/sms/inbound`
- Outbound SMS from the workspace: `POST /api/twilio/sms/send`
- Runtime status: `GET /api/twilio/sms/status`

Paste this into the Twilio Console Messaging webhook field:

```text
https://YOUR_PUBLIC_APP_URL/api/twilio/sms/inbound
```

Local inbound simulator:

```bash
python scripts/simulate_twilio_sms.py --from +15035550123 --body "Hi, I need help with a quote"
```

## WordPress Sync

Install `wordpress-plugin/northwestern-traffic-crm.zip` in WordPress, then configure:

- CRM base URL: the deployed FastAPI app origin.
- CRM API key: the same value as `GS_API_KEY`.
- Sync leads and Easy Links: enabled.
- Sync page visit events: optional, useful during pilots but higher-volume.

The active sync endpoints are:

- `POST /api/wp/lead`
- `POST /api/wp/traffic-event`
- `POST /api/wp/easy-link-click`
- `GET /api/wp/dashboard-summary`

## Product Smoke Test

1. Open `/crm/workspace`.
2. Load demo data.
3. Open a recent conversation.
4. Add a note.
5. Send a reply.
6. Start a quote/proposal handoff.
7. Confirm the handoff keeps the selected `contact_id`.
