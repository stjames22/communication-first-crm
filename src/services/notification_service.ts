import { sendProviderSms } from "./integration_service";

export async function sendQuoteSmsNotification(input: {
  toNumber: string;
  quoteNumber: string;
  quoteUrl: string;
}) {
  return sendProviderSms({
    toNumber: input.toNumber,
    body: `Your quote ${input.quoteNumber} is ready: ${input.quoteUrl}`
  });
}

export async function sendQuoteEmailNotification(input: {
  toEmail: string;
  quoteNumber: string;
  quoteUrl: string;
}) {
  // TODO(integration): wire to transactional email provider.
  return {
    provider: "mock_email",
    deliveryStatus: "queued",
    providerMessageId: `mock-email-${Date.now()}`,
    toEmail: input.toEmail,
    subject: `Your quote ${input.quoteNumber}`
  };
}
