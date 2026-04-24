const baseUrl = process.env.CRM_BASE_URL ?? "http://127.0.0.1:3000";

type JsonRecord = Record<string, any>;

async function main() {
  await postJson("/api/dev/seed-demo", {});

  try {
    await checkDuplicatePhoneMatch();
    await checkDuplicateNameMatch();
    await checkDuplicateSiteMatch();
    await checkTwilioSmsNormalization();
    await checkRingCentralCallNormalization();
  } finally {
    await postJson("/api/dev/seed-demo", {});
  }

  console.log("ok domain regression");
}

async function checkDuplicatePhoneMatch() {
  const result = await getJson("/api/contacts/duplicates/search?phone=%28503%29555-0141");
  assertEqual(result.match_strength, "exact", "phone duplicate strength");
  assertEqual(result.contact_summary.display_name, "Jordan Lee", "phone duplicate contact");
  assertIncludes(result.reason, "Phone number", "phone duplicate reason");
  console.log("ok duplicate phone");
}

async function checkDuplicateNameMatch() {
  const result = await getJson("/api/contacts/duplicates/search?name=Taylor%20Morgan");
  assertEqual(result.match_strength, "likely", "name duplicate strength");
  assertEqual(result.contact_summary.display_name, "Taylor Morgan", "name duplicate contact");
  assert(result.latest_quote_summary?.quote_number, "name duplicate includes latest quote summary");
  console.log("ok duplicate name");
}

async function checkDuplicateSiteMatch() {
  const result = await getJson(
    "/api/contacts/duplicates/search?address=200%20Market%20St%2C%20Sample%20City%2C%20ST&zip=10002"
  );
  assert(["exact", "likely"].includes(result.match_strength), "site duplicate strength should be exact or likely");
  assertEqual(result.contact_summary.display_name, "Taylor Morgan", "site duplicate contact");
  assertEqual(result.site_summary.address_line_1, "200 Market St", "site duplicate address");
  console.log("ok duplicate site");
}

async function checkTwilioSmsNormalization() {
  const providerMessageId = `domain-test-twilio-${Date.now()}`;
  const result = await postJson("/api/webhooks/twilio/sms/inbound", {
    From: "+15035559901",
    To: "+15035550000",
    Body: "Twilio normalized inbound text",
    NumMedia: "0",
    MessageSid: providerMessageId,
    MessagingServiceSid: "MG-domain-test"
  });

  assertEqual(result.message.provider_name, "twilio", "Twilio provider name");
  assertEqual(result.message.provider_message_id, providerMessageId, "Twilio provider message ID");
  assertEqual(result.message.provider_conversation_id, "MG-domain-test", "Twilio provider conversation ID");
  assertEqual(result.contact.status, "new_lead", "Twilio unknown sender creates lead shell");

  const timeline = await getJson(`/api/contacts/${result.contact.id}/timeline`);
  assert(timeline.some((activity: JsonRecord) => activity.activity_type === "message.inbound"), "Twilio text writes timeline activity");
  console.log("ok provider twilio sms");
}

async function checkRingCentralCallNormalization() {
  const providerCallId = `domain-test-rc-${Date.now()}`;
  const result = await postJson("/api/webhooks/ringcentral/calls/inbound", {
    fromNumber: "+15035559902",
    toNumber: "+15035550000",
    callId: providerCallId,
    sessionId: "rc-session-domain-test",
    status: "missed",
    durationSeconds: 0,
    voicemailUrl: "https://example.invalid/voicemail.wav"
  });

  assertEqual(result.provider_name, "ringcentral", "RingCentral provider name");
  assertEqual(result.provider_call_id, providerCallId, "RingCentral provider call ID");
  assertEqual(result.provider_conversation_id, "rc-session-domain-test", "RingCentral provider conversation ID");
  assertEqual(result.status, "missed", "RingCentral call status");

  const timeline = await getJson(`/api/contacts/${result.contact_id}/timeline`);
  assert(timeline.some((activity: JsonRecord) => activity.activity_type === "call.missed"), "RingCentral call writes timeline activity");
  console.log("ok provider ringcentral call");
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

function assertIncludes(actual: unknown, expected: string, message: string) {
  if (!String(actual ?? "").includes(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(actual)} to include ${JSON.stringify(expected)}`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
