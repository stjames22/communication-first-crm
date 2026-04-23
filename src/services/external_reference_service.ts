import { query } from "../lib/db";

export type ExternalReferenceInput = {
  internalType: string;
  internalId: string;
  externalSystem: string;
  externalType: string;
  externalId: string;
  metadata?: Record<string, unknown>;
};

export async function createExternalReference(input: ExternalReferenceInput) {
  const result = await query(
    `INSERT INTO external_references
     (internal_type, internal_id, external_system, external_type, external_id, metadata_json)
     VALUES ($1, $2, $3, $4, $5, $6::jsonb)
     ON CONFLICT (internal_type, internal_id, external_system, external_type, external_id)
     DO UPDATE SET metadata_json = EXCLUDED.metadata_json
     RETURNING *`,
    [
      input.internalType,
      input.internalId,
      input.externalSystem,
      input.externalType,
      input.externalId,
      JSON.stringify(input.metadata ?? {})
    ]
  );

  return result.rows[0];
}

export async function listExternalReferences(filters: {
  internalType?: string;
  internalId?: string;
  externalSystem?: string;
  externalType?: string;
  externalId?: string;
}) {
  const clauses: string[] = [];
  const params: string[] = [];

  for (const [column, value] of [
    ["internal_type", filters.internalType],
    ["internal_id", filters.internalId],
    ["external_system", filters.externalSystem],
    ["external_type", filters.externalType],
    ["external_id", filters.externalId]
  ] as const) {
    if (value) {
      params.push(value);
      clauses.push(`${column} = $${params.length}`);
    }
  }

  const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
  const result = await query(
    `SELECT *
     FROM external_references
     ${where}
     ORDER BY created_at DESC
     LIMIT 200`,
    params
  );

  return result.rows;
}
