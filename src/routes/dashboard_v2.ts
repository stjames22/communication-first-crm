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
      recentActivity
    ] = await Promise.all([
      query("SELECT COALESCE(SUM(unread_count), 0)::int AS count FROM conversations WHERE channel_type = 'sms'"),
      query("SELECT COUNT(*)::int AS count FROM calls WHERE status IN ('missed', 'no-answer')"),
      query("SELECT COUNT(*)::int AS count FROM contacts WHERE status IN ('lead', 'new_lead')"),
      query("SELECT COUNT(*)::int AS count FROM quotes WHERE status IN ('sent', 'draft')"),
      query(
        `SELECT COUNT(*)::int AS count
         FROM tasks
         WHERE status != 'completed'
           AND due_at >= date_trunc('day', NOW())
           AND due_at < date_trunc('day', NOW()) + INTERVAL '1 day'`
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
      recentActivity: recentActivity.rows
    });
  } catch (error) {
    next(error);
  }
});
