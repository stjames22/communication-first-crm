import { Router } from "express";
import { pool } from "../lib/db";

export const devRouter = Router();

devRouter.post("/seed-demo", async (_req, res, next) => {
  if (process.env.NODE_ENV === "production" && process.env.ALLOW_DEMO_SEED !== "true") {
    return res.status(404).json({ error: "Not found" });
  }

  let client;

  try {
    client = await pool.connect();
    await client.query("BEGIN");
    await client.query(
      `TRUNCATE
        external_references,
        attachments,
        quote_line_items,
        quote_versions,
        quotes,
        messages,
        calls,
        conversations,
        tasks,
        activities,
        service_sites,
        contact_tags,
        tags,
        contacts,
        accounts,
        users,
        message_templates,
        phone_routing_settings,
        quote_defaults,
        integration_settings
       RESTART IDENTITY CASCADE`
    );

    const admin = (
      await client.query(
        `INSERT INTO users (full_name, email, role)
         VALUES ('Jamie Stone', 'jamie@example.com', 'admin')
         RETURNING *`
      )
    ).rows[0];

    const staff = (
      await client.query(
        `INSERT INTO users (full_name, email, role)
         VALUES ('Morgan Lee', 'morgan@example.com', 'sales')
         RETURNING *`
      )
    ).rows[0];

    const tagRows = (
      await client.query(
        `INSERT INTO tags (name, color)
         VALUES
           ('New Lead', '#2563eb'),
           ('Quote Sent', '#7c3aed'),
           ('Needs Follow-Up', '#d97706'),
           ('Accepted', '#059669')
         RETURNING *`
      )
    ).rows;

    const account = (
      await client.query(
        `INSERT INTO accounts (company_name, billing_address, notes)
         VALUES ('Demo Accounts', '100 Example Ave, Sample City, ST 10001', 'Generic demo customers for workflow testing.')
         RETURNING *`
      )
    ).rows[0];

    const contacts = (
      await client.query(
        `INSERT INTO contacts
         (account_id, first_name, last_name, display_name, mobile_phone, email, preferred_contact_method, status, source, assigned_user_id)
         VALUES
           ($1, 'Jordan', 'Lee', 'Jordan Lee', '+15035550141', 'jordan@example.com', 'sms', 'lead', 'website', $2),
           ($1, 'Taylor', 'Morgan', 'Taylor Morgan', '+15035550142', 'taylor@example.com', 'sms', 'quoted', 'referral', $2),
           ($1, 'Casey', 'Rivera', 'Casey Rivera', '+15035550143', 'casey@example.com', 'phone', 'accepted', 'repeat_customer', $3)
         RETURNING *`,
        [account.id, staff.id, admin.id]
      )
    ).rows;

    const [jordan, taylor, casey] = contacts;
    const newLeadTag = tagRows.find((tag) => tag.name === "New Lead");
    const quoteSentTag = tagRows.find((tag) => tag.name === "Quote Sent");
    const followUpTag = tagRows.find((tag) => tag.name === "Needs Follow-Up");
    const acceptedTag = tagRows.find((tag) => tag.name === "Accepted");

    if (!newLeadTag || !quoteSentTag || !followUpTag || !acceptedTag) {
      throw new Error("Could not prepare demo tags.");
    }

    await client.query(
      `INSERT INTO contact_tags (contact_id, tag_id)
       VALUES ($1, $4), ($2, $5), ($2, $6), ($3, $7)`,
      [jordan.id, taylor.id, casey.id, newLeadTag.id, quoteSentTag.id, followUpTag.id, acceptedTag.id]
    );

    const sites = (
      await client.query(
        `INSERT INTO service_sites
         (contact_id, label, address_line_1, city, state, zip, delivery_zone, site_notes)
         VALUES
           ($1, 'Primary Site', '100 Example Ave', 'Sample City', 'ST', '10001', 'Standard', 'Prefers morning communication.'),
           ($2, 'Project Site', '200 Market St', 'Sample City', 'ST', '10002', 'Priority', 'Access notes on file.'),
           ($3, 'Secondary Site', '300 Oak Dr', 'Sample City', 'ST', '10003', 'Standard', 'Accepted implementation proposal.')
         RETURNING *`,
        [jordan.id, taylor.id, casey.id]
      )
    ).rows;

    const conversations = (
      await client.query(
        `INSERT INTO conversations
         (contact_id, assigned_user_id, channel_type, status, last_message_at, unread_count)
         VALUES
          ($1, $4, 'sms', 'open', NOW() - INTERVAL '12 minutes', 1),
           ($2, $4, 'sms', 'open', NOW() - INTERVAL '2 hours', 0),
           ($3, $5, 'sms', 'open', NOW() - INTERVAL '1 day', 0)
         RETURNING *`,
        [jordan.id, taylor.id, casey.id, staff.id, admin.id]
      )
    ).rows;

    await client.query(
      `INSERT INTO messages
       (conversation_id, contact_id, direction, channel, provider_message_id, body, delivery_status, created_at, sent_by_user_id)
       VALUES
         ($1, $4, 'inbound', 'sms', 'demo-jordan-1', 'Hi, can you send a proposal for the standard service package?', 'received', NOW() - INTERVAL '34 minutes', NULL),
         ($1, $4, 'outbound', 'sms', 'demo-jordan-2', 'Absolutely. I can build that from your site notes and send a proposal here.', 'sent', NOW() - INTERVAL '20 minutes', $7),
         ($1, $4, 'inbound', 'sms', 'demo-jordan-3', 'Great. Please include delivery timing and an implementation option.', 'received', NOW() - INTERVAL '12 minutes', NULL),
         ($2, $5, 'outbound', 'sms', 'demo-taylor-1', 'Your proposal QTE-2026-0001 is ready. Want me to adjust the timing?', 'sent', NOW() - INTERVAL '2 hours', $7),
         ($3, $6, 'outbound', 'sms', 'demo-casey-1', 'Thanks for accepting. We will confirm the next step shortly.', 'sent', NOW() - INTERVAL '1 day', $8)`,
      [conversations[0].id, conversations[1].id, conversations[2].id, jordan.id, taylor.id, casey.id, staff.id, admin.id]
    );

    await client.query(
      `INSERT INTO calls
       (contact_id, conversation_id, provider_call_id, direction, status, from_number, to_number, started_at, duration_seconds, assigned_user_id, disposition, notes)
       VALUES
         ($1, $3, 'demo-call-jordan-1', 'inbound', 'missed', $4, '+15035550000', NOW() - INTERVAL '45 minutes', 0, $6, NULL, NULL),
         ($2, $5, 'demo-call-taylor-1', 'outbound', 'completed', 'staff', $7, NOW() - INTERVAL '3 hours', 246, $6, 'left_voicemail', 'Left voicemail about proposal options.')`,
      [jordan.id, taylor.id, conversations[0].id, jordan.mobile_phone, conversations[1].id, staff.id, taylor.mobile_phone]
    );

    const taylorQuote = (
      await client.query(
        `INSERT INTO quotes
         (contact_id, service_site_id, quote_number, title, status, subtotal, delivery_total, tax_total, grand_total, sent_at, created_by_user_id)
         VALUES ($1, $2, 'QTE-2026-0001', 'Standard service proposal', 'sent', 1845.00, 95.00, 0.00, 1940.00, NOW() - INTERVAL '2 hours', $3)
         RETURNING *`,
        [taylor.id, sites[1].id, staff.id]
      )
    ).rows[0];

    const taylorVersion = (
      await client.query(
        `INSERT INTO quote_versions
         (quote_id, version_number, pricing_snapshot_json, notes, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, 1, $2::jsonb, 'Initial demo quote with manual delivery override.', 1845.00, 95.00, 0.00, 1940.00, $3)
         RETURNING *`,
        [
          taylorQuote.id,
          JSON.stringify({ manualOverrides: { delivery_total: 95 }, source: "demo-example" }),
          staff.id
        ]
      )
    ).rows[0];

    await client.query(
      `INSERT INTO quote_line_items
       (quote_version_id, item_type, name, description, quantity, unit, unit_price, total_price, sort_order, source_reference)
       VALUES
         ($1, 'service', 'Core service package', 'Primary service package for the requested scope.', 8, 'unit', 145.00, 1160.00, 1, 'core service'),
         ($1, 'service', 'Setup and coordination', 'Planning, setup, and coordination work.', 1, 'project', 485.00, 485.00, 2, 'setup service'),
         ($1, 'adjustment', 'Manual access adjustment', 'Additional handling for the requested site.', 1, 'each', 200.00, 200.00, 3, 'manual override')`,
      [taylorVersion.id]
    );

    await client.query("UPDATE quotes SET current_version_id = $2 WHERE id = $1", [taylorQuote.id, taylorVersion.id]);

    const caseyQuote = (
      await client.query(
        `INSERT INTO quotes
         (contact_id, service_site_id, quote_number, title, status, current_version_id, subtotal, delivery_total, tax_total, grand_total, sent_at, accepted_at, created_by_user_id)
         VALUES ($1, $2, 'QTE-2026-0002', 'Accepted implementation proposal', 'accepted', NULL, 980.00, 75.00, 0.00, 1055.00, NOW() - INTERVAL '3 days', NOW() - INTERVAL '1 day', $3)
         RETURNING *`,
        [casey.id, sites[2].id, admin.id]
      )
    ).rows[0];

    const caseyVersion = (
      await client.query(
        `INSERT INTO quote_versions
         (quote_id, version_number, pricing_snapshot_json, notes, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, 1, '{}'::jsonb, 'Accepted implementation package.', 980.00, 75.00, 0.00, 1055.00, $2)
         RETURNING *`,
        [caseyQuote.id, admin.id]
      )
    ).rows[0];

    await client.query(
      `INSERT INTO quote_line_items
       (quote_version_id, item_type, name, description, quantity, unit, unit_price, total_price, sort_order)
       VALUES
         ($1, 'service', 'Recurring service package', 'Accepted service package.', 5, 'unit', 160.00, 800.00, 1),
         ($1, 'service', 'Implementation add-on', 'Additional setup and coordination.', 1, 'project', 180.00, 180.00, 2)`,
      [caseyVersion.id]
    );

    await client.query("UPDATE quotes SET current_version_id = $2 WHERE id = $1", [caseyQuote.id, caseyVersion.id]);

    await client.query(
      `INSERT INTO tasks (contact_id, assigned_user_id, related_type, related_id, title, due_at, status, priority)
       VALUES
         ($1, $5, NULL, NULL, 'Reply with proposal draft including implementation option', NOW() + INTERVAL '1 hour', 'open', 'high'),
         ($2, $5, 'quote', $4, 'Follow up on quote/proposal', NOW() + INTERVAL '1 day', 'open', 'normal'),
         ($3, $6, 'quote', $7, 'Confirm accepted quote schedule', NOW() + INTERVAL '2 days', 'open', 'normal')`,
      [jordan.id, taylor.id, casey.id, taylorQuote.id, staff.id, admin.id, caseyQuote.id]
    );

    await client.query(
      `INSERT INTO activities (contact_id, related_type, related_id, activity_type, title, body, actor_user_id, metadata_json, created_at)
       VALUES
         ($1, 'message', NULL, 'message.inbound', 'Inbound text received', 'Great. Please include delivery timing and an implementation option.', NULL, '{}'::jsonb, NOW() - INTERVAL '12 minutes'),
         ($1, 'call', NULL, 'call.missed', 'Missed call', 'Missed inbound call from Jordan Lee.', $4, '{}'::jsonb, NOW() - INTERVAL '45 minutes'),
         ($1, 'task', NULL, 'task.created', 'Task created', 'Reply with proposal draft including implementation option', $4, '{}'::jsonb, NOW() - INTERVAL '10 minutes'),
         ($2, 'quote', $6, 'quote_created', 'Quote/proposal created', 'QTE-2026-0001 was created for Standard service proposal.', $4, '{"version":1}'::jsonb, NOW() - INTERVAL '3 hours'),
         ($2, 'quote', $6, 'quote_sent', 'Quote/proposal sent', 'QTE-2026-0001 was sent by text.', $4, '{}'::jsonb, NOW() - INTERVAL '2 hours'),
         ($2, 'call', NULL, 'call.disposition', 'Call disposition saved', 'Left voicemail about proposal options.', $4, '{"disposition":"left_voicemail"}'::jsonb, NOW() - INTERVAL '3 hours'),
         ($3, 'quote', $7, 'quote_accepted', 'Quote/proposal accepted', 'QTE-2026-0002 was marked accepted.', $5, '{}'::jsonb, NOW() - INTERVAL '1 day'),
         ($3, 'message', NULL, 'message.outbound', 'Outbound text sent', 'Thanks for accepting. We will confirm timing next.', $5, '{}'::jsonb, NOW() - INTERVAL '1 day')`,
      [jordan.id, taylor.id, casey.id, staff.id, admin.id, taylorQuote.id, caseyQuote.id]
    );

    await client.query(
      `INSERT INTO message_templates (name, channel, body)
       VALUES
         ('Quote ready', 'sms', 'Your quote is ready. Want me to send it here?'),
         ('Missed call', 'sms', 'Sorry we missed you. What is the best time to call back?'),
         ('Follow-up', 'sms', 'Quick follow-up on your quote. Would you like any changes?')`
    );

    await client.query(
      `INSERT INTO phone_routing_settings (label, inbound_number, destination_type, destination_value)
       VALUES ('Main line', '+15035550000', 'queue', 'sales')`
    );

    await client.query(
      `INSERT INTO quote_defaults (label, tax_rate, default_delivery_total, terms)
       VALUES ('Default delivery', 0, 95.00, 'Quote valid for 14 days. Delivery may vary by zone.')`
    );

    await client.query(
      `INSERT INTO integration_settings (provider_type, provider_name, enabled, settings_json)
       VALUES
         ('sms', 'twilio', FALSE, '{"todo":"Add credentials and webhook validation"}'::jsonb),
         ('voice', 'ringcentral', FALSE, '{"todo":"Add OAuth and routing setup"}'::jsonb)`
    );

    await client.query(
      `INSERT INTO external_references
       (internal_type, internal_id, external_system, external_type, external_id, metadata_json)
       VALUES
         ('quote', $1, 'legacy-system', 'estimate', 'EXT-DEMO-EST-0001', $2::jsonb)`,
      [
        taylorQuote.id,
        JSON.stringify({
          note: "Example bridge for a future external estimator quote.",
          quoteNumber: taylorQuote.quote_number
        })
      ]
    );

    await client.query("COMMIT");
    res.json({ ok: true, contacts: contacts.length, quotes: 2 });
  } catch (error) {
    if (client) {
      await client.query("ROLLBACK");
    }
    next(error);
  } finally {
    client?.release();
  }
});
