const baseUrl = process.env.CRM_BASE_URL ?? "http://127.0.0.1:3000";

type JsonRecord = Record<string, any>;

async function main() {
  section("Prepare demo data");
  await show("POST /api/dev/seed-demo", () => postJson("/api/dev/seed-demo", {}));

  section("Dashboard and records");
  await show("GET /health", () => getJson("/health"));
  await show("GET /api/dashboard", () => getJson("/api/dashboard"));

  const contacts = await getJson("/api/contacts");
  const conversations = await getJson("/api/conversations");
  const quotes = await getJson("/api/quotes");
  const calls = await getJson("/api/calls");
  const leadContact = contacts.find((contact: JsonRecord) => contact.display_name === "Jordan Lee") ?? contacts[0];
  const quotedContact = contacts.find((contact: JsonRecord) => contact.display_name === "Taylor Morgan") ?? contacts[1] ?? contacts[0];
  const conversation = conversations.find((item: JsonRecord) => item.contact_id === leadContact.id) ?? conversations[0];
  const quote = quotes.find((item: JsonRecord) => item.display_name === "Taylor Morgan") ?? quotes[0];
  const call = calls[0];

  await show("GET /api/contacts", async () => summarizeContacts(contacts));
  await show(`GET /api/contacts/${leadContact.id}`, () => getJson(`/api/contacts/${leadContact.id}`));
  await show(`GET /api/conversations/${conversation.id}`, () => getJson(`/api/conversations/${conversation.id}`));

  section("CRM writes to timeline");
  await show(`POST /api/contacts/${leadContact.id}/notes`, () =>
    postJson(`/api/contacts/${leadContact.id}/notes`, {
      body: "Sample note: customer prefers SMS and morning delivery."
    })
  );
  await show("POST /api/tasks", () =>
    postJson("/api/tasks", {
      contactId: leadContact.id,
      title: "Sample task: confirm follow-up details",
      priority: "high",
      dueAt: new Date(Date.now() + 60 * 60 * 1000).toISOString()
    })
  );

  section("SMS and shared inbox");
  await show(`POST /api/conversations/${conversation.id}/messages`, () =>
    postJson(`/api/conversations/${conversation.id}/messages`, {
      body: "Sample outbound text from staff workspace."
    })
  );
  await show("POST /api/webhooks/sms/inbound unknown number", () =>
    postJson("/api/webhooks/sms/inbound", {
      fromNumber: "+15035550991",
      toNumber: "+15035550000",
      body: "Sample inbound lead text from a new number.",
      providerMessageId: `sample-generic-sms-${Date.now()}`
    })
  );
  await show("POST /api/webhooks/twilio/sms/inbound", () =>
    postJson("/api/webhooks/twilio/sms/inbound", {
      From: "+15035550992",
      To: "+15035550000",
      Body: "Sample Twilio-shaped inbound text.",
      MessageSid: `SM-sample-${Date.now()}`,
      MessagingServiceSid: "MG-sample",
      NumMedia: "0"
    })
  );

  section("Calls");
  await show("POST /api/calls/outbound", () =>
    postJson("/api/calls/outbound", {
      contactId: leadContact.id
    })
  );
  if (call?.id) {
    await show(`POST /api/calls/${call.id}/disposition`, () =>
      postJson(`/api/calls/${call.id}/disposition`, {
        disposition: "sample_follow_up_needed",
        notes: "Sample disposition: call back after lunch."
      })
    );
  }
  await show("POST /api/webhooks/ringcentral/calls/inbound", () =>
    postJson("/api/webhooks/ringcentral/calls/inbound", {
      fromNumber: "+15035550993",
      toNumber: "+15035550000",
      callId: `rc-sample-${Date.now()}`,
      sessionId: "rc-session-sample",
      status: "missed",
      durationSeconds: 0
    })
  );

  section("Quotes");
  const siteId = quotedContact.primary_site?.id ?? (await getJson(`/api/contacts/${quotedContact.id}/sites`))[0]?.id;
  const newQuote = await show("POST /api/quotes", () =>
    postJson("/api/quotes", {
      contactId: quotedContact.id,
      serviceSiteId: siteId,
      title: "Sample quote from API samples",
      deliveryTotal: 95,
      taxTotal: 0,
      notes: "Sample quote preserves version history.",
      lineItems: [
        {
          itemType: "service",
          name: "Sample service package",
          quantity: 4,
          unit: "unit",
          unitPrice: 150
        },
        {
          itemType: "adjustment",
          name: "Sample delivery adjustment",
          quantity: 1,
          unit: "each",
          unitPrice: 75
        }
      ]
    })
  );
  const quoteId = newQuote?.quote?.id ?? quote?.id;
  await show(`POST /api/quotes/${quoteId}/versions`, () =>
    postJson(`/api/quotes/${quoteId}/versions`, {
      notes: "Sample revised version with changed quantity.",
      deliveryTotal: 95,
      taxTotal: 0,
      lineItems: [
        {
          itemType: "service",
          name: "Sample service package revised",
          quantity: 5,
          unit: "unit",
          unitPrice: 150
        }
      ]
    })
  );
  await show(`GET /api/quotes/${quoteId}/pdf`, () => getJson(`/api/quotes/${quoteId}/pdf`));
  await show(`POST /api/quotes/${quoteId}/send-sms`, () => postJson(`/api/quotes/${quoteId}/send-sms`, {}));
  await show(`POST /api/quotes/${quoteId}/send-email`, () => postJson(`/api/quotes/${quoteId}/send-email`, {}));
  await show(`POST /api/quotes/${quoteId}/accept`, () => postJson(`/api/quotes/${quoteId}/accept`, {}));

  section("Unified timeline");
  await show(`GET /api/contacts/${quotedContact.id}/timeline`, async () => {
    const timeline = await getJson(`/api/contacts/${quotedContact.id}/timeline`);
    return timeline.slice(0, 10).map((activity: JsonRecord) => ({
      activity_type: activity.activity_type,
      title: activity.title,
      body: activity.body
    }));
  });

  section("Admin settings");
  await show("GET /api/settings/templates", () => getJson("/api/settings/templates"));
  await show("POST /api/settings/templates", () =>
    postJson("/api/settings/templates", {
      name: "Sample template",
      channel: "sms",
      body: "Sample saved reply template."
    })
  );
  await show("GET /api/settings/integration-settings", () => getJson("/api/settings/integration-settings"));

  section("Done");
  console.log(`Open the app at ${baseUrl}/`);
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

async function show(label: string, run: () => Promise<any>) {
  const result = await run();
  console.log(`\n${label}`);
  console.log(JSON.stringify(trimForDisplay(result), null, 2));
  return result;
}

function summarizeContacts(contacts: JsonRecord[]) {
  return contacts.map((contact) => ({
    id: contact.id,
    display_name: contact.display_name,
    mobile_phone: contact.mobile_phone,
    status: contact.status,
    primary_site: contact.primary_site?.address_line_1 ?? null,
    latest_quote: contact.latest_quote?.quote_number ?? null
  }));
}

function trimForDisplay(value: any): any {
  if (Array.isArray(value)) {
    return value.slice(0, 5).map(trimForDisplay);
  }

  if (!value || typeof value !== "object") {
    return value;
  }

  const output: JsonRecord = {};
  for (const [key, item] of Object.entries(value)) {
    if (["pricing_snapshot_json", "metadata_json"].includes(key)) {
      output[key] = item;
    } else if (Array.isArray(item)) {
      output[key] = item.slice(0, 5).map(trimForDisplay);
    } else if (item && typeof item === "object") {
      output[key] = trimForDisplay(item);
    } else {
      output[key] = item;
    }
  }
  return output;
}

function section(title: string) {
  console.log(`\n=== ${title} ===`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
