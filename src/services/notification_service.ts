import { sendProviderSms } from "./integration_service";

export async function sendQuoteSmsNotification(input: {
  toNumber: string;
  quoteNumber: string;
  quoteUrl: string;
}) {
  const body = `Your quote ${input.quoteNumber} is ready: ${input.quoteUrl}`;
  const result = await sendProviderSms({
    toNumber: input.toNumber,
    body
  });

  return {
    ...result,
    body
  };
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

export async function notifyAssignedUser(input: {
  assignedUserId?: string | null;
  contactId: string;
  quoteId: string;
  notificationType: string;
  title: string;
  body: string;
}) {
  // TODO(integration): route this through in-app notifications, email, or chat.
  return {
    provider: "mock_internal_notification",
    deliveryStatus: "queued",
    providerNotificationId: `mock-notification-${Date.now()}`,
    assignedUserId: input.assignedUserId ?? null,
    contactId: input.contactId,
    quoteId: input.quoteId,
    notificationType: input.notificationType,
    title: input.title,
    body: input.body
  };
}
