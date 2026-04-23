import { query } from "../lib/db";
import { normalizePhone } from "../lib/normalizePhone";
import { getMessageProviderAdapter, listMessageProviderAdapters } from "./sms_provider_registry";
import type {
  NormalizedDeliveryStatus,
  NormalizedInboundCall,
  NormalizedInboundMessage,
  ProviderConfig,
  SupportedProviderName,
  WebhookHeaders
} from "./message_provider";

export type ProviderMessageResult = {
  providerMessageId: string | null;
  providerConversationId?: string | null;
  deliveryStatus: string;
  provider: SupportedProviderName;
};

export type ProviderCallResult = {
  providerCallId: string | null;
  providerConversationId?: string | null;
  status: string;
  provider: SupportedProviderName;
};

type IntegrationSettingsRow = {
  provider_type: "sms" | "voice";
  provider_name: SupportedProviderName;
  enabled: boolean;
  settings_json: Record<string, unknown> | null;
};

export async function sendProviderSms(input: {
  toNumber: string;
  body: string;
  mediaUrls?: string[];
}): Promise<ProviderMessageResult> {
  const config = await getActiveIntegrationConfig("sms");
  const adapter = getRequiredProviderAdapter(config.providerName);
  const fromNumber = config.defaultFromNumber ?? "+10000000000";
  const result = await adapter.send_sms(
    {
      to: normalizePhone(input.toNumber),
      from: normalizePhone(fromNumber),
      body: input.body,
      mediaUrls: input.mediaUrls ?? []
    },
    config
  );

  return {
    providerMessageId: result.providerMessageId,
    providerConversationId: result.providerConversationId ?? null,
    deliveryStatus: result.deliveryStatus,
    provider: result.providerName
  };
}

export async function startProviderOutboundCall(toNumber: string): Promise<ProviderCallResult> {
  const config = await getActiveIntegrationConfig("voice");
  return {
    providerCallId: `${config.providerName}-call-${Date.now()}-${normalizePhone(toNumber).replace(/\D/g, "")}`,
    providerConversationId: `${config.providerName}-call-session-${Date.now()}`,
    status: "queued",
    provider: config.providerName
  };
}

export async function normalizeInboundSmsByProvider(
  providerName: SupportedProviderName,
  payload: unknown,
  headers: WebhookHeaders
): Promise<NormalizedInboundMessage> {
  const config = await getIntegrationConfig(providerName, "sms");
  const adapter = getRequiredProviderAdapter(providerName);
  if (!adapter.validate_webhook_signature(payload, headers, config)) {
    throw new Error("Invalid webhook signature");
  }

  return adapter.normalize_inbound_message(payload);
}

export async function normalizeSmsStatusByProvider(
  providerName: SupportedProviderName,
  payload: unknown,
  headers: WebhookHeaders
): Promise<NormalizedDeliveryStatus> {
  const config = await getIntegrationConfig(providerName, "sms");
  const adapter = getRequiredProviderAdapter(providerName);
  if (!adapter.validate_webhook_signature(payload, headers, config)) {
    throw new Error("Invalid webhook signature");
  }

  return adapter.normalize_delivery_status(payload);
}

export async function normalizeInboundCallByProvider(
  providerName: SupportedProviderName,
  payload: unknown,
  headers: WebhookHeaders
): Promise<NormalizedInboundCall> {
  const config = await getIntegrationConfig(providerName, "voice");
  const adapter = getRequiredProviderAdapter(providerName);
  if (!adapter.validate_webhook_signature(payload, headers, config)) {
    throw new Error("Invalid webhook signature");
  }

  return adapter.normalize_inbound_call(payload);
}

