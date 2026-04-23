# Testing Checklist

Use this for repeatable demo-ready CRM checks.

## Setup

- `npm run db:setup`
- `npm run db:seed`
- `npm run dev:run`
- `npm run build`
- `npm run test:workflow`

## Contact Creation

- Create contact with name, phone, email.
- Add service site.
- Enter matching phone/name/address and confirm duplicate warning.
- Use `Open Existing Contact`.
- Use `Continue Anyway` and confirm timeline note is created.

## Shared Inbox

- Open seeded conversation.
- Send outbound test message.
- Confirm message appears in thread.
- Confirm message appears in contact timeline.

## Provider Abstraction

- Keep mock/generic provider active for local testing.
- Send outbound SMS through `/conversations/:id/messages`.
- Post inbound provider webhook payload.
- Confirm provider IDs stay separate from CRM IDs.
- Confirm delivery status updates internal message record.

## Quote Workflow

- Create quote tied to contact and service site.
- Save draft.
- Save new version.
- Open PDF hook.
- Send SMS/email.
- Mark accepted and declined on separate test quotes.
- Confirm each quote event appears on the activity timeline.

## Calls

- Post inbound missed call webhook.
- Add disposition.
- Confirm call and disposition appear on timeline.
- Create follow-up task.

## Dashboard

- Verify unread texts.
- Verify missed calls.
- Verify tasks due today.
- Verify quotes awaiting follow-up.

## Regression

- Browser console has no errors.
- API routes return expected status.
- No accidental duplicate contacts from strong duplicate matches.
