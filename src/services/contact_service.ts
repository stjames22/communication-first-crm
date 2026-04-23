import { query } from "../lib/db";
import { normalizePhone } from "../lib/normalizePhone";
import { createActivity } from "./activity_service";

export type ContactInput = {
  accountId?: string | null;
  firstName?: string | null;
  lastName?: string | null;
  displayName?: string | null;
  mobilePhone: string;
  secondaryPhone?: string | null;
  email?: string | null;
  preferredContactMethod?: string | null;
  status?: string | null;
  source?: string | null;
  assignedUserId?: string | null;
  duplicateWarningAccepted?: boolean | null;
};

export type ServiceSiteInput = {
  label?: string | null;
  addressLine1: string;
  addressLine2?: string | null;
  city: string;
  state: string;
  zip: string;
  deliveryZone?: string | null;
  siteNotes?: string | null;
};

export async function listContacts() {
  const result = await query(
    `SELECT
       c.*,
       u.full_name AS assigned_user_name,
       COALESCE(
         JSON_AGG(DISTINCT JSONB_BUILD_OBJECT('id', t.id, 'name', t.name, 'color', t.color))
         FILTER (WHERE t.id IS NOT NULL),
         '[]'::json
       ) AS tags,
       (
         SELECT JSON_BUILD_OBJECT(
           'id', s.id,
           'label', s.label,
           'address_line_1', s.address_line_1,
           'city', s.city,
           'state', s.state,
           'zip', s.zip,
           'delivery_zone', s.delivery_zone
         )
         FROM service_sites s
         WHERE s.contact_id = c.id
         ORDER BY s.created_at ASC
         LIMIT 1
       ) AS primary_site,
       (
         SELECT JSON_BUILD_OBJECT('id', q.id, 'status', q.status, 'quote_number', q.quote_number, 'grand_total', q.grand_total)
         FROM quotes q
         WHERE q.contact_id = c.id
         ORDER BY q.updated_at DESC
         LIMIT 1
       ) AS latest_quote
     FROM contacts c
     LEFT JOIN users u ON u.id = c.assigned_user_id
     LEFT JOIN contact_tags ct ON ct.contact_id = c.id
     LEFT JOIN tags t ON t.id = ct.tag_id
     GROUP BY c.id, u.full_name
     ORDER BY c.updated_at DESC
     LIMIT 200`
  );

  return result.rows;
}

export async function createContact(input: ContactInput) {
  const displayName = input.displayName || [input.firstName, input.lastName].filter(Boolean).join(" ") || "New Lead";
  const phone = normalizePhone(input.mobilePhone);

  const result = await query(
    `INSERT INTO contacts
     (account_id, first_name, last_name, display_name, mobile_phone, secondary_phone, email,
      preferred_contact_method, status, source, assigned_user_id)
     VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8, 'sms'), COALESCE($9, 'lead'), $10, $11)
     RETURNING *`,
    [
      input.accountId ?? null,
      input.firstName ?? null,
      input.lastName ?? null,
      displayName,
      phone,
      input.secondaryPhone ? normalizePhone(input.secondaryPhone) : null,
      input.email || null,
      input.preferredContactMethod ?? null,
      input.status ?? null,
      input.source ?? "manual",
      input.assignedUserId ?? null
    ]
  );

  const contact = result.rows[0];
  await createActivity({
    contactId: contact.id,
    relatedType: "contact",
    relatedId: contact.id,
    activityType: "contact.created",
    title: "Contact created",
    body: `${contact.display_name} was added to CRM.`,
    actorUserId: contact.assigned_user_id,
    metadata: { source: contact.source }
  });

  if (input.duplicateWarningAccepted) {
    await createActivity({
      contactId: contact.id,
      relatedType: "note",
      activityType: "note.created",
      title: "Note added",
      body: "Created after duplicate warning was shown.",
      actorUserId: contact.assigned_user_id
    });
  }

  return contact;
}

