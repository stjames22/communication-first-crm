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

Communication Hub is CRM-agnostic. The hub owns conversations, messages, phone/SMS/email/call events, front-desk summaries, and the activity timeline. CRM-specific customer, quote, job, and order behavior lives behind an adapter.

Set the adapter:

```bash
COMMUNICATION_CRM_ADAPTER=local
```

Supported values:

- `local`: default lightweight CRM scaffold included in this repo.
- `barkboys`: first reference adapter scaffold for BarkBoys-style customer, site, quote, and job concepts.
- `external`: placeholder for a customer-owned CRM integration. Implement the adapter before enabling it.

Twilio, RingCentral, and future communication providers should plug into Communication Hub endpoints, not directly into a CRM. The hub then asks the configured adapter to find/create/link customer context.

Set these environment variables for live SMS:

```bash
COMMUNICATION_CRM_ADAPTER=local
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

## CRM Adapter Layer

Adapters implement this pattern:

- `find_contact(phone=None, email=None, name=None)`
- `create_contact(payload)`
- `update_contact(contact_id, payload)`
- `get_contact_context(contact_id)`
- `link_conversation(contact_id, conversation_id)`
- `create_followup(contact_id, payload)`
- `create_quote_or_job(contact_id, payload)` optional
- `get_quote_or_job_context(contact_id)` optional

Files:

- `modules/communication_crm/crm_adapters.py`: adapter interface plus `LocalCRMAdapter`, `BarkBoysCRMAdapter`, and `ExternalCRMAdapter`.
- `modules/communication_crm/twilio_service.py`: Twilio/RingCentral-style communication flow feeds the hub, then the hub uses the adapter to resolve customer context.
- `modules/communication_crm/crm_service.py`: core conversation/message/timeline persistence.

BarkBoys is the first reference integration. The scaffold currently maps communication contacts to local CRM contact/site/quote concepts and includes TODOs for full quote/site/job integration.

## Notes

The repository still contains some legacy backend endpoints for compatibility with older tests and saved data. The current product direction is the communication CRM workspace at `/crm/workspace`.
