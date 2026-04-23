require("dotenv").config({ override: true });

const { readFile } = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { Client } = require("pg");

function createClient(connectionString) {
  return new Client({
    connectionString,
    connectionTimeoutMillis: 5000
  });
}

function getDatabaseUrl() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    throw new Error("Missing DATABASE_URL in environment.");
  }

  return databaseUrl;
}

function getTargetDatabaseName(databaseUrl) {
  const parsed = new URL(databaseUrl);
  const databaseName = decodeURIComponent(parsed.pathname.replace(/^\//, ""));

  if (!databaseName) {
    throw new Error("DATABASE_URL must include a database name.");
  }

  return databaseName;
}

function getAdminDatabaseUrl(databaseUrl) {
  const parsed = new URL(databaseUrl);
  parsed.pathname = "/postgres";
  return parsed.toString();
}

function quoteIdentifier(value) {
  return `"${value.replace(/"/g, "\"\"")}"`;
}

async function ensureDatabaseExists(databaseUrl) {
  const databaseName = getTargetDatabaseName(databaseUrl);
  const adminClient = createClient(getAdminDatabaseUrl(databaseUrl));

  console.log(`Checking database "${databaseName}"...`);
  await adminClient.connect();

  try {
    const existing = await adminClient.query(
      "SELECT datname FROM pg_database WHERE datname = $1 LIMIT 1",
      [databaseName]
    );

    if (existing.rows[0]) {
      console.log(`Database "${databaseName}" already exists.`);
      return;
    }

    await adminClient.query(`CREATE DATABASE ${quoteIdentifier(databaseName)}`);
    console.log(`Created database "${databaseName}".`);
  } finally {
    await adminClient.end();
  }
}

async function applySchema(databaseUrl) {
  const schemaPath = path.resolve(process.cwd(), "db/schema.sql");
  const schemaSql = await readFile(schemaPath, "utf8");
  const client = createClient(databaseUrl);

  console.log(`Applying schema to "${getTargetDatabaseName(databaseUrl)}"...`);
  await client.connect();

  try {
    await client.query(schemaSql);
    console.log(`Applied schema from ${schemaPath}.`);
  } finally {
    await client.end();
  }
}

function formatSetupError(error) {
  const normalizedError = normalizeSetupError(error);

  if (normalizedError && normalizedError.code === "ECONNREFUSED") {
    return "Could not reach Postgres at DATABASE_URL. Start the local Postgres server and run `npm run db:setup` again.";
  }

  if (normalizedError && normalizedError.code === "ETIMEDOUT") {
    return "Timed out while connecting to Postgres at DATABASE_URL. Make sure the local Postgres server is running and accepting connections, then rerun `npm run db:setup`.";
  }

  if (normalizedError && (normalizedError.code === "EPERM" || normalizedError.code === "EACCES")) {
    return "The connection to Postgres was blocked before it could be established. Confirm Postgres is listening on the host/port from DATABASE_URL and that local connections are allowed, then rerun `npm run db:setup`.";
  }

  if (normalizedError && normalizedError.code === "28P01") {
    return "Postgres rejected the username/password in DATABASE_URL. Update DATABASE_URL and rerun `npm run db:setup`.";
  }

  if (normalizedError instanceof Error && /role ".*" does not exist/i.test(normalizedError.message)) {
    const localUser = os.userInfo().username;
    return `The Postgres role in DATABASE_URL does not exist. On Homebrew installs this is often your macOS username, for example: postgres://${localUser}@localhost:5432/phone_integration_system`;
  }

  return normalizedError instanceof Error ? normalizedError.message : "Database setup failed.";
}

function normalizeSetupError(error) {
  if (error instanceof AggregateError && Array.isArray(error.errors) && error.errors.length > 0) {
    return normalizeSetupError(error.errors[0]);
  }

  if (error && typeof error === "object" && "cause" in error && error.cause) {
    return normalizeSetupError(error.cause);
  }

  return error;
}

async function main() {
  try {
    const databaseUrl = getDatabaseUrl();
    await ensureDatabaseExists(databaseUrl);
    await applySchema(databaseUrl);
    console.log("Database setup complete.");
  } catch (error) {
    console.error(formatSetupError(error));
    process.exitCode = 1;
  }
}

void main();