export async function getContact(contactId: string) {
  const contact = (
    await query(
      `SELECT c.*, u.full_name AS assigned_user_name
       FROM contacts c
       LEFT JOIN users u ON u.id = c.assigned_user_id
       WHERE c.id = $1
       LIMIT 1`,
      [contactId]
    )
  ).rows[0];

  if (!contact) {
    return null;
  }

  const [sites, conversations, calls, quotes, notes, tasks, attachments, tags] = await Promise.all([
    query("SELECT * FROM service_sites WHERE contact_id = $1 ORDER BY created_at ASC", [contactId]),
    query("SELECT * FROM conversations WHERE contact_id = $1 ORDER BY COALESCE(last_message_at, created_at) DESC", [contactId]),
    query("SELECT * FROM calls WHERE contact_id = $1 ORDER BY started_at DESC LIMIT 100", [contactId]),
    query("SELECT * FROM quotes WHERE contact_id = $1 ORDER BY updated_at DESC", [contactId]),
    query("SELECT * FROM activities WHERE contact_id = $1 AND activity_type = 'note.created' ORDER BY created_at DESC", [contactId]),
    query("SELECT * FROM tasks WHERE contact_id = $1 ORDER BY COALESCE(due_at, created_at) ASC", [contactId]),
    query("SELECT * FROM attachments WHERE contact_id = $1 ORDER BY created_at DESC", [contactId]),
    query(
      `SELECT t.*
       FROM tags t
       JOIN contact_tags ct ON ct.tag_id = t.id
       WHERE ct.contact_id = $1
       ORDER BY t.name ASC`,
      [contactId]
    )
  ]);

  return {
    contact,
    sites: sites.rows,
    conversations: conversations.rows,
    calls: calls.rows,
    quotes: quotes.rows,
    notes: notes.rows,
    tasks: tasks.rows,
    attachments: attachments.rows,
    tags: tags.rows
  };
}

export async function updateContact(contactId: string, input: Partial<ContactInput>) {
  const result = await query(
    `UPDATE contacts
     SET account_id = COALESCE($2, account_id),
         first_name = COALESCE($3, first_name),
         last_name = COALESCE($4, last_name),
         display_name = COALESCE($5, display_name),
         mobile_phone = COALESCE($6, mobile_phone),
         secondary_phone = COALESCE($7, secondary_phone),
         email = COALESCE($8, email),
         preferred_contact_method = COALESCE($9, preferred_contact_method),
         status = COALESCE($10, status),
         source = COALESCE($11, source),
         assigned_user_id = COALESCE($12, assigned_user_id)
     WHERE id = $1
     RETURNING *`,
    [
      contactId,
      input.accountId ?? null,
      input.firstName ?? null,
      input.lastName ?? null,
      input.displayName ?? null,
      input.mobilePhone ? normalizePhone(input.mobilePhone) : null,
      input.secondaryPhone ? normalizePhone(input.secondaryPhone) : null,
      input.email || null,
      input.preferredContactMethod ?? null,
      input.status ?? null,
      input.source ?? null,
      input.assignedUserId ?? null
    ]
  );

  return result.rows[0] ?? null;
}

export async function findContactByPhone(phone: string) {
  const result = await query("SELECT * FROM contacts WHERE mobile_phone = $1 LIMIT 1", [normalizePhone(phone)]);
  return result.rows[0] ?? null;
}

export async function findOrCreateLeadShellByPhone(phone: string, source = "inbound_sms") {
  const normalizedPhone = normalizePhone(phone);
  const existing = await findContactByPhone(normalizedPhone);
  if (existing) {
    return existing;
  }

  return createContact({
    displayName: `Lead ${normalizedPhone}`,
    mobilePhone: normalizedPhone,
    preferredContactMethod: "sms",
    status: "new_lead",
    source
  });
}

export async function listServiceSites(contactId: string) {
  const result = await query("SELECT * FROM service_sites WHERE contact_id = $1 ORDER BY created_at ASC", [contactId]);
  return result.rows;
}

export async function createServiceSite(contactId: string, input: ServiceSiteInput, actorUserId?: string | null) {
  const result = await query(
    `INSERT INTO service_sites
     (contact_id, label, address_line_1, address_line_2, city, state, zip, delivery_zone, site_notes)
     VALUES ($1, COALESCE($2, 'Primary'), $3, $4, $5, $6, $7, $8, $9)
     RETURNING *`,
    [
      contactId,
      input.label ?? null,
      input.addressLine1,
      input.addressLine2 ?? null,
      input.city,
      input.state,
      input.zip,
      input.deliveryZone ?? null,
      input.siteNotes ?? null
    ]
  );

  const site = result.rows[0];
  await createActivity({
    contactId,
    relatedType: "service_site",
    relatedId: site.id,
    activityType: "site.created",
    title: "Service site added",
    body: `${site.label}: ${site.address_line_1}, ${site.city}, ${site.state}`,
    actorUserId: actorUserId ?? null,
    metadata: { deliveryZone: site.delivery_zone }
  });

  return site;
}

export async function addNote(contactId: string, body: string, actorUserId?: string | null) {
  return createActivity({
    contactId,
    relatedType: "note",
    activityType: "note.created",
    title: "Note added",
    body,
    actorUserId: actorUserId ?? null
  });
}
