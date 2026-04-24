import { markQuoteViewed } from "./quote_service";

export async function getQuotePdfHook(quoteId: string, actorUserId?: string | null) {
  const quote = await markQuoteViewed(quoteId, actorUserId ?? null);
  if (!quote) {
    return null;
  }

  // TODO(pdf): replace with real PDF generation and attachment persistence.
  return {
    quoteId,
    status: "placeholder",
    url: `/quotes/${quoteId}/pdf`,
    message: "PDF generation hook is ready for a renderer service."
  };
}
