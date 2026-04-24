import { query } from "../lib/db";
import { buildAddressMatchKey, normalizeAddressPart, normalizeState, normalizeZip } from "../lib/normalizeAddress";

export type DuplicateMatchStrength = "exact" | "likely" | "possible";

export type DuplicateMatch = {
  match_strength: DuplicateMatchStrength;
  matched_contact_id: string;
  matched_site_id: string | null;
  reason: string;
  contact_summary: {
    id: string;
    display_name: string;
    mobile_phone: string | null;
    email: string | null;
  };
  site_summary: {
    id: string;
    label: string | null;
    address_line_1: string | null;
    city: string | null;
    state: string | null;
    zip: string | null;
  } | null;
  latest_quote_summary: {
    id: string;
    quote_number: string;
    status: string;
    grand_total: number | string | null;
  } | null;
  latest_activity_summary: {
    id: string;
    title: string;
    activity_type: string;
    created_at: string;
  } | null;
};

export async function searchContactDuplicates(input: {
  phone?: string | null;
  name?: string | null;
  address?: string | null;
  zip?: string | null;
}) {
  const phoneKey = normalizePhoneMatchKey(input.phone);
  const nameQuery = normalizeNameQuery(input.name);
  const addressLine = normalizeAddressPart(extractAddressLine(input.address));
  const zip = normalizeZip(input.zip);

  const collected = new Map<string, DuplicateMatch>();

  if (phoneKey) {
    const contactPhoneMatches = await query<DuplicateSearchRow>(
      `SELECT
         c.id AS contact_id,
         c.display_name,
         c.mobile_phone,
         c.email,
         s.id AS site_id,
         s.label AS site_label,
         s.address_line_1,
         s.city,
         s.state,
         s.zip,
         q.id AS latest_quote_id,
         q.quote_number,
         q.status AS latest_quote_status,
         q.grand_total,
         a.id AS latest_activity_id,
         a.title AS latest_activity_title,
         a.activity_type AS latest_activity_type,
         a.created_at AS latest_activity_created_at
       FROM contacts c
       LEFT JOIN LATERAL (
         SELECT *
         FROM service_sites
         WHERE contact_id = c.id
         ORDER BY created_at ASC
         LIMIT 1
       ) s ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM quotes
         WHERE contact_id = c.id
         ORDER BY updated_at DESC
         LIMIT 1
       ) q ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM activities
         WHERE contact_id = c.id
         ORDER BY created_at DESC
         LIMIT 1
       ) a ON TRUE
       WHERE RIGHT(REGEXP_REPLACE(COALESCE(c.mobile_phone, ''), '[^0-9]', '', 'g'), 10) = $1
          OR RIGHT(REGEXP_REPLACE(COALESCE(c.secondary_phone, ''), '[^0-9]', '', 'g'), 10) = $1`,
      [phoneKey]
    );

    for (const row of contactPhoneMatches.rows) {
      pushMatch(collected, toMatch(row, "exact", "Phone number matches an existing contact."));
    }

    const callPhoneMatches = await query<DuplicateSearchRow>(
      `SELECT
         c.id AS contact_id,
         c.display_name,
         c.mobile_phone,
         c.email,
         s.id AS site_id,
         s.label AS site_label,
         s.address_line_1,
         s.city,
         s.state,
         s.zip,
         q.id AS latest_quote_id,
         q.quote_number,
         q.status AS latest_quote_status,
         q.grand_total,
         a.id AS latest_activity_id,
         a.title AS latest_activity_title,
         a.activity_type AS latest_activity_type,
         a.created_at AS latest_activity_created_at
       FROM calls call_log
       JOIN contacts c ON c.id = call_log.contact_id
       LEFT JOIN LATERAL (
         SELECT *
         FROM service_sites
         WHERE contact_id = c.id
         ORDER BY created_at ASC
         LIMIT 1
       ) s ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM quotes
         WHERE contact_id = c.id
         ORDER BY updated_at DESC
         LIMIT 1
       ) q ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM activities
         WHERE contact_id = c.id
         ORDER BY created_at DESC
         LIMIT 1
       ) a ON TRUE
       WHERE RIGHT(REGEXP_REPLACE(COALESCE(call_log.from_number, ''), '[^0-9]', '', 'g'), 10) = $1
          OR RIGHT(REGEXP_REPLACE(COALESCE(call_log.to_number, ''), '[^0-9]', '', 'g'), 10) = $1
       ORDER BY call_log.started_at DESC
       LIMIT 10`,
      [phoneKey]
    );

    for (const row of callPhoneMatches.rows) {
      pushMatch(collected, toMatch(row, "exact", "Phone number matches an existing call record."));
    }
  }

  if (nameQuery) {
    const exactNameMatches = await query<DuplicateSearchRow>(
      `SELECT
         c.id AS contact_id,
         c.display_name,
         c.mobile_phone,
         c.email,
         s.id AS site_id,
         s.label AS site_label,
         s.address_line_1,
         s.city,
         s.state,
         s.zip,
         q.id AS latest_quote_id,
         q.quote_number,
         q.status AS latest_quote_status,
         q.grand_total,
         a.id AS latest_activity_id,
         a.title AS latest_activity_title,
         a.activity_type AS latest_activity_type,
         a.created_at AS latest_activity_created_at
       FROM contacts c
       LEFT JOIN LATERAL (
         SELECT *
         FROM service_sites
         WHERE contact_id = c.id
         ORDER BY created_at ASC
         LIMIT 1
       ) s ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM quotes
         WHERE contact_id = c.id
         ORDER BY updated_at DESC
         LIMIT 1
       ) q ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM activities
         WHERE contact_id = c.id
         ORDER BY created_at DESC
         LIMIT 1
       ) a ON TRUE
       WHERE LOWER(TRIM(COALESCE(c.display_name, ''))) = $1
          OR LOWER(TRIM(CONCAT_WS(' ', COALESCE(c.first_name, ''), COALESCE(c.last_name, '')))) = $1
       LIMIT 10`,
      [nameQuery]
    );

    for (const row of exactNameMatches.rows) {
      pushMatch(collected, toMatch(row, "likely", "Name exactly matches an existing contact."));
    }

    const partialNameMatches = await query<DuplicateSearchRow>(
      `SELECT
         c.id AS contact_id,
         c.display_name,
         c.mobile_phone,
         c.email,
         s.id AS site_id,
         s.label AS site_label,
         s.address_line_1,
         s.city,
         s.state,
         s.zip,
         q.id AS latest_quote_id,
         q.quote_number,
         q.status AS latest_quote_status,
         q.grand_total,
         a.id AS latest_activity_id,
         a.title AS latest_activity_title,
         a.activity_type AS latest_activity_type,
         a.created_at AS latest_activity_created_at
       FROM contacts c
       LEFT JOIN LATERAL (
         SELECT *
         FROM service_sites
         WHERE contact_id = c.id
         ORDER BY created_at ASC
         LIMIT 1
       ) s ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM quotes
         WHERE contact_id = c.id
         ORDER BY updated_at DESC
         LIMIT 1
       ) q ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM activities
         WHERE contact_id = c.id
         ORDER BY created_at DESC
         LIMIT 1
       ) a ON TRUE
       WHERE LOWER(COALESCE(c.display_name, '')) LIKE $1
          OR LOWER(CONCAT_WS(' ', COALESCE(c.first_name, ''), COALESCE(c.last_name, ''))) LIKE $1
       LIMIT 10`,
      [`%${nameQuery}%`]
    );

    for (const row of partialNameMatches.rows) {
      pushMatch(collected, toMatch(row, "possible", "Name is close to an existing contact."));
    }
  }

  if (addressLine || zip) {
    const siteRows = await query<DuplicateSearchRow>(
      `SELECT
         c.id AS contact_id,
         c.display_name,
         c.mobile_phone,
         c.email,
         s.id AS site_id,
         s.label AS site_label,
         s.address_line_1,
         s.city,
         s.state,
         s.zip,
         q.id AS latest_quote_id,
         q.quote_number,
         q.status AS latest_quote_status,
         q.grand_total,
         a.id AS latest_activity_id,
         a.title AS latest_activity_title,
         a.activity_type AS latest_activity_type,
         a.created_at AS latest_activity_created_at
       FROM service_sites s
       JOIN contacts c ON c.id = s.contact_id
       LEFT JOIN LATERAL (
         SELECT *
         FROM quotes
         WHERE contact_id = c.id
         ORDER BY updated_at DESC
         LIMIT 1
       ) q ON TRUE
       LEFT JOIN LATERAL (
         SELECT *
         FROM activities
         WHERE contact_id = c.id
         ORDER BY created_at DESC
         LIMIT 1
       ) a ON TRUE
       WHERE ($1 = '' OR REGEXP_REPLACE(LOWER(COALESCE(s.address_line_1, '')), '[^a-z0-9]', '', 'g') LIKE $2)
         AND ($3 = '' OR LEFT(REGEXP_REPLACE(COALESCE(s.zip, ''), '[^0-9]', '', 'g'), 5) = $3)
       LIMIT 20`,
      [addressLine, `%${addressLine.replace(/\s+/g, "")}%`, zip]
    );

    const requestedAddressKey = buildAddressMatchKey({
      addressLine1: extractAddressLine(input.address),
      city: extractCity(input.address),
      state: extractState(input.address),
      zip: input.zip
    });

    for (const row of siteRows.rows) {
      const rowAddressKey = buildAddressMatchKey({
        addressLine1: row.address_line_1,
        city: row.city,
        state: row.state,
        zip: row.zip
      });

      const strength: DuplicateMatchStrength =
        requestedAddressKey && rowAddressKey && requestedAddressKey === rowAddressKey
          ? "exact"
          : zip && normalizeZip(row.zip) === zip
            ? "likely"
            : "possible";

      pushMatch(
        collected,
        toMatch(
          row,
          strength,
          strength === "exact"
            ? "Address matches an existing service site."
            : "Address is close to an existing service site."
        )
      );
    }
  }

  const matches = Array.from(collected.values()).sort(compareDuplicateMatches);
  const bestMatch = matches[0] ?? null;

  return {
    match_strength: bestMatch?.match_strength ?? null,
    matched_contact_id: bestMatch?.matched_contact_id ?? null,
    matched_site_id: bestMatch?.matched_site_id ?? null,
    reason: bestMatch?.reason ?? null,
    contact_summary: bestMatch?.contact_summary ?? null,
    site_summary: bestMatch?.site_summary ?? null,
    latest_quote_summary: bestMatch?.latest_quote_summary ?? null,
    latest_activity_summary: bestMatch?.latest_activity_summary ?? null,
    matches
  };
}

