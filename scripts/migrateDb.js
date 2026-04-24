require("dotenv").config({ override: process.env.NODE_ENV !== "production" });

const { readFile } = require("node:fs/promises");
const path = require("node:path");
const { Client } = require("pg");

async function main() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    throw new Error("Missing DATABASE_URL in environment.");
  }

  const schemaPath = path.resolve(process.cwd(), "db/schema.sql");
  const schemaSql = await readFile(schemaPath, "utf8");
  const client = new Client({
    connectionString: databaseUrl,
    connectionTimeoutMillis: 10000
  });

  await client.connect();
  try {
    await client.query(schemaSql);
    console.log(`Applied database schema from ${schemaPath}.`);
  } finally {
    await client.end();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
