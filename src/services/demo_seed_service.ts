import { pool } from "../lib/db";

type Row = Record<string, any>;

const demoNow = "NOW()";

export async function seedDemoData() {
  const client = await pool.connect();

  try {
    await client.query("BEGIN");
    await resetDemoData(client);

    const users = await insertUsers(client);
    const tags = await insertTags(client);
    const accounts = await insertAccounts(client);
    const contacts = await insertContacts(client, users, accounts);
    const sites = await insertServiceSites(client, contacts);
    await insertContactTags(client, contacts, tags);
    const conversations = await insertConversations(client, contacts, users);
    await insertMessages(client, contacts, conversations, users);
    const calls = await insertCalls(client, contacts, conversations, users);
    const quotes = await insertQuotes(client, contacts, sites, users);
    await insertTasks(client, contacts, quotes, calls, users);
    await insertActivities(client, contacts, quotes, calls, users);
    await insertAdminSettings(client, quotes.taylorSent);

    await client.query("COMMIT");

    return {
      ok: true,
      reset: true,
      users: Object.keys(users).length,
      contacts: Object.keys(contacts).length,
      conversations: Object.keys(conversations).length,
      calls: Object.keys(calls).length,
      quotes: Object.keys(quotes).length,
      tasks: 7,
      activities: 28
    };
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

async function resetDemoData(client: any) {
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
}

async function insertUsers(client: any) {
  const rows = (
    await client.query(
      `INSERT INTO users (full_name, email, role)
       VALUES
         ('Jamie Stone', 'jamie@example.com', 'admin'),
         ('Morgan Lee', 'morgan@example.com', 'sales'),
         ('Riley Chen', 'riley@example.com', 'support'),
         ('Avery Brooks', 'avery@example.com', 'manager')
       RETURNING *`
    )
  ).rows;

  return indexByEmail(rows);
}

async function insertTags(client: any) {
  const rows = (
    await client.query(
      `INSERT INTO tags (name, color)
       VALUES
         ('New Lead', '#2563eb'),
         ('Quote Sent', '#7c3aed'),
         ('Needs Follow-Up', '#d97706'),
         ('Accepted', '#059669'),
         ('Company', '#0f766e'),
         ('VIP', '#be123c'),
         ('Stale', '#64748b'),
         ('Declined', '#b42318')
       RETURNING *`
    )
  ).rows;

  return indexByName(rows);
}

async function insertAccounts(client: any) {
  const rows = (
    await client.query(
      `INSERT INTO accounts (company_name, billing_address, notes)
       VALUES
         ('Northstar Operations', '410 Enterprise Way, Sample City, ST 10011', 'Multi-location company account.'),
         ('Riverside Facilities', '720 Harbor Blvd, Sample City, ST 10012', 'Facilities team with recurring service needs.'),
         ('Summit Group', '930 Summit Ave, Sample City, ST 10013', 'Procurement-led account.'),
         ('Clearwater Partners', '840 Lake Rd, Sample City, ST 10014', 'Decision maker prefers email summaries.')
       RETURNING *`
    )
  ).rows;

  return indexByName(rows, "company_name");
}

async function insertContacts(client: any, users: Row, accounts: Row) {
  const rows = (
    await client.query(
      `INSERT INTO contacts
       (account_id, first_name, last_name, display_name, mobile_phone, secondary_phone, email, preferred_contact_method, status, source, assigned_user_id)
       VALUES
         (NULL, 'Jordan', 'Lee', 'Jordan Lee', '+15035550141', NULL, 'jordan@example.com', 'sms', 'new_lead', 'website', $1),
         (NULL, 'Taylor', 'Morgan', 'Taylor Morgan', '+15035550142', NULL, 'taylor@example.com', 'sms', 'quoted', 'referral', $1),
         (NULL, 'Casey', 'Rivera', 'Casey Rivera', '+15035550143', '+15035559143', 'casey@example.com', 'phone', 'accepted', 'repeat_customer', $2),
         (NULL, 'Priya', 'Shah', 'Priya Shah', '+15035550144', NULL, 'priya@example.com', 'email', 'quoted', 'web_chat', $1),
         ($5, 'Alex', 'Grant', 'Alex Grant - Northstar Operations', '+15035550145', NULL, 'alex@northstar.example', 'sms', 'lead', 'partner', $3),
         (NULL, 'Maya', 'Chen', 'Maya Chen', '+15035550146', NULL, 'maya@example.com', 'sms', 'draft', 'phone', $1),
         (NULL, 'Elliot', 'Park', 'Elliot Park', '+15035550147', NULL, 'elliot@example.com', 'email', 'declined', 'website', $4),
         (NULL, 'Harper', 'Stone', 'Harper Stone', '+15035550148', NULL, 'harper@example.com', 'sms', 'follow_up_due', 'event', $1),
         ($6, 'Sam', 'Brooks', 'Sam Brooks - Riverside Facilities', '+15035550149', '+15035559149', 'sam@riverside.example', 'phone', 'customer', 'referral', $3),
         ($7, 'Nina', 'Patel', 'Nina Patel - Summit Group', '+15035550150', NULL, 'nina@summit.example', 'sms', 'lead', 'inbound_call', $2)
       RETURNING *`,
      [
        users["morgan@example.com"].id,
        users["jamie@example.com"].id,
        users["riley@example.com"].id,
        users["avery@example.com"].id,
        accounts["Northstar Operations"].id,
        accounts["Riverside Facilities"].id,
        accounts["Summit Group"].id
      ]
    )
  ).rows;

  return indexByName(rows, "display_name");
}

async function insertServiceSites(client: any, contacts: Row) {
  const rows = (
    await client.query(
      `INSERT INTO service_sites
       (contact_id, label, address_line_1, address_line_2, city, state, zip, delivery_zone, site_notes)
       VALUES
         ($1, 'Primary Site', '100 Example Ave', NULL, 'Sample City', 'ST', '10001', 'Standard', 'Prefers morning communication.'),
         ($2, 'Project Site', '200 Market St', 'Suite 4', 'Sample City', 'ST', '10002', 'Priority', 'Access notes on file.'),
         ($2, 'Secondary Location', '205 Market St', NULL, 'Sample City', 'ST', '10002', 'Standard', 'Use side entrance.'),
         ($3, 'Secondary Site', '300 Oak Dr', NULL, 'Sample City', 'ST', '10003', 'Standard', 'Accepted implementation proposal.'),
         ($4, 'Remote Office', '400 Pine Ln', NULL, 'Sample City', 'ST', '10004', 'Remote', 'Customer asked for email updates.'),
         ($5, 'Northstar HQ', '410 Enterprise Way', NULL, 'Sample City', 'ST', '10011', 'Priority', 'Front desk checks in all visitors.'),
         ($5, 'Northstar Annex', '412 Enterprise Way', 'Building B', 'Sample City', 'ST', '10011', 'Standard', 'Separate loading area.'),
         ($6, 'Home Office', '500 Cedar Ct', NULL, 'Sample City', 'ST', '10005', 'Standard', 'Draft proposal in progress.'),
         ($7, 'Primary Site', '600 Birch St', NULL, 'Sample City', 'ST', '10006', 'Standard', 'Declined current proposal.'),
         ($8, 'Project Site', '700 Willow Rd', NULL, 'Sample City', 'ST', '10007', 'Standard', 'Needs a follow-up.'),
         ($9, 'Riverside Main', '720 Harbor Blvd', NULL, 'Sample City', 'ST', '10012', 'Priority', 'Multiple service locations.'),
         ($9, 'Riverside Warehouse', '730 Harbor Blvd', 'Dock 2', 'Sample City', 'ST', '10012', 'Standard', 'Warehouse contact prefers calls.'),
         ($10, 'Summit Office', '930 Summit Ave', NULL, 'Sample City', 'ST', '10013', 'Standard', 'Voicemail left with assistant.')
       RETURNING *`,
      [
        contacts["Jordan Lee"].id,
        contacts["Taylor Morgan"].id,
        contacts["Casey Rivera"].id,
        contacts["Priya Shah"].id,
        contacts["Alex Grant - Northstar Operations"].id,
        contacts["Maya Chen"].id,
        contacts["Elliot Park"].id,
        contacts["Harper Stone"].id,
        contacts["Sam Brooks - Riverside Facilities"].id,
        contacts["Nina Patel - Summit Group"].id
      ]
    )
  ).rows;

  return {
    jordan: rows[0],
    taylorPrimary: rows[1],
    taylorSecondary: rows[2],
    casey: rows[3],
    priya: rows[4],
    northstarHq: rows[5],
    northstarAnnex: rows[6],
    maya: rows[7],
    elliot: rows[8],
    harper: rows[9],
    riversideMain: rows[10],
    riversideWarehouse: rows[11],
    nina: rows[12]
  };
}

async function insertContactTags(client: any, contacts: Row, tags: Row) {
  const values = [
    [contacts["Jordan Lee"].id, tags["New Lead"].id],
    [contacts["Taylor Morgan"].id, tags["Quote Sent"].id],
    [contacts["Taylor Morgan"].id, tags["Needs Follow-Up"].id],
    [contacts["Casey Rivera"].id, tags["Accepted"].id],
    [contacts["Priya Shah"].id, tags["Quote Sent"].id],
    [contacts["Alex Grant - Northstar Operations"].id, tags["Company"].id],
    [contacts["Alex Grant - Northstar Operations"].id, tags["New Lead"].id],
    [contacts["Maya Chen"].id, tags["VIP"].id],
    [contacts["Elliot Park"].id, tags["Declined"].id],
    [contacts["Harper Stone"].id, tags["Stale"].id],
    [contacts["Sam Brooks - Riverside Facilities"].id, tags["Company"].id],
    [contacts["Nina Patel - Summit Group"].id, tags["Needs Follow-Up"].id]
  ];

  for (const [contactId, tagId] of values) {
    await client.query("INSERT INTO contact_tags (contact_id, tag_id) VALUES ($1, $2)", [contactId, tagId]);
  }
}

async function insertConversations(client: any, contacts: Row, users: Row) {
  const rows = (
    await client.query(
      `INSERT INTO conversations
       (contact_id, assigned_user_id, channel_type, status, last_message_at, unread_count)
       VALUES
         ($1, $11, 'sms', 'open', NOW() - INTERVAL '12 minutes', 2),
         ($2, $11, 'sms', 'open', NOW() - INTERVAL '2 hours', 0),
         ($3, $12, 'sms', 'open', NOW() - INTERVAL '1 day', 0),
         ($4, $11, 'sms', 'open', NOW() - INTERVAL '5 hours', 0),
         ($5, $13, 'sms', 'open', NOW() - INTERVAL '24 minutes', 1),
         ($6, $11, 'sms', 'open', NOW() - INTERVAL '3 days', 0),
         ($7, $14, 'sms', 'closed', NOW() - INTERVAL '4 days', 0),
         ($8, $11, 'sms', 'open', NOW() - INTERVAL '6 days', 0),
         ($9, $13, 'sms', 'open', NOW() - INTERVAL '50 minutes', 0),
         ($10, $12, 'sms', 'open', NOW() - INTERVAL '18 minutes', 1)
       RETURNING *`,
      [
        contacts["Jordan Lee"].id,
        contacts["Taylor Morgan"].id,
        contacts["Casey Rivera"].id,
        contacts["Priya Shah"].id,
        contacts["Alex Grant - Northstar Operations"].id,
        contacts["Maya Chen"].id,
        contacts["Elliot Park"].id,
        contacts["Harper Stone"].id,
        contacts["Sam Brooks - Riverside Facilities"].id,
        contacts["Nina Patel - Summit Group"].id,
        users["morgan@example.com"].id,
        users["jamie@example.com"].id,
        users["riley@example.com"].id,
        users["avery@example.com"].id
      ]
    )
  ).rows;

  return {
    jordan: rows[0],
    taylor: rows[1],
    casey: rows[2],
    priya: rows[3],
    northstar: rows[4],
    maya: rows[5],
    elliot: rows[6],
    harper: rows[7],
    riverside: rows[8],
    nina: rows[9]
  };
}

async function insertMessages(client: any, contacts: Row, conversations: Row, users: Row) {
  const rows = [
    [conversations.jordan.id, contacts["Jordan Lee"].id, "inbound", "demo-jordan-1", "Hi, can you send a proposal for the standard service package?", "received", null, "34 minutes"],
    [conversations.jordan.id, contacts["Jordan Lee"].id, "outbound", "demo-jordan-2", "Absolutely. I can build that from your site notes and send a proposal here.", "sent", users["morgan@example.com"].id, "20 minutes"],
    [conversations.jordan.id, contacts["Jordan Lee"].id, "inbound", "demo-jordan-3", "Great. Please include delivery timing and an implementation option.", "received", null, "12 minutes"],
    [conversations.taylor.id, contacts["Taylor Morgan"].id, "outbound", "demo-taylor-1", "Your proposal QTE-2026-0001 is ready. Want me to adjust the timing?", "sent", users["morgan@example.com"].id, "2 hours"],
    [conversations.casey.id, contacts["Casey Rivera"].id, "outbound", "demo-casey-1", "Thanks for accepting. We will confirm timing next.", "sent", users["jamie@example.com"].id, "1 day"],
    [conversations.priya.id, contacts["Priya Shah"].id, "inbound", "demo-priya-1", "I opened the proposal. Can you clarify the optional support line?", "received", null, "5 hours"],
    [conversations.priya.id, contacts["Priya Shah"].id, "outbound", "demo-priya-2", "Yes, that line can be removed or adjusted before approval.", "sent", users["morgan@example.com"].id, "4 hours"],
    [conversations.northstar.id, contacts["Alex Grant - Northstar Operations"].id, "inbound", "demo-northstar-1", "We have two locations. Can someone confirm whether both are included?", "received", null, "24 minutes"],
    [conversations.maya.id, contacts["Maya Chen"].id, "outbound", "demo-maya-1", "I am drafting the proposal and will send it when the scope is confirmed.", "sent", users["morgan@example.com"].id, "3 days"],
    [conversations.elliot.id, contacts["Elliot Park"].id, "inbound", "demo-elliot-1", "Thanks, but we are going to pass for now.", "received", null, "4 days"],
    [conversations.harper.id, contacts["Harper Stone"].id, "outbound", "demo-harper-1", "Checking in on the proposal I sent last week.", "sent", users["morgan@example.com"].id, "6 days"],
    [conversations.riverside.id, contacts["Sam Brooks - Riverside Facilities"].id, "outbound", "demo-riverside-1", "I logged both locations and will include them in the account record.", "sent", users["riley@example.com"].id, "50 minutes"],
    [conversations.nina.id, contacts["Nina Patel - Summit Group"].id, "inbound", "demo-nina-1", "I missed your call. Can you text the next step?", "received", null, "18 minutes"]
  ];

  for (const row of rows) {
    await client.query(
      `INSERT INTO messages
       (conversation_id, contact_id, direction, channel, provider_name, provider_message_id, body, delivery_status, sent_by_user_id, created_at)
       VALUES ($1, $2, $3, 'sms', 'generic_webhook', $4, $5, $6, $7, NOW() - ($8::interval))`,
      row
    );
  }
}

async function insertCalls(client: any, contacts: Row, conversations: Row, users: Row) {
  const rows = (
    await client.query(
      `INSERT INTO calls
       (contact_id, conversation_id, provider_name, provider_call_id, direction, status, from_number, to_number, started_at, ended_at, duration_seconds, recording_url, voicemail_url, assigned_user_id, disposition, notes)
       VALUES
         ($1, $8, 'generic_webhook', 'demo-call-jordan-1', 'inbound', 'missed', $18, '+15035550000', NOW() - INTERVAL '45 minutes', NOW() - INTERVAL '44 minutes', 0, NULL, NULL, $15, NULL, NULL),
         ($2, $9, 'generic_webhook', 'demo-call-taylor-1', 'outbound', 'completed', 'staff', $19, NOW() - INTERVAL '3 hours', NOW() - INTERVAL '2 hours 56 minutes', 246, NULL, NULL, $15, 'left_voicemail', 'Left voicemail about proposal options.'),
         ($3, $10, 'generic_webhook', 'demo-call-casey-1', 'outbound', 'completed', 'staff', $20, NOW() - INTERVAL '1 day 2 hours', NOW() - INTERVAL '1 day 1 hour 55 minutes', 305, NULL, NULL, $16, 'confirmed_next_step', 'Confirmed accepted proposal handoff.'),
         ($4, $11, 'generic_webhook', 'demo-call-priya-1', 'inbound', 'completed', $21, '+15035550000', NOW() - INTERVAL '5 hours', NOW() - INTERVAL '4 hours 52 minutes', 480, NULL, NULL, $15, 'answered_questions', 'Reviewed viewed proposal and optional support line.'),
         ($5, $12, 'generic_webhook', 'demo-call-northstar-1', 'inbound', 'missed', $22, '+15035550000', NOW() - INTERVAL '1 hour', NOW() - INTERVAL '59 minutes', 0, NULL, 'https://example.invalid/voicemail/northstar.wav', $17, 'voicemail', 'Voicemail placeholder: asked about including both locations.'),
         ($6, $13, 'generic_webhook', 'demo-call-harper-1', 'outbound', 'no-answer', 'staff', $23, NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days', 0, NULL, NULL, $15, 'no_answer', 'No answer on stale proposal follow-up.'),
         ($7, $14, 'generic_webhook', 'demo-call-nina-1', 'inbound', 'missed', $24, '+15035550000', NOW() - INTERVAL '30 minutes', NOW() - INTERVAL '29 minutes', 0, NULL, 'https://example.invalid/voicemail/summit.wav', $16, 'voicemail', 'Voicemail placeholder: wants next-step details.')
       RETURNING *`,
      [
        contacts["Jordan Lee"].id,
        contacts["Taylor Morgan"].id,
        contacts["Casey Rivera"].id,
        contacts["Priya Shah"].id,
        contacts["Alex Grant - Northstar Operations"].id,
        contacts["Harper Stone"].id,
        contacts["Nina Patel - Summit Group"].id,
        conversations.jordan.id,
        conversations.taylor.id,
        conversations.casey.id,
        conversations.priya.id,
        conversations.northstar.id,
        conversations.harper.id,
        conversations.nina.id,
        users["morgan@example.com"].id,
        users["jamie@example.com"].id,
        users["riley@example.com"].id,
        contacts["Jordan Lee"].mobile_phone,
        contacts["Taylor Morgan"].mobile_phone,
        contacts["Casey Rivera"].mobile_phone,
        contacts["Priya Shah"].mobile_phone,
        contacts["Alex Grant - Northstar Operations"].mobile_phone,
        contacts["Harper Stone"].mobile_phone,
        contacts["Nina Patel - Summit Group"].mobile_phone
      ]
    )
  ).rows;

  return {
    jordanMissed: rows[0],
    taylorVoicemail: rows[1],
    caseyAnswered: rows[2],
    priyaAnswered: rows[3],
    northstarVoicemail: rows[4],
    harperNoAnswer: rows[5],
    ninaVoicemail: rows[6]
  };
}

async function insertQuotes(client: any, contacts: Row, sites: Row, users: Row) {
  const quotes = (
    await client.query(
      `INSERT INTO quotes
       (contact_id, service_site_id, quote_number, title, status, subtotal, delivery_total, tax_total, grand_total, sent_at, accepted_at, created_by_user_id, created_at, updated_at)
       VALUES
         ($1, $8, 'QTE-2026-0001', 'Standard service proposal', 'sent', 1845.00, 95.00, 0.00, 1940.00, NOW() - INTERVAL '2 hours', NULL, $15, NOW() - INTERVAL '3 hours', NOW() - INTERVAL '2 hours'),
         ($2, $9, 'QTE-2026-0002', 'Accepted implementation proposal', 'accepted', 980.00, 75.00, 0.00, 1055.00, NOW() - INTERVAL '3 days', NOW() - INTERVAL '1 day', $16, NOW() - INTERVAL '4 days', NOW() - INTERVAL '1 day'),
         ($3, $10, 'QTE-2026-0003', 'Viewed support proposal', 'viewed', 1320.00, 0.00, 0.00, 1320.00, NOW() - INTERVAL '1 day', NULL, $15, NOW() - INTERVAL '2 days', NOW() - INTERVAL '5 hours'),
         ($4, $11, 'QTE-2026-0004', 'Multi-location proposal', 'follow_up_due', 2680.00, 150.00, 0.00, 2830.00, NOW() - INTERVAL '3 days', NULL, $17, NOW() - INTERVAL '4 days', NOW() - INTERVAL '1 day'),
         ($5, $12, 'QTE-2026-0005', 'Draft service proposal', 'draft', 760.00, 50.00, 0.00, 810.00, NULL, NULL, $15, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
         ($6, $13, 'QTE-2026-0006', 'Declined proposal', 'declined', 1100.00, 90.00, 0.00, 1190.00, NOW() - INTERVAL '5 days', NULL, $18, NOW() - INTERVAL '6 days', NOW() - INTERVAL '4 days'),
         ($7, $14, 'QTE-2026-0007', 'Stale proposal needing follow-up', 'follow_up_due', 1560.00, 80.00, 0.00, 1640.00, NOW() - INTERVAL '7 days', NULL, $15, NOW() - INTERVAL '8 days', NOW() - INTERVAL '2 days')
       RETURNING *`,
      [
        contacts["Taylor Morgan"].id,
        contacts["Casey Rivera"].id,
        contacts["Priya Shah"].id,
        contacts["Alex Grant - Northstar Operations"].id,
        contacts["Maya Chen"].id,
        contacts["Elliot Park"].id,
        contacts["Harper Stone"].id,
        sites.taylorPrimary.id,
        sites.casey.id,
        sites.priya.id,
        sites.northstarHq.id,
        sites.maya.id,
        sites.elliot.id,
        sites.harper.id,
        users["morgan@example.com"].id,
        users["jamie@example.com"].id,
        users["riley@example.com"].id,
        users["avery@example.com"].id
      ]
    )
  ).rows;

  const quoteMap = {
    taylorSent: quotes[0],
    caseyAccepted: quotes[1],
    priyaViewed: quotes[2],
    northstarFollowUp: quotes[3],
    mayaDraft: quotes[4],
    elliotDeclined: quotes[5],
    harperFollowUp: quotes[6]
  };

  await insertQuoteVersions(client, quoteMap, users);
  return quoteMap;
}

async function insertQuoteVersions(client: any, quotes: Row, users: Row) {
  const versionRows = [
    [quotes.taylorSent.id, 1, "Initial sent proposal.", 1845, 95, 0, 1940, users["morgan@example.com"].id, [["Core service package", 8, "unit", 145], ["Setup and coordination", 1, "project", 485], ["Manual access adjustment", 1, "each", 200]]],
    [quotes.caseyAccepted.id, 1, "Accepted implementation package.", 980, 75, 0, 1055, users["jamie@example.com"].id, [["Recurring service package", 5, "unit", 160], ["Implementation add-on", 1, "project", 180]]],
    [quotes.priyaViewed.id, 1, "Original support proposal.", 1180, 0, 0, 1180, users["morgan@example.com"].id, [["Support package", 1, "project", 1180]]],
    [quotes.priyaViewed.id, 2, "Revised after customer viewed proposal.", 1320, 0, 0, 1320, users["morgan@example.com"].id, [["Support package", 1, "project", 1180], ["Optional support line", 1, "each", 140]]],
    [quotes.northstarFollowUp.id, 1, "Two-location proposal.", 2680, 150, 0, 2830, users["riley@example.com"].id, [["Location one service", 1, "project", 1380], ["Location two service", 1, "project", 1300]]],
    [quotes.mayaDraft.id, 1, "Draft proposal awaiting scope confirmation.", 760, 50, 0, 810, users["morgan@example.com"].id, [["Draft service package", 1, "project", 760]]],
    [quotes.elliotDeclined.id, 1, "Declined current proposal.", 1100, 90, 0, 1190, users["avery@example.com"].id, [["Service package", 1, "project", 1100]]],
    [quotes.harperFollowUp.id, 1, "Original stale proposal.", 1425, 80, 0, 1505, users["morgan@example.com"].id, [["Standard package", 1, "project", 1425]]],
    [quotes.harperFollowUp.id, 2, "Revised stale proposal.", 1560, 80, 0, 1640, users["morgan@example.com"].id, [["Standard package revised", 1, "project", 1560]]]
  ];

  const currentVersions = new Map<string, string>();
  for (const [quoteId, versionNumber, notes, subtotal, delivery, tax, grandTotal, userId, lineItems] of versionRows) {
    const version = (
      await client.query(
        `INSERT INTO quote_versions
         (quote_id, version_number, pricing_snapshot_json, notes, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8, $9)
         RETURNING *`,
        [
          quoteId,
          versionNumber,
          JSON.stringify({ source: "demo_seed", lineItems }),
          notes,
          subtotal,
          delivery,
          tax,
          grandTotal,
          userId
        ]
      )
    ).rows[0];

    currentVersions.set(String(quoteId), version.id);
    let sortOrder = 1;
    for (const [name, quantity, unit, unitPrice] of lineItems as any[]) {
      await client.query(
        `INSERT INTO quote_line_items
         (quote_version_id, item_type, name, quantity, unit, unit_price, total_price, sort_order, source_reference)
         VALUES ($1, 'service', $2, $3, $4, $5, $6, $7, 'demo_seed')`,
        [version.id, name, quantity, unit, unitPrice, Number(quantity) * Number(unitPrice), sortOrder++]
      );
    }
  }

  for (const [quoteId, versionId] of currentVersions.entries()) {
    await client.query("UPDATE quotes SET current_version_id = $2 WHERE id = $1", [quoteId, versionId]);
  }
}

async function insertTasks(client: any, contacts: Row, quotes: Row, calls: Row, users: Row) {
  await client.query(
    `INSERT INTO tasks (contact_id, assigned_user_id, related_type, related_id, title, due_at, status, priority)
     VALUES
       ($1, $11, NULL, NULL, 'Respond to new lead text', NOW() + INTERVAL '1 hour', 'open', 'high'),
       ($2, $11, 'quote', $8, 'Follow up on quote/proposal', NOW() + INTERVAL '22 hours', 'open', 'normal'),
       ($3, $11, 'quote', $9, 'Follow up on viewed proposal', NOW() - INTERVAL '2 hours', 'open', 'high'),
       ($4, $13, 'quote', $10, 'Follow up on quote/proposal', NOW() - INTERVAL '1 day', 'open', 'high'),
       ($5, $12, 'call', $14, 'Return missed call', NOW() + INTERVAL '2 hours', 'open', 'high'),
       ($6, $11, NULL, NULL, 'Confirm proposal scope', NOW() - INTERVAL '1 day', 'open', 'normal'),
       ($7, $12, 'call', $15, 'Send next-step text after voicemail', NOW() + INTERVAL '3 hours', 'open', 'normal')`,
    [
      contacts["Jordan Lee"].id,
      contacts["Taylor Morgan"].id,
      contacts["Priya Shah"].id,
      contacts["Harper Stone"].id,
      contacts["Nina Patel - Summit Group"].id,
      contacts["Maya Chen"].id,
      contacts["Alex Grant - Northstar Operations"].id,
      quotes.taylorSent.id,
      quotes.priyaViewed.id,
      quotes.harperFollowUp.id,
      users["morgan@example.com"].id,
      users["jamie@example.com"].id,
      users["riley@example.com"].id,
      calls.ninaVoicemail.id,
      calls.northstarVoicemail.id
    ]
  );
}

async function insertActivities(client: any, contacts: Row, quotes: Row, calls: Row, users: Row) {
  const values = [
    [contacts["Jordan Lee"].id, "contact", contacts["Jordan Lee"].id, "contact.created", "Contact created", "Jordan Lee was added from website lead form.", users["morgan@example.com"].id, "2 days"],
    [contacts["Jordan Lee"].id, "message", null, "message.inbound", "Inbound text received", "Great. Please include delivery timing and an implementation option.", null, "12 minutes"],
    [contacts["Jordan Lee"].id, "message", null, "message.outbound", "Outbound text sent", "Absolutely. I can build that from your site notes and send a proposal here.", users["morgan@example.com"].id, "20 minutes"],
    [contacts["Jordan Lee"].id, "call", calls.jordanMissed.id, "call.missed", "Missed call", "Missed inbound call from Jordan Lee.", users["morgan@example.com"].id, "45 minutes"],
    [contacts["Jordan Lee"].id, "task", null, "task.created", "Task created", "Respond to new lead text", users["morgan@example.com"].id, "10 minutes"],
    [contacts["Taylor Morgan"].id, "quote", quotes.taylorSent.id, "quote_created", "Quote/proposal created", "QTE-2026-0001 was created for Standard service proposal.", users["morgan@example.com"].id, "3 hours"],
    [contacts["Taylor Morgan"].id, "quote", quotes.taylorSent.id, "quote_sent", "Quote/proposal sent", "QTE-2026-0001 was sent by text.", users["morgan@example.com"].id, "2 hours"],
    [contacts["Taylor Morgan"].id, "task", null, "task.created", "Task created", "Follow up on quote/proposal", users["morgan@example.com"].id, "2 hours"],
    [contacts["Taylor Morgan"].id, "call", calls.taylorVoicemail.id, "call.disposition", "Call disposition saved", "Left voicemail about proposal options.", users["morgan@example.com"].id, "3 hours"],
    [contacts["Casey Rivera"].id, "quote", quotes.caseyAccepted.id, "quote_accepted", "Quote/proposal accepted", "QTE-2026-0002 was marked accepted.", users["jamie@example.com"].id, "1 day"],
    [contacts["Casey Rivera"].id, "quote", quotes.caseyAccepted.id, "quote_next_step", "Next step needed", "Prepare the next customer step.", users["jamie@example.com"].id, "1 day"],
    [contacts["Priya Shah"].id, "quote", quotes.priyaViewed.id, "quote_created", "Quote/proposal created", "QTE-2026-0003 was created.", users["morgan@example.com"].id, "2 days"],
    [contacts["Priya Shah"].id, "quote", quotes.priyaViewed.id, "quote_sent", "Quote/proposal sent", "QTE-2026-0003 was sent by email.", users["morgan@example.com"].id, "1 day"],
    [contacts["Priya Shah"].id, "quote", quotes.priyaViewed.id, "quote_viewed", "Quote/proposal viewed", "QTE-2026-0003 was viewed.", null, "5 hours"],
    [contacts["Priya Shah"].id, "note", null, "note.created", "Note added", "Customer asked about optional support line.", users["morgan@example.com"].id, "4 hours"],
    [contacts["Alex Grant - Northstar Operations"].id, "message", null, "message.inbound", "Inbound text received", "We have two locations. Can someone confirm whether both are included?", null, "24 minutes"],
    [contacts["Alex Grant - Northstar Operations"].id, "call", calls.northstarVoicemail.id, "call.missed", "Missed call", "Voicemail placeholder logged for Northstar Operations.", users["riley@example.com"].id, "1 hour"],
    [contacts["Alex Grant - Northstar Operations"].id, "task", null, "task.created", "Task created", "Send next-step text after voicemail", users["riley@example.com"].id, "50 minutes"],
    [contacts["Maya Chen"].id, "quote", quotes.mayaDraft.id, "quote_created", "Quote/proposal created", "Draft proposal created for Maya Chen.", users["morgan@example.com"].id, "1 day"],
    [contacts["Maya Chen"].id, "task", null, "task.created", "Task created", "Confirm proposal scope", users["morgan@example.com"].id, "1 day"],
    [contacts["Elliot Park"].id, "quote", quotes.elliotDeclined.id, "quote_declined", "Quote/proposal declined", "QTE-2026-0006 was marked declined.", users["avery@example.com"].id, "4 days"],
    [contacts["Elliot Park"].id, "quote", quotes.elliotDeclined.id, "quote_closed_lost", "Quote/proposal closed lost", "Customer passed on the current proposal.", users["avery@example.com"].id, "4 days"],
    [contacts["Harper Stone"].id, "quote", quotes.harperFollowUp.id, "quote_revised", "Quote/proposal revised", "QTE-2026-0007 version 2 was saved.", users["morgan@example.com"].id, "2 days"],
    [contacts["Harper Stone"].id, "quote", quotes.harperFollowUp.id, "quote_sent", "Quote/proposal sent", "QTE-2026-0007 was sent and needs follow-up.", users["morgan@example.com"].id, "7 days"],
    [contacts["Harper Stone"].id, "task", null, "task.created", "Task created", "Follow up on quote/proposal", users["morgan@example.com"].id, "1 day"],
    [contacts["Sam Brooks - Riverside Facilities"].id, "contact", contacts["Sam Brooks - Riverside Facilities"].id, "contact.created", "Contact created", "Company contact added with two service sites.", users["riley@example.com"].id, "3 days"],
    [contacts["Nina Patel - Summit Group"].id, "call", calls.ninaVoicemail.id, "call.missed", "Missed call", "Voicemail placeholder logged for Summit Group.", users["jamie@example.com"].id, "30 minutes"],
    [contacts["Nina Patel - Summit Group"].id, "message", null, "message.inbound", "Inbound text received", "I missed your call. Can you text the next step?", null, "18 minutes"]
  ];

  for (const [contactId, relatedType, relatedId, activityType, title, body, actorUserId, age] of values) {
    await client.query(
      `INSERT INTO activities
       (contact_id, related_type, related_id, activity_type, title, body, actor_user_id, metadata_json, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, '{}'::jsonb, ${demoNow} - ($8::interval))`,
      [contactId, relatedType, relatedId, activityType, title, body, actorUserId, age]
    );
  }
}

async function insertAdminSettings(client: any, taylorQuote: Row) {
  await client.query(
    `INSERT INTO message_templates (name, channel, body)
     VALUES
       ('Proposal ready', 'sms', 'Your proposal is ready. Would you like me to send it here?'),
       ('Missed call', 'sms', 'Sorry we missed you. What is the best time to call back?'),
       ('Follow-up', 'sms', 'Quick follow-up on your proposal. Would you like any changes?'),
       ('Next step', 'email', 'Thanks for reviewing the proposal. Here are the next steps.')`
  );

  await client.query(
    `INSERT INTO phone_routing_settings (label, inbound_number, destination_type, destination_value)
     VALUES
       ('Main line', '+15035550000', 'queue', 'sales'),
       ('Support line', '+15035550001', 'queue', 'support')`
  );

  await client.query(
    `INSERT INTO quote_defaults (label, tax_rate, default_delivery_total, terms)
     VALUES ('Default proposal terms', 0, 95.00, 'Proposal valid for 14 days. Final scope may change after review.')`
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
     VALUES ('quote', $1, 'future-external-system', 'proposal', 'EXT-DEMO-PROP-0001', $2::jsonb)`,
    [
      taylorQuote.id,
      JSON.stringify({
        note: "Example bridge for a future external workflow.",
        quoteNumber: taylorQuote.quote_number
      })
    ]
  );
}

function indexByEmail(rows: Row[]) {
  return Object.fromEntries(rows.map((row) => [row.email, row]));
}

function indexByName(rows: Row[], field = "name") {
  return Object.fromEntries(rows.map((row) => [row[field], row]));
}
