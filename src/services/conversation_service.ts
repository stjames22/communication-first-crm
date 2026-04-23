import { query } from "../lib/db";

export async function ensureConversation(contactId: string, assignedUserId?: string | null, channelType = "sms") {
  const existing = (
    await query(
      `SELECT *
       FROM conversations
       WHERE contact_id = $1 AND channel_type = $2 AND status != 'archived'
       ORDER BY created_at DESC
       LIMIT 1`,
      [contactId, channelType]
    )
  ).rows[0];

  if (existing) {
    return existing;
  }

  const result = await query(
    `INSERT INTO conversations (contact_id, assigned_user_id, channel_type, status)
     VALUES ($1, $2, $3, 'open')
     RETURNING *`,
    [contactId, assignedUserId ?? null, channelType]
  );

  return result.rows[0];
}

export async function listConversations() {
  const result = await query(
    `WITH last_messages AS (
       SELECT DISTINCT ON (conversation_id)
         conversation_id,
         body,
         direction,
         delivery_status,
         created_at
       FROM messages
       ORDER BY conversation_id, created_at DESC
     )
     SELECT
       conv.*,
       c.display_name,
       c.mobile_phone,
       c.email,
       c.status AS contact_status,
       u.full_name AS assigned_user_name,
       lm.body AS last_message_body,
       lm.direction AS last_message_direction,
       lm.delivery_status AS last_message_delivery_status,
       COALESCE(lm.created_at, conv.last_message_at, conv.created_at) AS sort_at,
       (
         SELECT JSON_BUILD_OBJECT('id', q.id, 'status', q.status, 'quote_number', q.quote_number, 'grand_total', q.grand_total)
         FROM quotes q
         WHERE q.contact_id = c.id
         ORDER BY q.updated_at DESC
         LIMIT 1
       ) AS latest_quote,
       (
         SELECT COALESCE(JSON_AGG(JSON_BUILD_OBJECT('id', t.id, 'name', t.name, 'color', t.color) ORDER BY t.name), '[]'::json)
         FROM contact_tags ct
         JOIN tags t ON t.id = ct.tag_id
         WHERE ct.contact_id = c.id
       ) AS tags
     FROM conversations conv
     JOIN contacts c ON c.id = conv.contact_id
     LEFT JOIN users u ON u.id = conv.assigned_user_id
     LEFT JOIN last_messages lm ON lm.conversation_id = conv.id
     ORDER BY sort_at DESC
     LIMIT 200`
  );

  return result.rows;
}

export async function getConversation(conversationId: string) {
  const conversation = (
    await query(
      `SELECT conv.*, c.display_name, c.mobile_phone, c.email, c.status AS contact_status
       FROM conversations conv
       JOIN contacts c ON c.id = conv.contact_id
       WHERE conv.id = $1
       LIMIT 1`,
      [conversationId]
    )
  ).rows[0];

  if (!conversation) {
    return null;
  }

  const [messages, sites, tags, latestQuote, tasks, recentActivity] = await Promise.all([
    query("SELECT * FROM messages WHERE conversation_id = $1 ORDER BY created_at ASC LIMIT 300", [conversationId]),
    query("SELECT * FROM service_sites WHERE contact_id = $1 ORDER BY created_at ASC", [conversation.contact_id]),
    query(
      `SELECT t.*
       FROM tags t
       JOIN contact_tags ct ON ct.tag_id = t.id
       WHERE ct.contact_id = $1
       ORDER BY t.name`,
      [conversation.contact_id]
    ),
    query("SELECT * FROM quotes WHERE contact_id = $1 ORDER BY updated_at DESC LIMIT 1", [conversation.contact_id]),
    query("SELECT * FROM tasks WHERE contact_id = $1 AND status != 'completed' ORDER BY due_at ASC NULLS LAST LIMIT 5", [
      conversation.contact_id
    ]),
    query("SELECT * FROM activities WHERE contact_id = $1 ORDER BY created_at DESC LIMIT 25", [conversation.contact_id])
  ]);

  return {
    conversation,
    messages: messages.rows,
    contactSummary: {
      id: conversation.contact_id,
      display_name: conversation.display_name,
      mobile_phone: conversation.mobile_phone,
      email: conversation.email,
      status: conversation.contact_status,
      primary_site: sites.rows[0] ?? null,
      tags: tags.rows,
      latest_quote: latestQuote.rows[0] ?? null,
      next_tasks: tasks.rows
    },
    recentActivity: recentActivity.rows
  };
}

export async function markConversationRead(conversationId: string) {
  const result = await query(
    `UPDATE conversations
     SET unread_count = 0
     WHERE id = $1
     RETURNING *`,
    [conversationId]
  );

  return result.rows[0] ?? null;
}
