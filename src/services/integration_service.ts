import { normalizePhone } from "../lib/normalizePhone";

export type ProviderMessageResult = {
  providerMessageId: string | null;
  deliveryStatus: string;
  provider: "mock" | "twilio" | "ringcentral";
};

export type ProviderCallResult = {
  providerCallId: string | null;
  status: string;
  provider: "mock" | "twilio" | "ringcentral";
};

export async function sendProviderSms(toNumber: string, body: string): Promise<ProviderMessageResult> {
  // TODO(integration): Replace this mock adapter with Twilio or RingCentral.
  // Keep provider IDs in provider_message_id only; never reuse them as internal IDs.
  return {
    providerMessageId: `mock-msg-${Date.now()}-${normalizePhone(toNumber).replace(/\D/g, "")}`,
    deliveryStatus: body.length > 0 ? "sent" : "failed",
    provider: "mock"
  };
}

export async function startProviderOutboundCall(toNumber: string): Promise<ProviderCallResult> {
  // TODO(integration): Connect click-to-call through Twilio Voice or RingCentral.
  return {
    providerCallId: `mock-call-${Date.now()}-${normalizePhone(toNumber).replace(/\D/g, "")}`,
    status: "queued",
    provider: "mock"
  };
}

export function getIntegrationStatus() {
  return {
    sms: {
      activeProvider: "mock",
      readyFor: ["twilio", "ringcentral"],
      note: "Provider IDs are stored separately from internal IDs."
    },
    voice: {
      activeProvider: "mock",
      readyFor: ["twilio", "ringcentral"],
      note: "Outbound call requests go through integration_service."
    }
  };
}
