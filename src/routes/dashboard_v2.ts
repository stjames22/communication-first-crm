import { Router } from "express";
import { query } from "../lib/db";

export const dashboardV2Router = Router();

dashboardV2Router.get("/", async (_req, res, next) => {
  try {
    const [
      unreadTexts,
      missedCalls,
      newLeads,
      quotesAwaitingFollowUp,
      tasksDueToday,
      quotesAwaitingFollowUpList,
      recentActivity
    ] = await Promise.all([
      query("SELECT COALESCE(SUM(unread_count), 0)::int AS count FROM conversations WHERE channel_type = 'sms'"),
      query("SELECT COUNT(*)::int AS count FROM calls WHERE status IN ('missed', 'no-answer')"),
      query("SELECT COUNT(*)::int AS count FROM contacts WHERE status IN ('lead', 'new_lead')"),
      query("SELECT COUNT(*)::int AS count FROM quotes WHERE status IN ('sent', 'viewed', 'follow_up_due')"),
      query(
        `SELECT COUNT(*)::int AS count
         FROM tasks
         WHERE status != 'completed'
           AND due_at >= date_trunc('day', NOW())
           AND due_at < date_trunc('day', NOW()) + INTERVAL '1 day'`
      ),
      query(
        `SELECT
           q.id,
           q.contact_id,
           q.quote_number,
           q.title,
           q.status,
           q.sent_at,
           q.grand_total,
           c.display_name,
           c.mobile_phone,
           c.email,
           COALESCE(q.created_by_user_id, c.assigned_user_id) AS assigned_user_id,
           u.full_name AS assigned_user_name,
           t.id AS follow_up_task_id,
           t.due_at AS follow_up_due_at
         FROM quotes q
         JOIN contacts c ON c.id = q.contact_id
         LEFT JOIN users u ON u.id = COALESCE(q.created_by_user_id, c.assigned_user_id)
         LEFT JOIN LATERAL (
           SELECT *
           FROM tasks
           WHERE related_type = 'quote'
             AND related_id = q.id
             AND status = 'open'
           ORDER BY due_at ASC NULLS LAST
           LIMIT 1
         ) t ON TRUE
         WHERE q.status IN ('sent', 'viewed', 'follow_up_due')
         ORDER BY
           COALESCE(t.due_at, q.sent_at, q.updated_at) ASC NULLS LAST,
           q.updated_at DESC
         LIMIT 12`
      ),
      query(
        `SELECT a.*, c.display_name
         FROM activities a
         JOIN contacts c ON c.id = a.contact_id
         ORDER BY a.created_at DESC
         LIMIT 12`
      )
    ]);

    res.json({
      metrics: {
        unreadTexts: unreadTexts.rows[0].count,
        missedCalls: missedCalls.rows[0].count,
        newLeads: newLeads.rows[0].count,
        quotesAwaitingFollowUp: quotesAwaitingFollowUp.rows[0].count,
        tasksDueToday: tasksDueToday.rows[0].count
      },
      quotesAwaitingFollowUp: quotesAwaitingFollowUpList.rows,
      recentActivity: recentActivity.rows
    });
  } catch (error) {
    next(error);
  }
});
