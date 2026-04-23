import { Router } from "express";
import { z } from "zod";
import { query } from "../lib/db";
import { getIntegrationStatus } from "../services/integration_service";

export const adminV2Router = Router();

const templateSchema = z.object({
  name: z.string().min(1),
  channel: z.string().default("sms"),
  body: z.string().min(1),
  isActive: z.boolean().optional()
});

const phoneRoutingSchema = z.object({
  label: z.string().min(1),
  inboundNumber: z.string().min(5),
  destinationType: z.string().default("queue"),
  destinationValue: z.string().min(1),
  isActive: z.boolean().optional()
});

adminV2Router.get("/templates", async (_req, res, next) => {
  try {
    const result = await query("SELECT * FROM message_templates ORDER BY created_at DESC");
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

adminV2Router.post("/templates", async (req, res, next) => {
  try {
    const payload = templateSchema.parse(req.body);
    const result = await query(
      `INSERT INTO message_templates (name, channel, body, is_active)
       VALUES ($1, $2, $3, COALESCE($4, TRUE))
       RETURNING *`,
      [payload.name, payload.channel, payload.body, payload.isActive ?? null]
    );
    res.status(201).json(result.rows[0]);
  } catch (error) {
    next(error);
  }
});

adminV2Router.get("/phone-routing", async (_req, res, next) => {
  try {
    const result = await query("SELECT * FROM phone_routing_settings ORDER BY created_at DESC");
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

adminV2Router.post("/phone-routing", async (req, res, next) => {
  try {
    const payload = phoneRoutingSchema.parse(req.body);
    const result = await query(
      `INSERT INTO phone_routing_settings
       (label, inbound_number, destination_type, destination_value, is_active)
       VALUES ($1, $2, $3, $4, COALESCE($5, TRUE))
       RETURNING *`,
      [
        payload.label,
        payload.inboundNumber,
        payload.destinationType,
        payload.destinationValue,
        payload.isActive ?? null
      ]
    );
    res.status(201).json(result.rows[0]);
  } catch (error) {
    next(error);
  }
});

adminV2Router.get("/users", async (_req, res, next) => {
  try {
    const result = await query("SELECT * FROM users ORDER BY full_name ASC");
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

adminV2Router.get("/quote-defaults", async (_req, res, next) => {
  try {
    const result = await query("SELECT * FROM quote_defaults ORDER BY created_at DESC");
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

adminV2Router.get("/integration-settings", async (_req, res, next) => {
  try {
    const persisted = await query("SELECT * FROM integration_settings ORDER BY provider_type, provider_name");
    res.json({
      runtime: getIntegrationStatus(),
      persisted: persisted.rows
    });
  } catch (error) {
    next(error);
  }
});
