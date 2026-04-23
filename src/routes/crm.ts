import { Router } from "express";
import { z } from "zod";
import {
  addNote,
  createContact,
  createServiceSite,
  getContact,
  listContacts,
  listServiceSites,
  updateContact
} from "../services/contact_service";
import { listContactTimeline } from "../services/activity_service";
import { findBlockingDuplicate, searchContactDuplicates } from "../services/duplicate_service";
import { createTask, listTasks } from "../services/task_service";

export const contactsRouter = Router();
export const tasksRouter = Router();

const contactSchema = z.object({
  accountId: z.string().uuid().nullable().optional(),
  firstName: z.string().nullable().optional(),
  lastName: z.string().nullable().optional(),
  displayName: z.string().nullable().optional(),
  mobilePhone: z.string().min(5),
  secondaryPhone: z.string().nullable().optional(),
  email: z.string().email().nullable().optional().or(z.literal("")),
  preferredContactMethod: z.string().nullable().optional(),
  status: z.string().nullable().optional(),
  source: z.string().nullable().optional(),
  assignedUserId: z.string().uuid().nullable().optional(),
  duplicateWarningAccepted: z.boolean().optional()
});

const contactPatchSchema = contactSchema.partial().extend({
  mobilePhone: z.string().min(5).optional()
});

const siteSchema = z.object({
  label: z.string().nullable().optional(),
  addressLine1: z.string().min(1),
  addressLine2: z.string().nullable().optional(),
  city: z.string().min(1),
  state: z.string().min(1),
  zip: z.string().min(1),
  deliveryZone: z.string().nullable().optional(),
  siteNotes: z.string().nullable().optional(),
  actorUserId: z.string().uuid().nullable().optional()
});

const noteSchema = z.object({
  body: z.string().min(1),
  actorUserId: z.string().uuid().nullable().optional()
});

const taskSchema = z.object({
  contactId: z.string().uuid(),
  assignedUserId: z.string().uuid().nullable().optional(),
  title: z.string().min(1),
  dueAt: z.string().nullable().optional(),
  status: z.string().nullable().optional(),
  priority: z.string().nullable().optional()
});

const duplicateQuerySchema = z.object({
  phone: z.string().optional(),
  name: z.string().optional(),
  address: z.string().optional(),
  zip: z.string().optional()
});

contactsRouter.get("/", async (_req, res, next) => {
  try {
    res.json(await listContacts());
  } catch (error) {
    next(error);
  }
});

contactsRouter.get("/duplicates/search", async (req, res, next) => {
  try {
    const payload = duplicateQuerySchema.parse(req.query);
    res.json(await searchContactDuplicates(payload));
  } catch (error) {
    next(error);
  }
});

contactsRouter.post("/", async (req, res, next) => {
  try {
    const payload = contactSchema.parse(req.body);
    if (!payload.duplicateWarningAccepted) {
      const blockingDuplicate = await findBlockingDuplicate({
        mobilePhone: payload.mobilePhone,
        secondaryPhone: payload.secondaryPhone,
        displayName: payload.displayName,
        firstName: payload.firstName,
        lastName: payload.lastName
      });

      if (blockingDuplicate) {
        return res.status(409).json({
          error: "Possible existing customer found",
          duplicate: blockingDuplicate
        });
      }
    }
    const contact = await createContact(payload);
    res.status(201).json(contact);
  } catch (error) {
    next(error);
  }
});

contactsRouter.get("/:id", async (req, res, next) => {
  try {
    const contact = await getContact(req.params.id);
    if (!contact) {
      return res.status(404).json({ error: "Contact not found" });
    }
    res.json(contact);
  } catch (error) {
    next(error);
  }
});

contactsRouter.patch("/:id", async (req, res, next) => {
  try {
    const payload = contactPatchSchema.parse(req.body);
    const contact = await updateContact(req.params.id, payload);
    if (!contact) {
      return res.status(404).json({ error: "Contact not found" });
    }
    res.json(contact);
  } catch (error) {
    next(error);
  }
});

contactsRouter.get("/:id/timeline", async (req, res, next) => {
  try {
    res.json(await listContactTimeline(req.params.id));
  } catch (error) {
    next(error);
  }
});

contactsRouter.get("/:id/sites", async (req, res, next) => {
  try {
    res.json(await listServiceSites(req.params.id));
  } catch (error) {
    next(error);
  }
});

contactsRouter.post("/:id/sites", async (req, res, next) => {
  try {
    const payload = siteSchema.parse(req.body);
    const site = await createServiceSite(req.params.id, payload, payload.actorUserId ?? null);
    res.status(201).json(site);
  } catch (error) {
    next(error);
  }
});

contactsRouter.post("/:id/notes", async (req, res, next) => {
  try {
    const payload = noteSchema.parse(req.body);
    const note = await addNote(req.params.id, payload.body, payload.actorUserId ?? null);
    res.status(201).json(note);
  } catch (error) {
    next(error);
  }
});

tasksRouter.get("/", async (_req, res, next) => {
  try {
    res.json(await listTasks());
  } catch (error) {
    next(error);
  }
});

tasksRouter.post("/", async (req, res, next) => {
  try {
    const payload = taskSchema.parse(req.body);
    const task = await createTask(payload);
    res.status(201).json(task);
  } catch (error) {
    next(error);
  }
});