export async function findBlockingDuplicate(input: {
  mobilePhone?: string | null;
  secondaryPhone?: string | null;
  displayName?: string | null;
  firstName?: string | null;
  lastName?: string | null;
  addressLine1?: string | null;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
}) {
  const duplicateResult = await searchContactDuplicates({
    phone: input.mobilePhone ?? input.secondaryPhone ?? null,
    name: [input.displayName, input.firstName, input.lastName].filter(Boolean).join(" "),
    address: [input.addressLine1, input.city, input.state].filter(Boolean).join(", "),
    zip: input.zip ?? null
  });

  return duplicateResult.matches.find((match) => match.match_strength === "exact") ?? null;
}

type DuplicateSearchRow = {
  contact_id: string;
  display_name: string;
  mobile_phone: string | null;
  email: string | null;
  site_id: string | null;
  site_label: string | null;
  address_line_1: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  latest_quote_id: string | null;
  quote_number: string | null;
  latest_quote_status: string | null;
  grand_total: number | string | null;
  latest_activity_id: string | null;
  latest_activity_title: string | null;
  latest_activity_type: string | null;
  latest_activity_created_at: string | null;
};

function toMatch(row: DuplicateSearchRow, matchStrength: DuplicateMatchStrength, reason: string): DuplicateMatch {
  return {
    match_strength: matchStrength,
    matched_contact_id: row.contact_id,
    matched_site_id: row.site_id ?? null,
    reason,
    contact_summary: {
      id: row.contact_id,
      display_name: row.display_name,
      mobile_phone: row.mobile_phone,
      email: row.email
    },
    site_summary: row.site_id
      ? {
          id: row.site_id,
          label: row.site_label,
          address_line_1: row.address_line_1,
          city: row.city,
          state: row.state,
          zip: row.zip
        }
      : null,
    latest_quote_summary: row.latest_quote_id
      ? {
          id: row.latest_quote_id,
          quote_number: row.quote_number ?? "",
          status: row.latest_quote_status ?? "draft",
          grand_total: row.grand_total
        }
      : null,
    latest_activity_summary: row.latest_activity_id && row.latest_activity_created_at
      ? {
          id: row.latest_activity_id,
          title: row.latest_activity_title ?? "",
          activity_type: row.latest_activity_type ?? "",
          created_at: row.latest_activity_created_at
        }
      : null
  };
}

