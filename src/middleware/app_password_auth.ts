import crypto from "node:crypto";
import type { NextFunction, Request, Response } from "express";

const cookieName = "cfcrm_auth";

export function isAppPasswordEnabled() {
  return Boolean(process.env.APP_PASSWORD);
}

export function requireAppPassword(req: Request, res: Response, next: NextFunction) {
  if (!isAppPasswordEnabled() || isPublicPath(req.path)) {
    return next();
  }

  if (hasValidAppPasswordCookie(req)) {
    return next();
  }

  const acceptHeader = req.headers.accept || "";
  if (req.method === "GET" && acceptHeader.includes("text/html")) {
    const nextPath = encodeURIComponent(req.originalUrl || "/");
    return res.redirect(`/login.html?next=${nextPath}`);
  }

  return res.status(401).json({ error: "Password required" });
}

export function verifyAppPassword(password: string) {
  const expected = process.env.APP_PASSWORD;
  if (!expected) {
    return false;
  }

  return timingSafeEqual(password, expected);
}

export function hasValidAppPasswordCookie(req: Request) {
  const token = parseCookies(req.headers.cookie || "")[cookieName];
  return Boolean(token && timingSafeEqual(token, makeAuthToken()));
}

export function setAppPasswordCookie(res: Response) {
  res.cookie(cookieName, makeAuthToken(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: 1000 * 60 * 60 * 12,
    path: "/"
  });
}

export function clearAppPasswordCookie(res: Response) {
  res.clearCookie(cookieName, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/"
  });
}

function isPublicPath(path: string) {
  return (
    path === "/health" ||
    path.startsWith("/health/") ||
    path === "/login.html" ||
    path === "/login.css" ||
    path === "/login.js" ||
    path === "/favicon.ico" ||
    path === "/auth/session" ||
    path === "/auth/password-login" ||
    path === "/auth/logout" ||
    path === "/api/auth/session" ||
    path === "/api/auth/password-login" ||
    path === "/api/auth/logout" ||
    path.startsWith("/webhooks/") ||
    path.startsWith("/api/webhooks/")
  );
}

function makeAuthToken() {
  const secret = process.env.APP_AUTH_SECRET || process.env.APP_PASSWORD || "development";
  return crypto.createHmac("sha256", secret).update("communication-first-crm").digest("hex");
}

function timingSafeEqual(actual: string, expected: string) {
  const actualBuffer = Buffer.from(actual);
  const expectedBuffer = Buffer.from(expected);

  if (actualBuffer.length !== expectedBuffer.length) {
    return false;
  }

  return crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

function parseCookies(header: string) {
  return header.split(";").reduce<Record<string, string>>((cookies, part) => {
    const [rawName, ...rawValue] = part.trim().split("=");
    if (!rawName || rawValue.length === 0) {
      return cookies;
    }

    cookies[rawName] = decodeURIComponent(rawValue.join("="));
    return cookies;
  }, {});
}
