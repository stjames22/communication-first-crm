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
  title: string;
  dueAt?: string | null;
  status?: string | null;
  priority?: string | null;
}) {
  const result = await query(
    `INSERT INTO tasks (contact_id, assigned_user_id, title, due_at, status, priority)
     VALUES ($1, $2, $3, $4, COALESCE($5, 'open'), COALESCE($6, 'normal'))
     RETURNING *`,
    [
      input.contactId,
      input.assignedUserId ?? null,
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
