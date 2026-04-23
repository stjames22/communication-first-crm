import { Router } from "express";
import { z } from "zod";
import { getConversation, listConversations, markConversationRead } from "../services/conversation_service";
import { logInboundSms, sendOutboundSms, updateSmsStatus } from "../services/message_service";

export const conversationsV2Router = Router();
export const smsWebhooksRouter = Router();

const outboundMessageSchema = z.object({
  body: z.string().min(1),
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
      providerMessageId: payload.providerMessageId ?? payload.MessageSid ?? null,
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

    const message = await updateSmsStatus(providerMessageId, deliveryStatus);
    res.json(message ?? { ok: true, ignored: true });
  } catch (error) {
    next(error);
  }
});