function pushMatch(store: Map<string, DuplicateMatch>, match: DuplicateMatch) {
  const key = `${match.matched_contact_id}:${match.matched_site_id ?? "contact"}`;
  const existing = store.get(key);
  if (!existing || compareDuplicateMatches(match, existing) < 0) {
    store.set(key, match);
  }
}

function compareDuplicateMatches(left: DuplicateMatch, right: DuplicateMatch) {
  const byStrength = strengthRank(left.match_strength) - strengthRank(right.match_strength);
  if (byStrength !== 0) {
    return byStrength;
  }

  const leftActivity = left.latest_activity_summary?.created_at ? Date.parse(left.latest_activity_summary.created_at) : 0;
  const rightActivity = right.latest_activity_summary?.created_at ? Date.parse(right.latest_activity_summary.created_at) : 0;
  return rightActivity - leftActivity;
}

function strengthRank(value: DuplicateMatchStrength) {
  switch (value) {
    case "exact":
      return 0;
    case "likely":
      return 1;
    default:
      return 2;
  }
}

function normalizePhoneMatchKey(value?: string | null) {
  const digits = String(value ?? "").replace(/\D/g, "");
  if (!digits) {
    return "";
  }

  return digits.length > 10 ? digits.slice(-10) : digits;
}

function normalizeNameQuery(value?: string | null) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function extractCity(address?: string | null) {
  const parts = String(address ?? "").split(",");
  return parts[1] ?? "";
}

function extractState(address?: string | null) {
  const parts = String(address ?? "").split(",");
  return parts[2] ?? "";
}

function extractAddressLine(address?: string | null) {
  const parts = String(address ?? "").split(",");
  return parts[0] ?? "";
}
