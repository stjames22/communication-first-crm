export function normalizeAddressPart(value?: string | null): string {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function normalizeState(value?: string | null): string {
  return normalizeAddressPart(value).replace(/\s+/g, "").toUpperCase();
}

export function normalizeZip(value?: string | null): string {
  return String(value ?? "").replace(/\D/g, "").slice(0, 5);
}

export function buildAddressMatchKey(input: {
  addressLine1?: string | null;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
}): string {
  return [
    normalizeAddressPart(input.addressLine1),
    normalizeAddressPart(input.city),
    normalizeState(input.state),
    normalizeZip(input.zip)
  ]
    .filter(Boolean)
    .join("|");
}
