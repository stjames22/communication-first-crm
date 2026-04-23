import { pool, query } from "../lib/db";
import { createActivity } from "./activity_service";
import { sendQuoteEmailNotification, sendQuoteSmsNotification } from "./notification_service";

export type QuoteLineItemInput = {
  itemType?: string | null;
  name: string;
  description?: string | null;
  quantity?: number | null;
  unit?: string | null;
  unitPrice?: number | null;
  sourceReference?: string | null;
};

export type QuoteInput = {
  contactId: string;
  serviceSiteId: string;
  title: string;
  status?: string | null;
  deliveryTotal?: number | null;
  taxTotal?: number | null;
  notes?: string | null;
  createdByUserId?: string | null;
  lineItems?: QuoteLineItemInput[];
};

export async function listQuotes() {
  const result = await query(
    `SELECT q.*, c.display_name, c.mobile_phone, s.label AS site_label, s.address_line_1
     FROM quotes q
     JOIN contacts c ON c.id = q.contact_id
     JOIN service_sites s ON s.id = q.service_site_id
     ORDER BY q.updated_at DESC
     LIMIT 200`
  );

  return result.rows;
}

export async function createQuote(input: QuoteInput) {
  const client = await pool.connect();

  try {
    await client.query("BEGIN");
    const totals = calculateTotals(input.lineItems ?? [], input.deliveryTotal ?? 0, input.taxTotal ?? 0);
    const quoteNumber = await nextQuoteNumber(client);

    const quote = (
      await client.query(
        `INSERT INTO quotes
         (contact_id, service_site_id, quote_number, title, status, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, $2, $3, $4, COALESCE($5, 'draft'), $6, $7, $8, $9, $10)
         RETURNING *`,
        [
          input.contactId,
          input.serviceSiteId,
          quoteNumber,
          input.title,
          input.status ?? null,
          totals.subtotal,
          totals.deliveryTotal,
          totals.taxTotal,
          totals.grandTotal,
          input.createdByUserId ?? null
        ]
      )
    ).rows[0];

    const version = (
      await client.query(
        `INSERT INTO quote_versions
         (quote_id, version_number, pricing_snapshot_json, notes, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, 1, $2::jsonb, $3, $4, $5, $6, $7, $8)
         RETURNING *`,
        [
          quote.id,
          JSON.stringify({
            lineItems: input.lineItems ?? [],
            deliveryTotal: totals.deliveryTotal,
            manualOverrides: true
          }),
          input.notes ?? "Initial quote version",
          totals.subtotal,
          totals.deliveryTotal,
          totals.taxTotal,
          totals.grandTotal,
          input.createdByUserId ?? null
        ]
      )
    ).rows[0];

    for (const [index, lineItem] of (input.lineItems ?? []).entries()) {
      const quantity = Number(lineItem.quantity ?? 1);
      const unitPrice = Number(lineItem.unitPrice ?? 0);
      await client.query(
        `INSERT INTO quote_line_items
         (quote_version_id, item_type, name, description, quantity, unit, unit_price, total_price, sort_order, source_reference)
         VALUES ($1, COALESCE($2, 'service'), $3, $4, $5, COALESCE($6, 'each'), $7, $8, $9, $10)`,
        [
          version.id,
          lineItem.itemType ?? null,
          lineItem.name,
          lineItem.description ?? null,
          quantity,
          lineItem.unit ?? null,
          unitPrice,
          quantity * unitPrice,
          index + 1,
          lineItem.sourceReference ?? null
        ]
      );
    }

    await client.query("UPDATE quotes SET current_version_id = $2 WHERE id = $1", [quote.id, version.id]);
    await client.query("COMMIT");

    await createActivity({
      contactId: quote.contact_id,
      relatedType: "quote",
      relatedId: quote.id,
      activityType: "quote.created",
      title: "Quote created",
      body: `${quote.quote_number} was created for ${quote.title}.`,
      actorUserId: input.createdByUserId ?? null,
      metadata: { grandTotal: totals.grandTotal, version: 1 }
    });

    return getQuote(quote.id);
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

export async function getQuote(quoteId: string) {
  const quote = (
    await query(
      `SELECT q.*, c.display_name, c.mobile_phone, c.email, s.label AS site_label,
              s.address_line_1, s.city, s.state, s.zip
       FROM quotes q
       JOIN contacts c ON c.id = q.contact_id
       JOIN service_sites s ON s.id = q.service_site_id
       WHERE q.id = $1
       LIMIT 1`,
      [quoteId]
    )
  ).rows[0];

  if (!quote) {
    return null;
  }

  const versions = await query(
    `SELECT v.*,
       COALESCE(
         JSON_AGG(li ORDER BY li.sort_order ASC) FILTER (WHERE li.id IS NOT NULL),
         '[]'::json
       ) AS line_items
     FROM quote_versions v
     LEFT JOIN quote_line_items li ON li.quote_version_id = v.id
     WHERE v.quote_id = $1
     GROUP BY v.id
     ORDER BY v.version_number DESC`,
    [quoteId]
  );

  return { quote, versions: versions.rows };
}

export async function updateQuote(quoteId: string, input: { title?: string; status?: string }) {
  const result = await query(
    `UPDATE quotes
     SET title = COALESCE($2, title),
         status = COALESCE($3, status)
     WHERE id = $1
     RETURNING *`,
    [quoteId, input.title ?? null, input.status ?? null]
  );

  return result.rows[0] ?? null;
}

export async function createQuoteVersion(
  quoteId: string,
  input: {
    notes?: string | null;
    deliveryTotal?: number | null;
    taxTotal?: number | null;
    createdByUserId?: string | null;
    lineItems?: QuoteLineItemInput[];
  }
) {
  const client = await pool.connect();

  try {
    await client.query("BEGIN");
    const quote = (await client.query("SELECT * FROM quotes WHERE id = $1 LIMIT 1", [quoteId])).rows[0];
    if (!quote) {
      await client.query("ROLLBACK");
      return null;
    }

    const latestVersion = (
      await client.query("SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM quote_versions WHERE quote_id = $1", [
        quoteId
      ])
    ).rows[0];
    const versionNumber = Number(latestVersion.next_version);
    const totals = calculateTotals(input.lineItems ?? [], input.deliveryTotal ?? Number(quote.delivery_total), input.taxTotal ?? Number(quote.tax_total));

    const version = (
      await client.query(
        `INSERT INTO quote_versions
         (quote_id, version_number, pricing_snapshot_json, notes, subtotal, delivery_total, tax_total, grand_total, created_by_user_id)
         VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8, $9)
         RETURNING *`,
        [
          quoteId,
          versionNumber,
          JSON.stringify({ lineItems: input.lineItems ?? [], deliveryTotal: totals.deliveryTotal }),
          input.notes ?? null,
          totals.subtotal,
          totals.deliveryTotal,
          totals.taxTotal,
          totals.grandTotal,
          input.createdByUserId ?? null
        ]
      )
    ).rows[0];

    for (const [index, lineItem] of (input.lineItems ?? []).entries()) {
      const quantity = Number(lineItem.quantity ?? 1);
      const unitPrice = Number(lineItem.unitPrice ?? 0);
      await client.query(
        `INSERT INTO quote_line_items
         (quote_version_id, item_type, name, description, quantity, unit, unit_price, total_price, sort_order, source_reference)
         VALUES ($1, COALESCE($2, 'service'), $3, $4, $5, COALESCE($6, 'each'), $7, $8, $9, $10)`,
        [
          version.id,
          lineItem.itemType ?? null,
          lineItem.name,
          lineItem.description ?? null,
          quantity,
          lineItem.unit ?? null,
          unitPrice,
          quantity * unitPrice,
          index + 1,
          lineItem.sourceReference ?? null
        ]
      );
    }

    await client.query(
      `UPDATE quotes
       SET current_version_id = $2,
           status = CASE WHEN status = 'accepted' THEN status ELSE 'draft' END,
           subtotal = $3,
           delivery_total = $4,
           tax_total = $5,
           grand_total = $6
       WHERE id = $1`,
      [quoteId, version.id, totals.subtotal, totals.deliveryTotal, totals.taxTotal, totals.grandTotal]
    );

    await client.query("COMMIT");
    await createActivity({
      contactId: quote.contact_id,
      relatedType: "quote",
      relatedId: quote.id,
      activityType: "quote.revised",
      title: "Quote version saved",
      body: `${quote.quote_number} version ${versionNumber} was saved.`,
      actorUserId: input.createdByUserId ?? null,
      metadata: { version: versionNumber, grandTotal: totals.grandTotal }
    });

    return getQuote(quoteId);
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

export async function sendQuoteBySms(quoteId: string, actorUserId?: string | null) {
  const quoteDetail = await getQuote(quoteId);
  if (!quoteDetail) {
    return null;
  }

  const { quote } = quoteDetail;
  const result = await sendQuoteSmsNotification({
    toNumber: quote.mobile_phone,
    quoteNumber: quote.quote_number,
    quoteUrl: `/quotes/${quote.id}/pdf`
  });

  await query("UPDATE quotes SET status = 'sent', sent_at = COALESCE(sent_at, NOW()) WHERE id = $1", [quoteId]);
  await createActivity({
    contactId: quote.contact_id,
    relatedType: "quote",
    relatedId: quote.id,
    activityType: "quote.sent.sms",
    title: "Quote sent by SMS",
    body: `${quote.quote_number} was sent by text.`,
    actorUserId: actorUserId ?? null,
    metadata: result
  });

  // TODO(automation): quote sent -> create follow-up reminder.
  return result;
}

export async function sendQuoteByEmail(quoteId: string, actorUserId?: string | null) {
  const quoteDetail = await getQuote(quoteId);
  if (!quoteDetail) {
    return null;
  }

  const { quote } = quoteDetail;
  const result = await sendQuoteEmailNotification({
    toEmail: quote.email || "missing-email@example.invalid",
    quoteNumber: quote.quote_number,
    quoteUrl: `/quotes/${quote.id}/pdf`
  });

  await query("UPDATE quotes SET status = 'sent', sent_at = COALESCE(sent_at, NOW()) WHERE id = $1", [quoteId]);
  await createActivity({
    contactId: quote.contact_id,
    relatedType: "quote",
    relatedId: quote.id,
    activityType: "quote.sent.email",
    title: "Quote sent by email",
    body: `${quote.quote_number} was sent by email.`,
    actorUserId: actorUserId ?? null,
    metadata: result
  });

  return result;
}

export async function markQuoteStatus(quoteId: string, status: "accepted" | "declined", actorUserId?: string | null) {
  const result = await query(
    `UPDATE quotes
     SET status = $2,
         accepted_at = CASE WHEN $2 = 'accepted' THEN NOW() ELSE accepted_at END
     WHERE id = $1
     RETURNING *`,
    [quoteId, status]
  );

  const quote = result.rows[0];
  if (!quote) {
    return null;
  }

  await createActivity({
    contactId: quote.contact_id,
    relatedType: "quote",
    relatedId: quote.id,
    activityType: `quote.${status}`,
    title: status === "accepted" ? "Quote accepted" : "Quote declined",
    body: `${quote.quote_number} was marked ${status}.`,
    actorUserId: actorUserId ?? null,
    metadata: { status }
  });

  return quote;
}

function calculateTotals(lineItems: QuoteLineItemInput[], deliveryTotal: number, taxTotal: number) {
  const subtotal = lineItems.reduce((sum, item) => {
    const quantity = Number(item.quantity ?? 1);
    const unitPrice = Number(item.unitPrice ?? 0);
    return sum + quantity * unitPrice;
  }, 0);

  return {
    subtotal,
    deliveryTotal,
    taxTotal,
    grandTotal: subtotal + deliveryTotal + taxTotal
  };
}

async function nextQuoteNumber(client: { query: (text: string, params?: unknown[]) => Promise<{ rows: Array<{ next_quote: number }> }> }) {
  const result = await client.query("SELECT COUNT(*)::int + 1 AS next_quote FROM quotes");
  const nextQuote = Number(result.rows[0].next_quote);
  return `BBQ-${new Date().getFullYear()}-${String(nextQuote).padStart(4, "0")}`;
}
