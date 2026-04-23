import { query } from "../lib/db";
import { normalizePhone } from "../lib/normalizePhone";
import { createActivity } from "./activity_service";
import { ensureConversation } from "./conversation_service";
import { findOrCreateLeadShellByPhone } from "./contact_service";
import { startProviderOutboundCall } from "./integration_service";

export async function listCalls() {
  const result = await query(
    `SELECT calls.*, c.display_name, c.mobile_phone, u.full_name AS assigned_user_name
     FROM calls
     JOIN contacts c ON c.id = calls.contact_id
     LEFT JOIN users u ON u.id = calls.assigned_user_id
     ORDER BY calls.started_at DESC
     LIMIT 200`
  );

  return result.rows;
}

export async function logInboundCall(input: {
  fromNumber: string;
  toNumber: string;
  providerCallId?: string | null;
  status?: string | null;
}) {
  const contact = await findOrCreateLeadShellByPhone(input.fromNumber, "inbound_call");
  const conversation = await ensureConversation(contact.id, contact.assigned_user_id, "phone");
  const result = await query(
    `INSERT INTO calls
     (contact_id, conversation_id, provider_call_id, direction, status, from_number, to_number, assigned_user_id)
     VALUES ($1, $2, $3, 'inbound', COALESCE($4, 'ringing'), $5, $6, $7)
     ON CONFLICT (provider_call_id) DO UPDATE
       SET status = EXCLUDED.status
     RETURNING *`,
    [
      contact.id,
      conversation.id,
      input.providerCallId ?? null,
      input.status ?? null,
      normalizePhone(input.fromNumber),
      normalizePhone(input.toNumber),
      contact.assigned_user_id ?? null
    ]
  );

  const call = result.rows[0];
  await createActivity({
    contactId: contact.id,
    relatedType: "call",
    relatedId: call.id,
    activityType: call.status === "missed" ? "call.missed" : "call.inbound",
    title: call.status === "missed" ? "Missed call" : "Inbound call logged",
    body: `Inbound call from ${call.from_number}.`,
    actorUserId: call.assigned_user_id,
    metadata: { providerCallId: input.providerCallId ?? null }
  });

  // TODO(automation): missed call -> create follow-up task.
  return call;
}

export async function startOutboundCall(input: {
  contactId: string;
  toNumber?: string | null;
  assignedUserId?: string | null;
}) {
  const contact = (
    await query("SELECT * FROM contacts WHERE id = $1 LIMIT 1", [input.contactId])
  ).rows[0];

  if (!contact) {
    return null;
  }

  const toNumber = normalizePhone(input.toNumber || contact.mobile_phone);
  const providerResult = await startProviderOutboundCall(toNumber);
  const conversation = await ensureConversation(contact.id, input.assignedUserId ?? contact.assigned_user_id, "phone");
  const result = await query(
    `INSERT INTO calls
     (contact_id, conversation_id, provider_call_id, direction, status, from_number, to_number, assigned_user_id)
     VALUES ($1, $2, $3, 'outbound', $4, 'staff', $5, $6)
     RETURNING *`,
    [
      contact.id,
      conversation.id,
      providerResult.providerCallId,
      providerResult.status,
      toNumber,
      input.assignedUserId ?? contact.assigned_user_id ?? null
    ]
  );

  const call = result.rows[0];
  await createActivity({
    contactId: contact.id,
    relatedType: "call",
    relatedId: call.id,
    activityType: "call.outbound",
    title: "Outbound call started",
    body: `Staff started a call to ${toNumber}.`,
    actorUserId: input.assignedUserId ?? null,
    metadata: {
      provider: providerResult.provider,
      providerCallId: providerResult.providerCallId
    }
  });

  return call;
}

export async function updateCallStatus(input: {
  providerCallId: string;
  status?: string | null;
  durationSeconds?: number | null;
  recordingUrl?: string | null;
  voicemailUrl?: string | null;
}) {
  const result = await query(
    `UPDATE calls
     SET status = COALESCE($2, status),
         duration_seconds = COALESCE($3, duration_seconds),
         recording_url = COALESCE($4, recording_url),
         voicemail_url = COALESCE($5, voicemail_url),
         ended_at = CASE WHEN $2 IN ('completed', 'missed', 'failed', 'busy', 'no-answer') THEN NOW() ELSE ended_at END
     WHERE provider_call_id = $1
     RETURNING *`,
    [
      input.providerCallId,
      input.status ?? null,
      input.durationSeconds ?? null,
      input.recordingUrl ?? null,
      input.voicemailUrl ?? null
    ]
  );

  const call = result.rows[0];
  if (call) {
    await createActivity({
      contactId: call.contact_id,
      relatedType: "call",
      relatedId: call.id,
      activityType: input.status === "missed" ? "call.missed" : "call.status",
      title: input.status === "missed" ? "Missed call" : "Call status updated",
      body: `Call status is ${call.status}.`,
      actorUserId: call.assigned_user_id,
      metadata: { providerCallId: input.providerCallId }
    });
  }

  return call ?? null;
}

export async function setCallDisposition(callId: string, disposition: string, notes?: string | null, actorUserId?: string | null) {
  const result = await query(
    `UPDATE calls
     SET disposition = $2,
         notes = COALESCE($3, notes)
     WHERE id = $1
     RETURNING *`,
    [callId, disposition, notes ?? null]
  );

  const call = result.rows[0];
  if (call) {
    await createActivity({
      contactId: call.contact_id,
      relatedType: "call",
      relatedId: call.id,
      activityType: "call.disposition",
      title: "Call disposition saved",
      body: notes || disposition,
      actorUserId: actorUserId ?? call.assigned_user_id ?? null,
      metadata: { disposition }
    });
  }

  return call ?? null;
}
