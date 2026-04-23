export function normalizePhone(phone: string): string {
  return phone.replace(/[^\d+]/g, "");
}
