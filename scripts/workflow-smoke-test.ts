const baseUrl = process.env.CRM_BASE_URL ?? "http://127.0.0.1:3000";

type Check = {
  name: string;
  path: string;
  validate?: (body: unknown) => void;
};

const checks: Check[] = [
  {
    name: "health",
    path: "/health",
    validate(body) {
      const result = body as { ok?: boolean; database?: string };
      if (result.ok !== true || result.database !== "ok") {
        throw new Error(`Expected health ok/database ok, got ${JSON.stringify(body)}`);
      }
    }
  },
  { name: "contacts", path: "/api/contacts", validate: expectArray },
  { name: "conversations", path: "/api/conversations", validate: expectArray },
  { name: "quotes", path: "/api/quotes", validate: expectArray },
  { name: "calls", path: "/api/calls", validate: expectArray }
];

async function main() {
  for (const check of checks) {
    const response = await fetch(`${baseUrl}${check.path}`);
    if (!response.ok) {
      throw new Error(`${check.name} failed with ${response.status} ${response.statusText}`);
    }

    const body = await response.json();
    check.validate?.(body);
    console.log(`ok ${check.name}`);
  }
}

function expectArray(body: unknown) {
  if (!Array.isArray(body)) {
    throw new Error(`Expected array response, got ${JSON.stringify(body)}`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
