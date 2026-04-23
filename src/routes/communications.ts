import { Router } from "express";
import { z } from "zod";
import { getConversation, listConversations, markConversationRead } from "../services/conversation_service";
import {
  normalizeInboundSmsByProvider,
  normalizeSmsStatusByProvider
} from "../services/integration_service";
import { logInboundSms, sendOutboundSms, updateSmsStatus } from "../services/message_service";
import type { SupportedProviderName } from "../services/message_provider";

export const conversationsV2Router = Router();
export const smsWebhooksRouter = Router();
export const providerSmsWebhooksRouter = Router({ mergeParams: true });

const outboundMessageSchema = z.object({
  body: z.string().min(1),
  mediaUrls: z.array(z.string().url()).optional(),
  sentByUserId: z.string().uuid().nullable().optional()
});

const inboundSmsSchema = z.object({
  fromNumber: z.string().optional(),
  toNumber: z.string().optional(),
  body: z.string().optional(),
  providerMessageId: z.string().nullable().optional(),
  mediaCount: z.coerce.number().optional(),
  From: z.string().optional(),
  To: z.string().optional(),
  Body: z.string().optional(),
  MessageSid: z.string().optional(),
  NumMedia: z.coerce.number().optional()
});

const smsStatusSchema = z.object({
  providerMessageId: z.string().optional(),
  deliveryStatus: z.string().optional(),
  MessageSid: z.string().optional(),
  MessageStatus: z.string().optional()
});

const providerParamSchema = z.object({
  provider: z.enum(["twilio", "ringcentral", "telnyx", "generic_webhook"])
});

conversationsV2Router.get("/", async (_req, res, next) => {
  try {
    res.json(await listConversations());
  } catch (error) {
    next(error);
  }
});

conversationsV2Router.get("/:id", async (req, res, next) => {
  try {
    const detail = await getConversation(req.params.id);
    if (!detail) {
      return res.status(404).json({ error: "Conversation not found" });
    }

    await markConversationRead(req.params.id);
    res.json(detail);
  } catch (error) {
    next(error);
  }
});

conversationsV2Router.post("/:id/messages", async (req, res, next) => {
  try {
    const payload = outboundMessageSchema.parse(req.body);
    const message = await sendOutboundSms({
      conversationId: req.params.id,
      body: payload.body,
      mediaUrls: payload.mediaUrls ?? [],
      sentByUserId: payload.sentByUserId ?? null
    });

    if (!message) {
      return res.status(404).json({ error: "Conversation not found" });
    }

    res.status(201).json(message);
  } catch (error) {
    next(error);
  }
});

smsWebhooksRouter.post("/inbound", async (req, res, next) => {
  try {
    const payload = inboundSmsSchema.parse(req.body);
    const fromNumber = payload.fromNumber || payload.From;
    const toNumber = payload.toNumber || payload.To;

    if (!fromNumber || !toNumber) {
      return res.status(400).json({ error: "fromNumber and toNumber are required" });
    }

    const result = await logInboundSms({
      fromNumber,
      toNumber,
      body: payload.body ?? payload.Body ?? "",
      providerName: "generic_webhook",
      providerMessageId: payload.providerMessageId ?? payload.MessageSid ?? null,
      providerConversationId: null,
      mediaCount: payload.mediaCount ?? payload.NumMedia ?? 0
    });

    res.status(201).json(result);
  } catch (error) {
    next(error);
  }
});

smsWebhooksRouter.post("/status", async (req, res, next) => {
  try {
    const payload = smsStatusSchema.parse(req.body);
    const providerMessageId = payload.providerMessageId || payload.MessageSid;
    const deliveryStatus = payload.deliveryStatus || payload.MessageStatus;

    if (!providerMessageId || !deliveryStatus) {
      return res.status(400).json({ error: "providerMessageId and deliveryStatus are required" });
    }

    const message = await updateSmsStatus({
      providerName: "generic_webhook",
      providerMessageId,
      deliveryStatus
    });
    res.json(message ?? { ok: true, ignored: true });
  } catch (error) {
    next(error);
  }
});

providerSmsWebhooksRouter.post("/inbound", async (req, res, next) => {
  try {
    const { provider } = providerParamSchema.parse(req.params);
    const normalized = await normalizeInboundSmsByProvider(provider as SupportedProviderName, req.body, req.headers);
    const result = await logInboundSms({
      ...normalized,
      providerName: provider as SupportedProviderName
    });
    res.status(201).json(result);
  } catch (error) {
    next(error);
  }
});

providerSmsWebhooksRouter.post("/status", async (req, res, next) => {
  try {
    const { provider } = providerParamSchema.parse(req.params);
    const normalized = await normalizeSmsStatusByProvider(provider as SupportedProviderName, req.body, req.headers);
    const result = await updateSmsStatus({
      ...normalized,
      providerName: provider as SupportedProviderName
    });
    res.json(result ?? { ok: true, ignored: true });
  } catch (error) {
    next(error);
  }
});
