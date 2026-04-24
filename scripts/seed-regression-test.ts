const baseUrl = process.env.CRM_BASE_URL ?? "http://127.0.0.1:3000";

type Counts = {
  contacts: number;
  conversations: number;
  calls: number;
  quotes: number;
  tasks: number;
  activities: number;
};

async function main() {
  await postJson("/api/dev/seed-demo", {});
  const first = await loadCounts();
  assertSeedShape(first);

  await postJson("/api/dev/seed-demo", {});
  const second = await loadCounts();
  assertSeedShape(second);
  assertEqual(second.contacts, first.contacts, "contacts should remain stable after reseed");
  assertEqual(second.conversations, first.conversations, "conversations should remain stable after reseed");
  assertEqual(second.calls, first.calls, "calls should remain stable after reseed");
  assertEqual(second.quotes, first.quotes, "quotes should remain stable after reseed");
  assertEqual(second.tasks, first.tasks, "tasks should remain stable after reseed");

  const dashboard = await getJson("/api/dashboard");
  assert(dashboard.metrics.unreadTexts >= 1, "dashboard should include unread texts");
  assert(dashboard.metrics.missedCalls >= 1, "dashboard should include missed calls");
  assert(dashboard.metrics.newLeads >= 1, "dashboard should include new leads");
  assert(dashboard.metrics.quotesAwaitingFollowUp >= 1, "dashboard should include quote/proposal follow-ups");
  assert(dashboard.metrics.tasksDueToday >= 1, "dashboard should include tasks due today");

  console.log("ok seed regression");
}

async function loadCounts(): Promise<Counts> {
  const [contacts, conversations, calls, quotes, tasks, activitySource] = await Promise.all([
    getJson("/api/contacts"),
    getJson("/api/conversations"),
    getJson("/api/calls"),
    getJson("/api/quotes"),
    getJson("/api/tasks"),
    getJson("/api/dashboard")
  ]);

  return {
    contacts: contacts.length,
    conversations: conversations.length,
    calls: calls.length,
    quotes: quotes.length,
    tasks: tasks.length,
    activities: activitySource.recentActivity.length
  };
}

function assertSeedShape(counts: Counts) {
  assert(counts.contacts >= 8, `expected at least 8 contacts, got ${counts.contacts}`);
  assert(counts.conversations >= 8, `expected at least 8 conversations, got ${counts.conversations}`);
  assert(counts.calls >= 5, `expected at least 5 calls, got ${counts.calls}`);
  assert(counts.quotes >= 6, `expected at least 6 quotes, got ${counts.quotes}`);
  assert(counts.tasks >= 5, `expected at least 5 tasks, got ${counts.tasks}`);
  assert(counts.activities >= 8, `expected recent activities, got ${counts.activities}`);
}

async function getJson(path: string) {
  const response = await fetch(`${baseUrl}${path}`);
  return parseJsonResponse(response, path);
}

async function postJson(path: string, body: unknown) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  return parseJsonResponse(response, path);
}

async function parseJsonResponse(response: Response, path: string) {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(`${path} failed with ${response.status}: ${JSON.stringify(body)}`);
  }

  return body;
}

function assert(value: unknown, message: string): asserts value {
  if (!value) {
    throw new Error(message);
  }
}

function assertEqual(actual: unknown, expected: unknown, message: string) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
