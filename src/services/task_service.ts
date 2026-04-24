import { query } from "../lib/db";
import { createActivity } from "./activity_service";

export async function listTasks() {
  const result = await query(
    `SELECT t.*, c.display_name, c.mobile_phone, u.full_name AS assigned_user_name
     FROM tasks t
     JOIN contacts c ON c.id = t.contact_id
     LEFT JOIN users u ON u.id = t.assigned_user_id
     ORDER BY
       CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END,
       t.due_at ASC NULLS LAST,
       t.created_at DESC
     LIMIT 200`
  );

  return result.rows;
}

export async function createTask(input: {
  contactId: string;
  assignedUserId?: string | null;
  relatedType?: string | null;
  relatedId?: string | null;
  title: string;
  dueAt?: string | null;
  status?: string | null;
  priority?: string | null;
}) {
  const result = await query(
    `INSERT INTO tasks (contact_id, assigned_user_id, related_type, related_id, title, due_at, status, priority)
     VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, 'open'), COALESCE($8, 'normal'))
     RETURNING *`,
    [
      input.contactId,
      input.assignedUserId ?? null,
      input.relatedType ?? null,
      input.relatedId ?? null,
      input.title,
      input.dueAt ?? null,
      input.status ?? null,
      input.priority ?? null
    ]
  );

  const task = result.rows[0];
  await createActivity({
    contactId: task.contact_id,
    relatedType: "task",
    relatedId: task.id,
    activityType: "task.created",
    title: "Task created",
    body: task.title,
    actorUserId: input.assignedUserId ?? null,
    metadata: { dueAt: task.due_at, priority: task.priority }
  });

  return task;
}

export async function createQuoteFollowUpTask(input: {
  contactId: string;
  quoteId: string;
  assignedUserId?: string | null;
  sentAt: string | Date;
}) {
  const existing = (
    await query(
      `SELECT *
       FROM tasks
       WHERE related_type = 'quote'
         AND related_id = $1
         AND title = 'Follow up on quote/proposal'
         AND status = 'open'
       LIMIT 1`,
      [input.quoteId]
    )
  ).rows[0];

  if (existing) {
    return existing;
  }

  const sentAt = new Date(input.sentAt);
  const dueAt = new Date(sentAt.getTime() + 24 * 60 * 60 * 1000).toISOString();
  return createTask({
    contactId: input.contactId,
    assignedUserId: input.assignedUserId ?? null,
    relatedType: "quote",
    relatedId: input.quoteId,
    title: "Follow up on quote/proposal",
    dueAt,
    status: "open",
    priority: "normal"
  });
}

export async function markQuoteFollowUpTasksCompleted(input: {
  quoteId: string;
  actorUserId?: string | null;
}) {
  const result = await query(
    `UPDATE tasks
     SET status = 'completed'
     WHERE related_type = 'quote'
       AND related_id = $1
       AND status != 'completed'
     RETURNING *`,
    [input.quoteId]
  );

  for (const task of result.rows) {
    await createActivity({
      contactId: task.contact_id,
      relatedType: "task",
      relatedId: task.id,
      activityType: "task_completed",
      title: "Task completed",
      body: task.title,
      actorUserId: input.actorUserId ?? task.assigned_user_id ?? null,
      metadata: { quoteId: input.quoteId }
    });
  }

  return result.rows;
}
