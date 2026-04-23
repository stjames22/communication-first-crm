import { Router } from "express";
import { z } from "zod";
import { createExternalReference, listExternalReferences } from "../services/external_reference_service";

export const externalReferencesRouter = Router();

const externalReferenceSchema = z.object({
  internalType: z.string().min(1),
  internalId: z.string().uuid(),
  externalSystem: z.string().min(1),
  externalType: z.string().min(1),
  externalId: z.string().min(1),
  metadata: z.record(z.unknown()).optional()
});

externalReferencesRouter.get("/", async (req, res, next) => {
  try {
    res.json(
      await listExternalReferences({
        internalType: asString(req.query.internalType),
        internalId: asString(req.query.internalId),
        externalSystem: asString(req.query.externalSystem),
        externalType: asString(req.query.externalType),
        externalId: asString(req.query.externalId)
      })
    );
  } catch (error) {
    next(error);
  }
});

externalReferencesRouter.post("/", async (req, res, next) => {
  try {
    const payload = externalReferenceSchema.parse(req.body);
    res.status(201).json(await createExternalReference(payload));
  } catch (error) {
    next(error);
  }
});

function asString(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}
