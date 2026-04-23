import { Router } from "express";
import { pool } from "../lib/db";

export const devRouter = Router();

devRouter.post("/seed-demo", async (_req, res, next) => {
  if (process.env.NODE_ENV === "production" && process.env.ALLOW_DEMO_SEED !== "true") {
    return res.status(404).json({ error: "Not found" });
  }

  const client = await pool.connect();

  try {
    await client.query("BEGIN");
    await client.query(
      `TRUNCATE
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
         VALUES ('Jamie Stone', 'jamie@barkboys.example', 'admin')
         RETURNING *`
      )
    ).rows[0];

    const staff = (
      await client.query(
        `INSERT INTO users (full_name, email, role)
         VALUES ('Morgan Lee', 'morgan@barkboys.example', 'sales')
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
         VALUES ('Barkboys Demo Accounts', '148 Cedar Run, Portland, OR 97205', 'Commercial and residential demo customers.')
         RETURNING *`
      )
    ).rows[0];

    const contacts = (
      await client.query(
        `INSERT INTO contacts
         (account_id, first_name, last_name, display_name, mobile_phone, email, preferred_contact_method, status, source, assigned_user_id)
         VALUES
           ($1, 'Kyle', 'Bennett', 'Kyle Bennett', '+15035550141', 'kyle@example.com', 'sms', 'lead', 'website', $2),
           ($1, 'Avery', 'Cole', 'Avery Cole', '+15035550142', 'avery@example.com', 'sms', 'quoted', 'referral', $2),
           ($1, 'Riley', 'Sullivan', 'Riley Sullivan', '+15035550143', 'riley@example.com', 'phone', 'accepted', 'repeat_customer', $3)
         RETURNING *`,
        [account.id, staff.id, admin.id]
      )
    ).rows;

    const [kyle, avery, riley] = contacts;
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
      [kyle.id, avery.id, riley.id, newLeadTag.id, quoteSentTag.id, followUpTag.id, acceptedTag.id]
    );

    const sites = (
      await client.query(
        `INSERT INTO service_sites
         (contact_id, label, address_line_1, city, state, zip, delivery_zone, site_notes)
         VALUES
           ($1, 'Home', '2217 SE Alder St', 'Portland', 'OR', '97214', 'Central', 'Street parking, prefers morning delivery.'),
           ($2, 'Backyard Project', '6110 N Omaha Ave', 'Portland', 'OR', '97217', 'North', 'Gate code on file.'),
           ($3, 'Primary Residence', '932 SW Maple Dr', 'Beaverton', 'OR', '97005', 'West', 'Accepted spring install quote.')
         RETURNING *`,
        [kyle.id, avery.id, riley.id]
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
        [kyle.id, avery.id, riley.id, staff.id, admin.id]
      )
    ).rows;

    await client.query(
      `INSERT INTO messages
       (conversation_id, contact_id, direction, channel, provider_message_id, body, delivery_status, created_at, sent_by_user_id)
       VALUES
         ($1, $4, 'inbound', 'sms', 'demo-kyle-1', 'Hi, can Barkboys quote mulch and cleanup for my front beds?', 'received', NOW() - INTERVAL '34 minutes', NULL),
         ($1, $4, 'outbound', 'sms', 'demo-kyle-2', 'Absolutely. I can build that from your site notes and send a quote here.', 'sent', NOW() - INTERVAL '20 minutes', $7),
         ($1, $4, 'inbound', 'sms', 'demo-kyle-3', 'Great. Please include delivery and a simple edging option.', 'received', NOW() - INTERVAL '12 minutes', NULL),
         ($2, $5, 'outbound', 'sms', 'demo-avery-1', 'Your Barkboys quote BBQ-2026-0001 is ready. Want me to adjust the delivery window?', 'sent', NOW() - INTERVAL '2 hours', $7),
         ($3, $6, 'outbound', 'sms', 'demo-riley-1', 'Thanks for accepting. We will confirm install timing next.', 'sent', NOW() - INTERVAL '1 day', $8)`,
      [conversations[0].id, conversations[1].id, conversations[2].id, kyle.id, avery.id, riley.id, staff.id, admin.id]
    );

    await client.query(
      `INSERT INTO calls
       (contact_id, conversation_id, provider_call_id, direction, status, from_number, to_number, started_at, duration_seconds, assigned_user_id, disposition, notes)
       VALUES
         ($1, $3, 'demo-call-kyle-1', 'inbound', 'missed', $4, '+15035550000', NOW() - INTERVAL '45 minutes', 0, $6, NULL, NULL),
         ($2, $5, 'demo-call-avery-1', 'outbound', 'completed', 'staff', $7, NOW() - INTERVAL '3 hours', 246, $6, 'left_voicemail', 'Left voicemail about delivery options.')`,
      [kyle.id, avery.id, conversations[0].id, kyle.mobile_phone, conversations[1].id, staff.id, avery.mobile_phone]
    );

    const averyQuote = (
      await client.query(
        `INSERT INTO quotes
         (contact_id, service_site_id, quote_number, title, status, subtotal, delivery_total, tax_total, grand_total, sent_at, created_by_user_id)
         VALUES ($1, $2, 'BBQ-2026-0001', 'Barkboys backyard refresh', 'sent', 1845.00, 95.00, 0.00, 1940.00, NOW() - INTERVAL '2 hours', $3)
         RETURNING *`,
        [avery.id, sites[1].id, staff.id]
      )
    ).rows[0];

    const averyVersion = (
      await client.query(
        `INSERT INTO quote_versions
         (quote_id, version_number, pricing_snapshot_json, notes, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, 1, $2::jsonb, 'Initial Barkboys demo quote with manual delivery override.', 1845.00, 95.00, 0.00, 1940.00, $3)
         RETURNING *`,
        [
          averyQuote.id,
          JSON.stringify({ manualOverrides: { delivery_total: 95 }, source: "barkboys-example" }),
          staff.id
        ]
      )
    ).rows[0];

    await client.query(
      `INSERT INTO quote_line_items
       (quote_version_id, item_type, name, description, quantity, unit, unit_price, total_price, sort_order, source_reference)
       VALUES
         ($1, 'service', 'Mulch installation', 'Premium bark mulch install across backyard beds.', 8, 'yard', 145.00, 1160.00, 1, 'barkboys mulch'),
         ($1, 'service', 'Bed cleanup', 'Debris removal, shaping, and light weed removal.', 1, 'project', 485.00, 485.00, 2, 'barkboys cleanup'),
         ($1, 'adjustment', 'Manual slope adjustment', 'Extra handling for side-yard access.', 1, 'each', 200.00, 200.00, 3, 'manual override')`,
      [averyVersion.id]
    );

    await client.query("UPDATE quotes SET current_version_id = $2 WHERE id = $1", [averyQuote.id, averyVersion.id]);

    const rileyQuote = (
      await client.query(
        `INSERT INTO quotes
         (contact_id, service_site_id, quote_number, title, status, current_version_id, subtotal, delivery_total, tax_total, grand_total, sent_at, accepted_at, created_by_user_id)
         VALUES ($1, $2, 'BBQ-2026-0002', 'Spring refresh accepted quote', 'accepted', NULL, 980.00, 75.00, 0.00, 1055.00, NOW() - INTERVAL '3 days', NOW() - INTERVAL '1 day', $3)
         RETURNING *`,
        [riley.id, sites[2].id, admin.id]
      )
    ).rows[0];

    const rileyVersion = (
      await client.query(
        `INSERT INTO quote_versions
         (quote_id, version_number, pricing_snapshot_json, notes, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, 1, '{}'::jsonb, 'Accepted spring install package.', 980.00, 75.00, 0.00, 1055.00, $2)
         RETURNING *`,
        [rileyQuote.id, admin.id]
      )
    ).rows[0];

    await client.query(
      `INSERT INTO quote_line_items
       (quote_version_id, item_type, name, description, quantity, unit, unit_price, total_price, sort_order)
       VALUES
         ($1, 'service', 'Bark mulch refresh', 'Front and side bed refresh.', 5, 'yard', 160.00, 800.00, 1),
         ($1, 'service', 'Cleanup add-on', 'Final cleanup and haul-away.', 1, 'project', 180.00, 180.00, 2)`,
      [rileyVersion.id]
    );

    await client.query("UPDATE quotes SET current_version_id = $2 WHERE id = $1", [rileyQuote.id, rileyVersion.id]);

    await client.query(
      `INSERT INTO tasks (contact_id, assigned_user_id, title, due_at, status, priority)
       VALUES
         ($1, $4, 'Reply with quote draft including edging option', NOW() + INTERVAL '1 hour', 'open', 'high'),
         ($2, $4, 'Follow up on sent Barkboys quote', NOW() + INTERVAL '1 day', 'open', 'normal'),
         ($3, $5, 'Confirm accepted quote schedule', NOW() + INTERVAL '2 days', 'open', 'normal')`,
      [kyle.id, avery.id, riley.id, staff.id, admin.id]
    );

    await client.query(
      `INSERT INTO activities (contact_id, related_type, related_id, activity_type, title, body, actor_user_id, metadata_json, created_at)
       VALUES
         ($1, 'message', NULL, 'message.inbound', 'Inbound text received', 'Great. Please include delivery and a simple edging option.', NULL, '{}'::jsonb, NOW() - INTERVAL '12 minutes'),
         ($1, 'call', NULL, 'call.missed', 'Missed call', 'Missed inbound call from Kyle Bennett.', $4, '{}'::jsonb, NOW() - INTERVAL '45 minutes'),
         ($1, 'task', NULL, 'task.created', 'Task created', 'Reply with quote draft including edging option', $4, '{}'::jsonb, NOW() - INTERVAL '10 minutes'),
         ($2, 'quote', $6, 'quote.created', 'Quote created', 'BBQ-2026-0001 was created for Barkboys backyard refresh.', $4, '{"version":1}'::jsonb, NOW() - INTERVAL '3 hours'),
         ($2, 'quote', $6, 'quote.sent.sms', 'Quote sent by SMS', 'BBQ-2026-0001 was sent by text.', $4, '{}'::jsonb, NOW() - INTERVAL '2 hours'),
         ($2, 'call', NULL, 'call.disposition', 'Call disposition saved', 'Left voicemail about delivery options.', $4, '{"disposition":"left_voicemail"}'::jsonb, NOW() - INTERVAL '3 hours'),
         ($3, 'quote', $7, 'quote.accepted', 'Quote accepted', 'BBQ-2026-0002 was marked accepted.', $5, '{}'::jsonb, NOW() - INTERVAL '1 day'),
         ($3, 'message', NULL, 'message.outbound', 'Outbound text sent', 'Thanks for accepting. We will confirm install timing next.', $5, '{}'::jsonb, NOW() - INTERVAL '1 day')`,
      [kyle.id, avery.id, riley.id, staff.id, admin.id, averyQuote.id, rileyQuote.id]
    );

    await client.query(
      `INSERT INTO message_templates (name, channel, body)
       VALUES
         ('Quote ready', 'sms', 'Your Barkboys quote is ready. Want me to send it here?'),
         ('Missed call', 'sms', 'Sorry we missed you. What is the best time to call back?'),
         ('Follow-up', 'sms', 'Quick follow-up on your Barkboys quote. Would you like any changes?')`
    );

    await client.query(
      `INSERT INTO phone_routing_settings (label, inbound_number, destination_type, destination_value)
       VALUES ('Main Barkboys line', '+15035550000', 'queue', 'sales')`
    );

    await client.query(
      `INSERT INTO quote_defaults (label, tax_rate, default_delivery_total, terms)
       VALUES ('Barkboys default', 0, 95.00, 'Quote valid for 14 days. Delivery may vary by zone.')`
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
         ('quote', $1, 'barkboys', 'estimate', 'BB-DEMO-EST-0001', $2::jsonb)`,
      [
        averyQuote.id,
        JSON.stringify({
          note: "Example bridge for a future BarkBoys estimator quote.",
          quoteNumber: averyQuote.quote_number
        })
      ]
    );

    await client.query("COMMIT");
    res.json({ ok: true, contacts: contacts.length, quotes: 2 });
  } catch (error) {
    await client.query("ROLLBACK");
    next(error);
  } finally {
    client.release();
  }
});
