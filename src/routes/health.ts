import { Router } from "express";
import { query } from "../lib/db";

export const healthRouter = Router();

healthRouter.get("/", async (_req, res) => {
  try {
    await query("SELECT 1");
    res.json({ ok: true, service: "communication-first-crm", database: "up" });
  } catch (error) {
    console.error("Health check database query failed.", error);
    res.status(503).json({ ok: false, service: "communication-first-crm", database: "down" });
  }
});
