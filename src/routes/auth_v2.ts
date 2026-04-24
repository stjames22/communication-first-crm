import { Router } from "express";
import { z } from "zod";
import { query } from "../lib/db";
import {
  clearAppPasswordCookie,
  hasValidAppPasswordCookie,
  isAppPasswordEnabled,
  setAppPasswordCookie,
  verifyAppPassword
} from "../middleware/app_password_auth";

export const authV2Router = Router();

const loginSchema = z.object({
  email: z.string().email()
});

const passwordLoginSchema = z.object({
  password: z.string().min(1)
});

authV2Router.get("/session", async (req, res, next) => {
  try {
    const passwordAuthEnabled = isAppPasswordEnabled();
    const passwordAuthenticated = !passwordAuthEnabled || hasValidAppPasswordCookie(req);
    const result = await query(
      `SELECT id, full_name, email, role, is_active
       FROM users
       WHERE is_active = TRUE
       ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, created_at ASC
       LIMIT 1`
    );

    res.json({
      authenticated: passwordAuthenticated && Boolean(result.rows[0]),
      user: passwordAuthenticated ? result.rows[0] ?? null : null,
      authMode: passwordAuthEnabled ? "app_password" : "scaffold",
      authWarning: passwordAuthEnabled
        ? "Shared app password enabled. Add per-user roles before storing real customer data."
        : "Scaffold auth only. This demo workflow is not production-secure."
    });
  } catch (error) {
    next(error);
  }
});

authV2Router.post("/password-login", async (req, res, next) => {
  try {
    if (!isAppPasswordEnabled()) {
      return res.status(400).json({ error: "App password is not configured" });
    }

    const payload = passwordLoginSchema.parse(req.body);
    if (!verifyAppPassword(payload.password)) {
      return res.status(401).json({ error: "Incorrect password" });
    }

    setAppPasswordCookie(res);

    const result = await query(
      `SELECT id, full_name, email, role, is_active
       FROM users
       WHERE is_active = TRUE
       ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, created_at ASC
       LIMIT 1`
    );

    res.json({
      authenticated: true,
      user: result.rows[0] ?? null,
      authMode: "app_password",
      authWarning: "Shared app password enabled. Add per-user roles before storing real customer data."
    });
  } catch (error) {
    next(error);
  }
});

authV2Router.post("/logout", (_req, res) => {
  clearAppPasswordCookie(res);
  res.json({ ok: true });
});

authV2Router.post("/login", async (req, res, next) => {
  try {
    if (isAppPasswordEnabled() && !hasValidAppPasswordCookie(req)) {
      return res.status(401).json({ error: "Password required" });
    }

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
