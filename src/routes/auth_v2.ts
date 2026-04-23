import { Router } from "express";
import { z } from "zod";
import { query } from "../lib/db";

export const authV2Router = Router();

const loginSchema = z.object({
  email: z.string().email()
});

authV2Router.get("/session", async (_req, res, next) => {
  try {
    const result = await query(
      `SELECT id, full_name, email, role, is_active
       FROM users
       WHERE is_active = TRUE
       ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, created_at ASC
       LIMIT 1`
    );

    res.json({
      authenticated: Boolean(result.rows[0]),
      user: result.rows[0] ?? null,
      authMode: "scaffold"
    });
  } catch (error) {
    next(error);
  }
});

authV2Router.post("/login", async (req, res, next) => {
  try {
    const payload = loginSchema.parse(req.body);
    const result = await query(
      `SELECT id, full_name, email, role, is_active
       FROM users
       WHERE email = $1 AND is_active = TRUE
       LIMIT 1`,
      [payload.email]
    );

    if (!result.rows[0]) {
      return res.status(401).json({ error: "No active user found for that email" });
    }

    // TODO(auth): replace scaffold login with signed sessions or SSO.
    res.json({
      authenticated: true,
      user: result.rows[0],
      authMode: "scaffold"
    });
  } catch (error) {
    next(error);
  }
});
