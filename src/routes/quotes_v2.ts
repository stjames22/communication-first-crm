import { Router } from "express";
import { z } from "zod";
import { getQuotePdfHook } from "../services/quote_pdf_service";
import {
  createQuote,
  createQuoteVersion,
  getQuote,
  listQuotes,
  markQuoteStatus,
  sendQuoteByEmail,
  sendQuoteBySms,
  updateQuote
} from "../services/quote_service";

export const quotesV2Router = Router();

const lineItemSchema = z.object({
  itemType: z.string().nullable().optional(),
  name: z.string().min(1),
  description: z.string().nullable().optional(),
  quantity: z.coerce.number().nullable().optional(),
  unit: z.string().nullable().optional(),
  unitPrice: z.coerce.number().nullable().optional(),
  sourceReference: z.string().nullable().optional()
});

const quoteSchema = z.object({
  contactId: z.string().uuid(),
  serviceSiteId: z.string().uuid(),
  title: z.string().min(1),
  status: z.string().nullable().optional(),
  deliveryTotal: z.coerce.number().nullable().optional(),
  taxTotal: z.coerce.number().nullable().optional(),
  notes: z.string().nullable().optional(),
  createdByUserId: z.string().uuid().nullable().optional(),
  lineItems: z.array(lineItemSchema).optional()
});

const quotePatchSchema = z.object({
  title: z.string().optional(),
  status: z.string().optional()
});

const versionSchema = z.object({
  notes: z.string().nullable().optional(),
  deliveryTotal: z.coerce.number().nullable().optional(),
  taxTotal: z.coerce.number().nullable().optional(),
  createdByUserId: z.string().uuid().nullable().optional(),
  lineItems: z.array(lineItemSchema).optional()
});

const actorSchema = z.object({
  actorUserId: z.string().uuid().nullable().optional()
});

quotesV2Router.get("/", async (_req, res, next) => {
  try {
    res.json(await listQuotes());
  } catch (error) {
    next(error);
  }
});

quotesV2Router.post("/", async (req, res, next) => {
  try {
    const payload = quoteSchema.parse(req.body);
    res.status(201).json(await createQuote(payload));
  } catch (error) {
    next(error);
  }
});

quotesV2Router.get("/:id", async (req, res, next) => {
  try {
    const quote = await getQuote(req.params.id);
    if (!quote) {
      return res.status(404).json({ error: "Quote not found" });
    }
    res.json(quote);
  } catch (error) {
    next(error);
  }
});

quotesV2Router.patch("/:id", async (req, res, next) => {
  try {
    const payload = quotePatchSchema.parse(req.body);
    const quote = await updateQuote(req.params.id, payload);
    if (!quote) {
      return res.status(404).json({ error: "Quote not found" });
    }
    res.json(quote);
  } catch (error) {
    next(error);
  }
});

quotesV2Router.post("/:id/versions", async (req, res, next) => {
  try {
    const payload = versionSchema.parse(req.body);
    const quote = await createQuoteVersion(req.params.id, payload);
    if (!quote) {
      return res.status(404).json({ error: "Quote not found" });
    }
    res.status(201).json(quote);
  } catch (error) {
    next(error);
  }
});

quotesV2Router.get("/:id/pdf", async (req, res) => {
  res.json(getQuotePdfHook(req.params.id));
});

quotesV2Router.post("/:id/send-sms", async (req, res, next) => {
  try {
    const payload = actorSchema.parse(req.body ?? {});
    const result = await sendQuoteBySms(req.params.id, payload.actorUserId ?? null);
    if (!result) {
      return res.status(404).json({ error: "Quote not found" });
    }
    res.json(result);
  } catch (error) {
    next(error);
  }
});

quotesV2Router.post("/:id/send-email", async (req, res, next) => {
  try {
    const payload = actorSchema.parse(req.body ?? {});
    const result = await sendQuoteByEmail(req.params.id, payload.actorUserId ?? null);
    if (!result) {
      return res.status(404).json({ error: "Quote not found" });
    }
    res.json(result);
  } catch (error) {
    next(error);
  }
});

quotesV2Router.post("/:id/accept", async (req, res, next) => {
  try {
    const payload = actorSchema.parse(req.body ?? {});
    const quote = await markQuoteStatus(req.params.id, "accepted", payload.actorUserId ?? null);
    if (!quote) {
      return res.status(404).json({ error: "Quote not found" });
    }
    res.json(quote);
  } catch (error) {
    next(error);
  }
});

quotesV2Router.post("/:id/decline", async (req, res, next) => {
  try {
    const payload = actorSchema.parse(req.body ?? {});
    const quote = await markQuoteStatus(req.params.id, "declined", payload.actorUserId ?? null);
    if (!quote) {
      return res.status(404).json({ error: "Quote not found" });
    }
    res.json(quote);
  } catch (error) {
    next(error);
  }
});
