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
      authMode: "scaffold",
      authWarning: "Scaffold auth only. This demo workflow is not production-secure."
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

    // TODO(auth): replace scaffold login with signed server sessions.
    // TODO(auth): alternatively support SSO if that matches staff workflow better.
    // TODO(auth): if password auth is used, add hashing, logout, session rotation, and role enforcement.
    res.json({
      authenticated: true,
      user: result.rows[0],
      authMode: "scaffold",
      authWarning: "Scaffold auth only. This demo workflow is not production-secure."
    });
  } catch (error) {
    next(error);
  }
});