export async function getIntegrationStatus() {
  const settings = await loadIntegrationSettings();
  const activeSmsProvider = getEnabledProviderName(settings, "sms") ?? "generic_webhook";
  const activeVoiceProvider = getEnabledProviderName(settings, "voice") ?? "generic_webhook";
  return {
    sms: {
      activeProvider: activeSmsProvider,
      readyFor: listMessageProviderAdapters().map((adapter) => adapter.providerName),
      note: "Outbound SMS goes through the selected adapter. Provider IDs stay separate from CRM IDs."
    },
    voice: {
      activeProvider: activeVoiceProvider,
      readyFor: listMessageProviderAdapters().map((adapter) => adapter.providerName),
      note: "Inbound and outbound call events normalize through provider adapters."
    }
  };
}

export async function upsertIntegrationSetting(input: {
  providerType: "sms" | "voice";
  providerName: SupportedProviderName;
  enabled?: boolean;
  settings?: Record<string, unknown>;
}) {
  const settingsJson = {
    active_sms_provider: input.providerType === "sms" ? input.providerName : undefined,
    provider_credentials_placeholder: true,
    default_from_number: input.settings?.default_from_number ?? null,
    webhook_signing_secret_placeholder: true,
    routing_rules: input.settings?.routing_rules ?? null,
    ...input.settings
  };

  if (input.enabled) {
    await query("UPDATE integration_settings SET enabled = FALSE WHERE provider_type = $1", [input.providerType]);
  }

  const result = await query(
    `INSERT INTO integration_settings (provider_type, provider_name, enabled, settings_json)
     VALUES ($1, $2, COALESCE($3, FALSE), $4::jsonb)
     ON CONFLICT (provider_type, provider_name) DO UPDATE
       SET enabled = EXCLUDED.enabled,
           settings_json = EXCLUDED.settings_json
     RETURNING *`,
    [input.providerType, input.providerName, input.enabled ?? false, JSON.stringify(settingsJson)]
  );

  return result.rows[0];
}

async function getActiveIntegrationConfig(providerType: "sms" | "voice"): Promise<ProviderConfig> {
  const settings = await loadIntegrationSettings();
  const enabled = settings.find((row) => row.provider_type === providerType && row.enabled);
  if (enabled) {
    return rowToConfig(enabled);
  }

  return {
    providerName: "generic_webhook",
    providerType,
    defaultFromNumber: "+10000000000",
    webhookSigningSecret: null,
    credentials: {},
    routingRules: {}
  };
}

async function getIntegrationConfig(providerName: SupportedProviderName, providerType: "sms" | "voice"): Promise<ProviderConfig> {
  const settings = await loadIntegrationSettings();
  const existing = settings.find((row) => row.provider_name === providerName && row.provider_type === providerType);
  return existing
    ? rowToConfig(existing)
    : {
        providerName,
        providerType,
        defaultFromNumber: "+10000000000",
        webhookSigningSecret: null,
        credentials: {},
        routingRules: {}
      };
}

async function loadIntegrationSettings() {
  const result = await query<IntegrationSettingsRow>("SELECT * FROM integration_settings ORDER BY provider_type, provider_name");
  return result.rows;
}

function rowToConfig(row: IntegrationSettingsRow): ProviderConfig {
  const settings = row.settings_json ?? {};
  return {
    providerName: row.provider_name,
    providerType: row.provider_type,
    defaultFromNumber: typeof settings.default_from_number === "string" ? settings.default_from_number : null,
    webhookSigningSecret:
      typeof settings.webhook_signing_secret === "string" ? settings.webhook_signing_secret : null,
    credentials:
      typeof settings.provider_credentials === "object" && settings.provider_credentials !== null
        ? (settings.provider_credentials as Record<string, unknown>)
        : {},
    routingRules:
      typeof settings.routing_rules === "object" && settings.routing_rules !== null
        ? (settings.routing_rules as Record<string, unknown>)
        : {}
  };
}

function getEnabledProviderName(rows: IntegrationSettingsRow[], providerType: "sms" | "voice") {
  return rows.find((row) => row.provider_type === providerType && row.enabled)?.provider_name ?? null;
}

function getRequiredProviderAdapter(providerName: SupportedProviderName) {
  const adapter = getMessageProviderAdapter(providerName);
  if (!adapter) {
    throw new Error(`Unsupported provider: ${providerName}`);
  }

  return adapter;
}
