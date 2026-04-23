import { Router } from "express";
import { z } from "zod";
import { normalizeInboundCallByProvider } from "../services/integration_service";
import {
  listCalls,
  logInboundCall,
  setCallDisposition,
  startOutboundCall,
  updateCallStatus
} from "../services/call_service";
import type { SupportedProviderName } from "../services/message_provider";

export const callsV2Router = Router();
export const callWebhooksRouter = Router();
export const providerCallWebhooksRouter = Router({ mergeParams: true });

const outboundCallSchema = z.object({
  contactId: z.string().uuid(),
  toNumber: z.string().nullable().optional(),
  assignedUserId: z.string().uuid().nullable().optional()
});

const inboundCallSchema = z.object({
  fromNumber: z.string().optional(),
  toNumber: z.string().optional(),
  providerCallId: z.string().nullable().optional(),
  status: z.string().nullable().optional(),
  From: z.string().optional(),
  To: z.string().optional(),
  CallSid: z.string().optional(),
  CallStatus: z.string().optional()
});

const callStatusSchema = z.object({
  providerCallId: z.string().optional(),
  status: z.string().nullable().optional(),
  durationSeconds: z.coerce.number().nullable().optional(),
  recordingUrl: z.string().nullable().optional(),
  voicemailUrl: z.string().nullable().optional(),
  CallSid: z.string().optional(),
  CallStatus: z.string().optional(),
  CallDuration: z.coerce.number().optional(),
  RecordingUrl: z.string().optional()
});

const dispositionSchema = z.object({
  disposition: z.string().min(1),
  notes: z.string().nullable().optional(),
  actorUserId: z.string().uuid().nullable().optional()
});

const providerParamSchema = z.object({
  provider: z.enum(["twilio", "ringcentral", "telnyx", "generic_webhook"])
});

callsV2Router.get("/", async (_req, res, next) => {
  try {
    res.json(await listCalls());
  } catch (error) {
    next(error);
  }
});

callsV2Router.post("/outbound", async (req, res, next) => {
  try {
    const payload = outboundCallSchema.parse(req.body);
    const call = await startOutboundCall(payload);
    if (!call) {
      return res.status(404).json({ error: "Contact not found" });
    }
    res.status(201).json(call);
  } catch (error) {
    next(error);
  }
});

callsV2Router.post("/:id/disposition", async (req, res, next) => {
  try {
    const payload = dispositionSchema.parse(req.body);
    const call = await setCallDisposition(req.params.id, payload.disposition, payload.notes ?? null, payload.actorUserId ?? null);
    if (!call) {
      return res.status(404).json({ error: "Call not found" });
    }
    res.json(call);
  } catch (error) {
    next(error);
  }
});

callWebhooksRouter.post("/inbound", async (req, res, next) => {
  try {
    const payload = inboundCallSchema.parse(req.body);
    const fromNumber = payload.fromNumber || payload.From;
    const toNumber = payload.toNumber || payload.To;

    if (!fromNumber || !toNumber) {
      return res.status(400).json({ error: "fromNumber and toNumber are required" });
    }

    const call = await logInboundCall({
      fromNumber,
      toNumber,
      providerName: "generic_webhook",
      providerCallId: payload.providerCallId ?? payload.CallSid ?? null,
      providerConversationId: null,
      status: payload.status ?? payload.CallStatus ?? null
    });

    res.status(201).json(call);
  } catch (error) {
    next(error);
  }
});

callWebhooksRouter.post("/status", async (req, res, next) => {
  try {
    const payload = callStatusSchema.parse(req.body);
    const providerCallId = payload.providerCallId || payload.CallSid;

    if (!providerCallId) {
      return res.status(400).json({ error: "providerCallId is required" });
    }

    const call = await updateCallStatus({
      providerName: "generic_webhook",
      providerCallId,
      status: payload.status ?? payload.CallStatus ?? null,
      durationSeconds: payload.durationSeconds ?? payload.CallDuration ?? null,
      recordingUrl: payload.recordingUrl ?? payload.RecordingUrl ?? null,
      voicemailUrl: payload.voicemailUrl ?? null
    });

    res.json(call ?? { ok: true, ignored: true });
  } catch (error) {
    next(error);
  }
});

providerCallWebhooksRouter.post("/inbound", async (req, res, next) => {
  try {
    const { provider } = providerParamSchema.parse(req.params);
    const normalized = await normalizeInboundCallByProvider(provider as SupportedProviderName, req.body, req.headers);
    const call = await logInboundCall({
      fromNumber: normalized.fromNumber,
      toNumber: normalized.toNumber,
      providerName: provider as SupportedProviderName,
      providerCallId: normalized.providerCallId ?? null,
      providerConversationId: normalized.providerConversationId ?? null,
      status: normalized.status ?? null
    });
    res.status(201).json(call);
  } catch (error) {
    next(error);
  }
});

providerCallWebhooksRouter.post("/status", async (req, res, next) => {
  try {
    const { provider } = providerParamSchema.parse(req.params);
    const normalized = await normalizeInboundCallByProvider(provider as SupportedProviderName, req.body, req.headers);
    if (!normalized.providerCallId) {
      return res.status(400).json({ error: "providerCallId is required" });
    }

    const call = await updateCallStatus({
      providerName: provider as SupportedProviderName,
      providerCallId: normalized.providerCallId,
      providerConversationId: normalized.providerConversationId ?? null,
      status: normalized.status ?? null,
      durationSeconds: normalized.durationSeconds ?? null,
      recordingUrl: normalized.recordingUrl ?? null,
      voicemailUrl: normalized.voicemailUrl ?? null
    });
    res.json(call ?? { ok: true, ignored: true });
  } catch (error) {
    next(error);
  }
});
