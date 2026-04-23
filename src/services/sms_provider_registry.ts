import { normalizePhone } from "../lib/normalizePhone";
import type {
  MessageProviderAdapter,
  NormalizedDeliveryStatus,
  NormalizedInboundCall,
  NormalizedInboundMessage,
  ProviderConfig,
  SendSmsInput,
  SendSmsResult,
  SupportedProviderName,
  WebhookHeaders
} from "./message_provider";

function makePlaceholderSendResult(providerName: SupportedProviderName, input: SendSmsInput): SendSmsResult {
  const normalizedTo = normalizePhone(input.to).replace(/\D/g, "");
  return {
    providerName,
    providerMessageId: `${providerName}-msg-${Date.now()}-${normalizedTo}`,
    providerConversationId: `${providerName}-conv-${normalizedTo}`,
    deliveryStatus: input.body ? "sent" : "failed"
  };
}

function requireString(value: unknown, field: string) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} is required`);
  }

  return value;
}

function asObject(value: unknown) {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function baseNormalizeInboundMessage(payload: unknown, fieldMap: Record<string, string>): NormalizedInboundMessage {
  const data = asObject(payload);
  const fromNumber = requireString(data[fieldMap.fromNumber], "fromNumber");
  const toNumber = requireString(data[fieldMap.toNumber], "toNumber");
  return {
    fromNumber,
    toNumber,
    body: String(data[fieldMap.body] ?? ""),
    mediaCount: Number(data[fieldMap.mediaCount] ?? 0),
    providerMessageId: typeof data[fieldMap.providerMessageId] === "string" ? String(data[fieldMap.providerMessageId]) : null,
    providerConversationId:
      typeof data[fieldMap.providerConversationId] === "string" ? String(data[fieldMap.providerConversationId]) : null
  };
}

function baseNormalizeDeliveryStatus(payload: unknown, fieldMap: Record<string, string>): NormalizedDeliveryStatus {
  const data = asObject(payload);
  return {
    providerMessageId: requireString(data[fieldMap.providerMessageId], "providerMessageId"),
    providerConversationId:
      typeof data[fieldMap.providerConversationId] === "string" ? String(data[fieldMap.providerConversationId]) : null,
    deliveryStatus: requireString(data[fieldMap.deliveryStatus], "deliveryStatus")
  };
}

function baseNormalizeInboundCall(payload: unknown, fieldMap: Record<string, string>): NormalizedInboundCall {
  const data = asObject(payload);
  return {
    fromNumber: requireString(data[fieldMap.fromNumber], "fromNumber"),
    toNumber: requireString(data[fieldMap.toNumber], "toNumber"),
    providerCallId: typeof data[fieldMap.providerCallId] === "string" ? String(data[fieldMap.providerCallId]) : null,
    providerConversationId:
      typeof data[fieldMap.providerConversationId] === "string" ? String(data[fieldMap.providerConversationId]) : null,
    status: typeof data[fieldMap.status] === "string" ? String(data[fieldMap.status]) : null,
    durationSeconds: data[fieldMap.durationSeconds] != null ? Number(data[fieldMap.durationSeconds]) : null,
    recordingUrl: typeof data[fieldMap.recordingUrl] === "string" ? String(data[fieldMap.recordingUrl]) : null,
    voicemailUrl: typeof data[fieldMap.voicemailUrl] === "string" ? String(data[fieldMap.voicemailUrl]) : null
  };
}

function validateSimpleSignature(headers: WebhookHeaders, config: ProviderConfig) {
  if (!config.webhookSigningSecret) {
    return true;
  }

  const incomingSignature = headers["x-webhook-signature"] ?? headers["x-provider-signature"];
  if (Array.isArray(incomingSignature)) {
    return incomingSignature.includes(config.webhookSigningSecret);
  }

  return incomingSignature === config.webhookSigningSecret;
}

const twilioAdapter: MessageProviderAdapter = {
  providerName: "twilio",
  async send_sms(input) {
    return makePlaceholderSendResult("twilio", input);
  },
  normalize_inbound_message(payload) {
    return baseNormalizeInboundMessage(payload, {
      fromNumber: "From",
      toNumber: "To",
      body: "Body",
      mediaCount: "NumMedia",
      providerMessageId: "MessageSid",
      providerConversationId: "MessagingServiceSid"
    });
  },
  normalize_delivery_status(payload) {
    return baseNormalizeDeliveryStatus(payload, {
      providerMessageId: "MessageSid",
      providerConversationId: "MessagingServiceSid",
      deliveryStatus: "MessageStatus"
    });
  },
  normalize_inbound_call(payload) {
    return baseNormalizeInboundCall(payload, {
      fromNumber: "From",
      toNumber: "To",
      providerCallId: "CallSid",
      providerConversationId: "ParentCallSid",
      status: "CallStatus",
      durationSeconds: "CallDuration",
      recordingUrl: "RecordingUrl",
      voicemailUrl: "VoicemailUrl"
    });
  },
  validate_webhook_signature(_payload, headers, config) {
    return validateSimpleSignature(headers, config);
  }
};

const ringCentralAdapter: MessageProviderAdapter = {
  providerName: "ringcentral",
  async send_sms(input) {
    return makePlaceholderSendResult("ringcentral", input);
  },
  normalize_inbound_message(payload) {
    return baseNormalizeInboundMessage(payload, {
      fromNumber: "fromNumber",
      toNumber: "toNumber",
      body: "body",
      mediaCount: "mediaCount",
      providerMessageId: "messageId",
      providerConversationId: "conversationId"
    });
  },
  normalize_delivery_status(payload) {
    return baseNormalizeDeliveryStatus(payload, {
      providerMessageId: "messageId",
      providerConversationId: "conversationId",
      deliveryStatus: "deliveryStatus"
    });
  },
  normalize_inbound_call(payload) {
    return baseNormalizeInboundCall(payload, {
      fromNumber: "fromNumber",
      toNumber: "toNumber",
      providerCallId: "callId",
      providerConversationId: "sessionId",
      status: "status",
      durationSeconds: "durationSeconds",
      recordingUrl: "recordingUrl",
      voicemailUrl: "voicemailUrl"
    });
  },
  validate_webhook_signature(_payload, headers, config) {
    return validateSimpleSignature(headers, config);
  }
};

const telnyxAdapter: MessageProviderAdapter = {
  providerName: "telnyx",
  async send_sms(input) {
    return makePlaceholderSendResult("telnyx", input);
  },
  normalize_inbound_message(payload) {
    const data = asObject(payload);
    const event = asObject(data.data);
    const body = asObject(event.payload);
    const from = asObject(body.from);
    const to = Array.isArray(body.to) ? asObject(body.to[0]) : asObject(body.to);
    return {
      fromNumber: requireString(from.phone_number, "fromNumber"),
      toNumber: requireString(to.phone_number, "toNumber"),
      body: String(body.text ?? ""),
      mediaCount: Array.isArray(body.media) ? body.media.length : 0,
      providerMessageId: typeof event.id === "string" ? event.id : null,
      providerConversationId: typeof body.messaging_profile_id === "string" ? body.messaging_profile_id : null
    };
  },
  normalize_delivery_status(payload) {
    const data = asObject(payload);
    const event = asObject(data.data);
    const body = asObject(event.payload);
    return {
      providerMessageId: requireString(body.id ?? event.id, "providerMessageId"),
      providerConversationId: typeof body.messaging_profile_id === "string" ? body.messaging_profile_id : null,
      deliveryStatus: requireString(body.delivery_status ?? body.status, "deliveryStatus")
    };
  },
  normalize_inbound_call(payload) {
    const data = asObject(payload);
    const event = asObject(data.data);
    const body = asObject(event.payload);
    const recordingUrls = Array.isArray(body.recording_urls) ? body.recording_urls : [];
    return {
      fromNumber: requireString(body.from, "fromNumber"),
      toNumber: requireString(body.to, "toNumber"),
      providerCallId: typeof body.call_control_id === "string" ? body.call_control_id : null,
      providerConversationId: typeof body.call_session_id === "string" ? body.call_session_id : null,
      status: typeof body.call_status === "string" ? body.call_status : null,
      durationSeconds: body.call_duration != null ? Number(body.call_duration) : null,
      recordingUrl: typeof recordingUrls[0] === "string" ? recordingUrls[0] : null,
      voicemailUrl: typeof body.voicemail_url === "string" ? body.voicemail_url : null
    };
  },
  validate_webhook_signature(_payload, headers, config) {
    return validateSimpleSignature(headers, config);
  }
};

const genericWebhookAdapter: MessageProviderAdapter = {
  providerName: "generic_webhook",
  async send_sms(input) {
    return makePlaceholderSendResult("generic_webhook", input);
  },
  normalize_inbound_message(payload) {
    return baseNormalizeInboundMessage(payload, {
      fromNumber: "fromNumber",
      toNumber: "toNumber",
      body: "body",
      mediaCount: "mediaCount",
      providerMessageId: "providerMessageId",
      providerConversationId: "providerConversationId"
    });
  },
  normalize_delivery_status(payload) {
    return baseNormalizeDeliveryStatus(payload, {
      providerMessageId: "providerMessageId",
      providerConversationId: "providerConversationId",
      deliveryStatus: "deliveryStatus"
    });
  },
  normalize_inbound_call(payload) {
    return baseNormalizeInboundCall(payload, {
      fromNumber: "fromNumber",
      toNumber: "toNumber",
      providerCallId: "providerCallId",
      providerConversationId: "providerConversationId",
      status: "status",
      durationSeconds: "durationSeconds",
      recordingUrl: "recordingUrl",
      voicemailUrl: "voicemailUrl"
    });
  },
  validate_webhook_signature(_payload, headers, config) {
    return validateSimpleSignature(headers, config);
  }
};

const providerRegistry = new Map<SupportedProviderName, MessageProviderAdapter>([
  ["twilio", twilioAdapter],
  ["ringcentral", ringCentralAdapter],
  ["telnyx", telnyxAdapter],
  ["generic_webhook", genericWebhookAdapter]
]);

export function getMessageProviderAdapter(providerName: SupportedProviderName) {
  return providerRegistry.get(providerName);
}

export function listMessageProviderAdapters() {
  return Array.from(providerRegistry.values());
}
