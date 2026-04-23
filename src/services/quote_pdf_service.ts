export function getQuotePdfHook(quoteId: string) {
  // TODO(pdf): replace with real PDF generation and attachment persistence.
  return {
    quoteId,
    status: "placeholder",
    url: `/quotes/${quoteId}/pdf`,
    message: "PDF generation hook is ready for a renderer service."
  };
}
