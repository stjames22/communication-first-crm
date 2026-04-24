import { query } from "../lib/db";
import { createActivity } from "./activity_service";
import { notifyAssignedUser } from "./notification_service";
import { createQuoteFollowUpTask } from "./task_service";

export type QuoteAutomationEvent =
  | "quote_sent"
  | "quote_viewed_no_response"
  | "quote_not_viewed_after_24h"
  | "quote_accepted"
  | "quote_declined";

export async function runQuoteAutomationHook(event: QuoteAutomationEvent, quote: any, actorUserId?: string | null) {
  switch (event) {
    case "quote_sent":
      return createQuoteFollowUpTask({
        contactId: quote.contact_id,
        quoteId: quote.id,
        assignedUserId: quote.created_by_user_id ?? quote.assigned_user_id ?? null,
        sentAt: quote.sent_at ?? new Date()
      });
    case "quote_viewed_no_response":
      return notifyAssignedUser({
        assignedUserId: quote.created_by_user_id ?? quote.assigned_user_id ?? null,
        contactId: quote.contact_id,
        quoteId: quote.id,
        notificationType: event,
        title: "Quote/proposal viewed",
        body: `${quote.quote_number} was viewed and has not received a response yet.`
      });
    case "quote_not_viewed_after_24h":
      await createQuoteFollowUpTask({
        contactId: quote.contact_id,
        quoteId: quote.id,
        assignedUserId: quote.created_by_user_id ?? quote.assigned_user_id ?? null,
        sentAt: quote.sent_at ?? new Date()
      });
      return notifyAssignedUser({
        assignedUserId: quote.created_by_user_id ?? quote.assigned_user_id ?? null,
        contactId: quote.contact_id,
        quoteId: quote.id,
        notificationType: event,
        title: "Quote/proposal not viewed",
        body: `${quote.quote_number} has not been viewed after 24 hours.`
      });
    case "quote_accepted":
      return createActivity({
        contactId: quote.contact_id,
        relatedType: "quote",
        relatedId: quote.id,
        activityType: "quote_next_step",
        title: "Next step needed",
        body: `${quote.quote_number} was accepted. Prepare the next customer step.`,
        actorUserId: actorUserId ?? null,
        metadata: { automationHook: event }
      });
    case "quote_declined":
      return createActivity({
        contactId: quote.contact_id,
        relatedType: "quote",
        relatedId: quote.id,
        activityType: "quote_closed_lost",
        title: "Quote/proposal closed lost",
        body: `${quote.quote_number} was declined.`,
        actorUserId: actorUserId ?? null,
        metadata: { automationHook: event }
      });
  }
}

export async function runQuoteNotViewedAfter24hHook() {
  const result = await query(
    `SELECT q.*, c.assigned_user_id
     FROM quotes q
     JOIN contacts c ON c.id = q.contact_id
     WHERE q.status = 'sent'
       AND q.sent_at IS NOT NULL
       AND q.sent_at <= NOW() - INTERVAL '24 hours'
       AND NOT EXISTS (
         SELECT 1
         FROM activities a
         WHERE a.related_type = 'quote'
           AND a.related_id = q.id
           AND a.activity_type = 'quote_not_viewed_after_24h'
       )
     LIMIT 100`
  );

  const processed = [];
  for (const quote of result.rows) {
    await query("UPDATE quotes SET status = 'follow_up_due', updated_at = NOW() WHERE id = $1", [quote.id]);
    await createActivity({
      contactId: quote.contact_id,
      relatedType: "quote",
      relatedId: quote.id,
      activityType: "quote_not_viewed_after_24h",
      title: "Quote/proposal not viewed after 24 hours",
      body: `${quote.quote_number} has not been viewed after 24 hours.`,
      actorUserId: null,
      metadata: { automationHook: "quote_not_viewed_after_24h" }
    });
    await runQuoteAutomationHook("quote_not_viewed_after_24h", quote);
    processed.push(quote.id);
  }

  return processed;
}
