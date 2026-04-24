import { Router } from "express";
import { seedDemoData } from "../services/demo_seed_service";

export const devRouter = Router();

devRouter.post("/seed-demo", async (_req, res, next) => {
  if (process.env.NODE_ENV === "production" && process.env.ALLOW_DEMO_SEED !== "true") {
    return res.status(404).json({ error: "Not found" });
  }

  try {
    res.json(await seedDemoData());
  } catch (error) {
    next(error);
  }
});
