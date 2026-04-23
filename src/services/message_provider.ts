export type SupportedProviderName = "twilio" | "ringcentral" | "telnyx" | "generic_webhook";

export type ProviderConfig = {
  providerName: SupportedProviderName;
  providerType: "sms" | "voice";
  defaultFromNumber?: string | null;
  webhookSigningSecret?: string | null;
  credentials?: Record<string, unknown>;
  routingRules?: Record<string, unknown>;
};

export type SendSmsInput = {
  to: string;
  from: string;
  body: string;
  mediaUrls?: string[];
};

export type SendSmsResult = {
  providerName: SupportedProviderName;
  providerMessageId: string | null;
  providerConversationId?: string | null;
  deliveryStatus: string;
};

export type NormalizedInboundMessage = {
  fromNumber: string;
  toNumber: string;
  body: string;
  mediaCount?: number;
  providerMessageId?: string | null;
  providerConversationId?: string | null;
};

export type NormalizedDeliveryStatus = {
  providerMessageId: string;
  providerConversationId?: string | null;
  deliveryStatus: string;
};

export type NormalizedInboundCall = {
  fromNumber: string;
  toNumber: string;
  providerCallId?: string | null;
  providerConversationId?: string | null;
  status?: string | null;
  durationSeconds?: number | null;
  recordingUrl?: string | null;
  voicemailUrl?: string | null;
};

export type WebhookHeaders = Record<string, string | string[] | undefined>;

export interface MessageProviderAdapter {
  readonly providerName: SupportedProviderName;
  send_sms(input: SendSmsInput, config: ProviderConfig): Promise<SendSmsResult>;
  normalize_inbound_message(payload: unknown): NormalizedInboundMessage;
  normalize_delivery_status(payload: unknown): NormalizedDeliveryStatus;
  normalize_inbound_call(payload: unknown): NormalizedInboundCall;
  validate_webhook_signature(payload: unknown, headers: WebhookHeaders, config: ProviderConfig): boolean;
}
