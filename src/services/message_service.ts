import { query } from "../lib/db";
import { normalizePhone } from "../lib/normalizePhone";
import { createActivity } from "./activity_service";
import { ensureConversation } from "./conversation_service";
import { findOrCreateLeadShellByPhone } from "./contact_service";
import { sendProviderSms } from "./integration_service";

export async function logInboundSms(input: {
  fromNumber: string;
  toNumber: string;
  body: string;
  providerMessageId?: string | null;
  mediaCount?: number;
}) {
  const contact = await findOrCreateLeadShellByPhone(input.fromNumber, "inbound_sms");
  const conversation = await ensureConversation(contact.id, contact.assigned_user_id, "sms");

  const result = await query(
    `INSERT INTO messages
     (conversation_id, contact_id, direction, channel, provider_message_id, body, media_count, delivery_status)
     VALUES ($1, $2, 'inbound', 'sms', $3, $4, $5, 'received')
     ON CONFLICT (provider_message_id) DO UPDATE
       SET delivery_status = EXCLUDED.delivery_status
     RETURNING *`,
    [
      conversation.id,
      contact.id,
      input.providerMessageId ?? null,
      input.body,
      input.mediaCount ?? 0
    ]
  );

  const message = result.rows[0];
  await query(
    `UPDATE conversations
     SET last_message_at = $2,
         unread_count = unread_count + 1,
         status = 'open'
     WHERE id = $1`,
    [conversation.id, message.created_at]
  );

  await createActivity({
    contactId: contact.id,
    relatedType: "message",
    relatedId: message.id,
    activityType: "message.inbound",
    title: "Inbound text received",
    body: input.body,
    metadata: {
      providerMessageId: input.providerMessageId ?? null,
      fromNumber: normalizePhone(input.fromNumber),
      toNumber: normalizePhone(input.toNumber)
    }
  });

  return { contact, conversation, message };
}

export async function sendOutboundSms(input: {
  conversationId: string;
  body: string;
  sentByUserId?: string | null;
}) {
  const conversation = (
    await query(
      `SELECT conv.*, c.mobile_phone, c.display_name
       FROM conversations conv
       JOIN contacts c ON c.id = conv.contact_id
       WHERE conv.id = $1
       LIMIT 1`,
      [input.conversationId]
    )
  ).rows[0];

  if (!conversation) {
    return null;
  }

  const providerResult = await sendProviderSms(conversation.mobile_phone, input.body);
  const result = await query(
    `INSERT INTO messages
     (conversation_id, contact_id, direction, channel, provider_message_id, body, media_count, delivery_status, sent_by_user_id)
     VALUES ($1, $2, 'outbound', 'sms', $3, $4, 0, $5, $6)
     RETURNING *`,
    [
      conversation.id,
      conversation.contact_id,
      providerResult.providerMessageId,
      input.body,
      providerResult.deliveryStatus,
      input.sentByUserId ?? null
    ]
  );

  const message = result.rows[0];
  await query(
    `UPDATE conversations
     SET last_message_at = $2,
         unread_count = 0,
         status = 'open'
     WHERE id = $1`,
    [conversation.id, message.created_at]
  );

  await createActivity({
    contactId: conversation.contact_id,
    relatedType: "message",
    relatedId: message.id,
    activityType: "message.outbound",
    title: "Outbound text sent",
    body: input.body,
    actorUserId: input.sentByUserId ?? null,
    metadata: {
      provider: providerResult.provider,
      providerMessageId: providerResult.providerMessageId
    }
  });

  return message;
}

export async function updateSmsStatus(providerMessageId: string, deliveryStatus: string) {
  const result = await query(
    `UPDATE messages
     SET delivery_status = $2
     WHERE provider_message_id = $1
     RETURNING *`,
    [providerMessageId, deliveryStatus]
  );

  const message = result.rows[0];
  if (message) {
    await createActivity({
      contactId: message.contact_id,
      relatedType: "message",
      relatedId: message.id,
      activityType: "message.status",
      title: "Text delivery updated",
      body: `Delivery status changed to ${deliveryStatus}.`,
      metadata: { providerMessageId }
    });
  }

  return message ?? null;
}
