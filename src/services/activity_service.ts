import { query } from "../lib/db";

export type ActivityInput = {
  contactId: string;
  relatedType: string;
  relatedId?: string | null;
  activityType: string;
  title: string;
  body?: string | null;
  actorUserId?: string | null;
  metadata?: Record<string, unknown>;
};

export async function createActivity(input: ActivityInput) {
  const result = await query(
    `INSERT INTO activities
     (contact_id, related_type, related_id, activity_type, title, body, actor_user_id, metadata_json)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
     RETURNING *`,
    [
      input.contactId,
      input.relatedType,
      input.relatedId ?? null,
      input.activityType,
      input.title,
      input.body ?? null,
      input.actorUserId ?? null,
      JSON.stringify(input.metadata ?? {})
    ]
  );

  return result.rows[0];
}

export async function listContactTimeline(contactId: string) {
  const result = await query(
    `SELECT a.*, u.full_name AS actor_name
     FROM activities a
     LEFT JOIN users u ON u.id = a.actor_user_id
     WHERE a.contact_id = $1
     ORDER BY a.created_at DESC
     LIMIT 200`,
    [contactId]
  );

  return result.rows;
}
