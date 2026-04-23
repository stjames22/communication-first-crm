import "./env";
import express from "express";
import cors from "cors";
import helmet from "helmet";
import morgan from "morgan";
import path from "node:path";
import { ZodError } from "zod";

import { adminV2Router } from "./routes/admin_v2";
import { authV2Router } from "./routes/auth_v2";
import { callsV2Router, callWebhooksRouter } from "./routes/calls_v2";
import { contactsRouter, tasksRouter } from "./routes/crm";
import { conversationsV2Router, smsWebhooksRouter } from "./routes/communications";
import { dashboardV2Router } from "./routes/dashboard_v2";
import { devRouter } from "./routes/dev";
import { externalReferencesRouter } from "./routes/external_references";
import { healthRouter } from "./routes/health";
import { quotesV2Router } from "./routes/quotes_v2";

export function createApp() {
  const app = express();
  const publicDir = path.resolve(process.cwd(), "public");

  app.use(helmet());
  app.use(cors());
  app.use(morgan("dev"));

  // Provider webhooks may arrive as form data. Keep provider payloads separate
  // from internal record IDs and normalize them in the service layer.
  app.use("/webhooks/sms", express.urlencoded({ extended: false }), express.json(), smsWebhooksRouter);
  app.use("/webhooks/calls", express.urlencoded({ extended: false }), express.json(), callWebhooksRouter);

  app.use(express.json());
  app.use(express.static(publicDir));

  app.use("/health", healthRouter);

  app.use("/auth", authV2Router);
  app.use("/dashboard", dashboardV2Router);
  app.use("/contacts", contactsRouter);
  app.use("/tasks", tasksRouter);
  app.use("/conversations", conversationsV2Router);
  app.use("/calls", callsV2Router);
  app.use("/quotes", quotesV2Router);
  app.use("/external-references", externalReferencesRouter);
  app.use("/settings", adminV2Router);

  app.use("/api/auth", authV2Router);
  app.use("/api/dashboard", dashboardV2Router);
  app.use("/api/contacts", contactsRouter);
  app.use("/api/tasks", tasksRouter);
  app.use("/api/conversations", conversationsV2Router);
  app.use("/api/calls", callsV2Router);
  app.use("/api/quotes", quotesV2Router);
  app.use("/api/external-references", externalReferencesRouter);
  app.use("/api/settings", adminV2Router);

  app.use("/api/webhooks/sms", smsWebhooksRouter);
  app.use("/api/webhooks/calls", callWebhooksRouter);
  app.use("/api/dev", devRouter);

  app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    if (err instanceof ZodError) {
      return res.status(400).json({
        error: "Invalid request body",
        details: err.issues.map((issue) => ({
          path: issue.path.join("."),
          message: issue.message
        }))
      });
    }

    if (isPgError(err)) {
      if (err.code === "23505") {
        return res.status(409).json({ error: "Record already exists" });
      }

      if (err.code === "23503") {
        return res.status(400).json({ error: "Related record not found" });
      }

      if (err.code === "22P02") {
        return res.status(400).json({ error: "Invalid identifier or field format" });
      }
    }

    console.error(err);
    res.status(500).json({ error: "Internal server error" });
  });

  return app;
}

function isPgError(error: unknown): error is { code?: string } {
  return typeof error === "object" && error !== null && "code" in error;
}

export const app = createApp();
