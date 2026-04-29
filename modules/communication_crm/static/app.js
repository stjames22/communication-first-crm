const demoFallback = {
  contacts: [
    {
      id: "demo-maya",
      display_name: "Maya Rivera",
      mobile_phone: "+15035550161",
      email: "maya.rivera@example.com",
      status: "lead",
      primary_site: { address_line_1: "1842 SE Oak St", city: "Portland", state: "OR", zip: "97214" }
    },
    {
      id: "demo-priya",
      display_name: "Priya Patel",
      mobile_phone: "+15035550163",
      email: "priya.patel@example.com",
      status: "lead",
      primary_site: { address_line_1: "420 NE Fremont St", city: "Portland", state: "OR", zip: "97212" }
    },
    {
      id: "demo-noah",
      display_name: "Noah Chen",
      mobile_phone: "+15035550162",
      email: "noah.chen@example.com",
      status: "proposal_sent",
      primary_site: { address_line_1: "7309 N Willamette Blvd", city: "Portland", state: "OR", zip: "97203" }
    }
  ],
  conversations: [
    {
      id: "demo-conv-maya",
      contact_id: "demo-maya",
      channel_type: "sms",
      unread_count: 1,
      last_message_at: new Date().toISOString(),
      display_name: "Maya Rivera",
      mobile_phone: "+15035550161",
      email: "maya.rivera@example.com",
      last_message_body: "Text is best. Afternoon appointments work.",
      priority: "matched_contact",
      priority_score: 78
    },
    {
      id: "demo-conv-priya",
      contact_id: "demo-priya",
      channel_type: "sms",
      unread_count: 1,
      last_message_at: new Date(Date.now() - 23 * 60 * 1000).toISOString(),
      display_name: "Priya Patel",
      mobile_phone: "+15035550163",
      email: "priya.patel@example.com",
      last_message_body: "I missed your call. Looking for a proposal for monthly service.",
      priority: "matched_contact",
      priority_score: 86
    },
    {
      id: "demo-conv-noah",
      contact_id: "demo-noah",
      channel_type: "sms",
      unread_count: 1,
      last_message_at: new Date(Date.now() - 39 * 60 * 1000).toISOString(),
      display_name: "Noah Chen",
      mobile_phone: "+15035550162",
      email: "noah.chen@example.com",
      last_message_body: "Can you resend the proposal for the service plan?",
      priority: "matched_contact",
      priority_score: 72
    }
  ],
  quotes: [
    {
      id: "demo-quote-noah",
      contact_id: "demo-noah",
      quote_number: "CRM-DEMO-1001",
      title: "Service plan proposal",
      status: "sent",
      grand_total: 1940,
      updated_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
    }
  ],
  calls: []
};

const demoThreads = {
  "demo-conv-maya": [
    ["inbound", "Hi, can you help with a service appointment next week?", 90],
    ["outbound", "Yes. I can start a proposal today. Do you prefer text updates?", 84],
    ["inbound", "Text is best. Afternoon appointments work.", 12]
  ],
  "demo-conv-priya": [
    ["inbound", "I missed your call. Looking for a proposal for monthly service.", 35]
  ],
  "demo-conv-noah": [
    ["inbound", "Can you resend the proposal for the service plan?", 55],
    ["outbound", "Absolutely. I sent it again and can adjust the scope if needed.", 51]
  ]
};

const state = {
  contacts: [],
  conversations: [],
  conversation: null,
  contactDetail: null,
  quotes: [],
  calls: [],
  selectedConversationId: null,
  selectedContactId: null,
  search: "",
  loading: true,
  error: null,
  usingDemoFallback: false
};

const $ = (selector) => document.querySelector(selector);

document.addEventListener("click", handleClick);
$("#seed").addEventListener("click", seedDemo);
$("#message-form").addEventListener("submit", sendMessage);
$("#conversation-search").addEventListener("input", handleSearch);

loadAll();

async function loadAll() {
  state.loading = true;
  state.error = null;
  render();

  try {
    const [contacts, conversations, quotes, calls] = await Promise.all([
      getJson("/crm/api/contacts"),
      getJson("/crm/api/conversations"),
      getJson("/crm/api/quotes"),
      getJson("/crm/api/calls")
    ]);

    state.contacts = contacts;
    state.conversations = conversations.map(hydrateConversation);
    state.quotes = quotes;
    state.calls = calls;
    state.usingDemoFallback = false;
  } catch (error) {
    // Demo fallback keeps the beta usable when local/hosted APIs are unavailable.
    // Replace this with a real offline cache once production API behavior is stable.
    state.contacts = demoFallback.contacts;
    state.conversations = demoFallback.conversations;
    state.quotes = demoFallback.quotes;
    state.calls = demoFallback.calls;
    state.usingDemoFallback = true;
    toast("Using demo workspace data.");
  }

  if (!state.conversations.some((item) => item.id === state.selectedConversationId)) {
    state.selectedConversationId = filteredConversations()[0]?.id || state.conversations[0]?.id || null;
  }

  if (state.selectedConversationId) {
    await selectConversation(state.selectedConversationId, { renderAfter: false });
  } else {
    clearActiveCustomer();
  }

  state.loading = false;
  render();
}

function render() {
  renderInbox();
  renderWorkspace();
  renderSummary();
}

function renderInbox() {
  if (state.loading) {
    $("#conversations").innerHTML = loadingRows("Loading conversations...");
    return;
  }
  if (state.error) {
    $("#conversations").innerHTML = `<article class="empty-state error">${esc(state.error)}</article>`;
    return;
  }

  const conversations = filteredConversations();
  if (!state.conversations.length) {
    $("#conversations").innerHTML = `
      <article class="empty-state">
        <strong>No conversations yet.</strong>
        <p>Load demo data or wait for a call, text, or email to arrive.</p>
      </article>
    `;
    return;
  }
  if (!conversations.length) {
    $("#conversations").innerHTML = `
      <article class="empty-state">
        <strong>No matches.</strong>
        <p>Try a name, phone, email, or message phrase.</p>
      </article>
    `;
    return;
  }

  $("#conversations").innerHTML = conversations.map((item) => {
    const active = item.id === state.selectedConversationId ? "active" : "";
    const selected = item.id === state.selectedConversationId ? "true" : "false";
    const unread = Number(item.unread_count || 0);
    const priority = priorityText(item);
    return `
      <article class="conversation-card ${active}" data-conversation="${esc(item.id)}" data-contact="${esc(item.contact_id || "")}" aria-selected="${selected}" tabindex="0">
        <div class="conversation-topline">
          <strong>${esc(item.display_name || "Unknown contact")}</strong>
          <time>${esc(shortTime(item.last_message_at))}</time>
        </div>
        <p>${esc(item.last_message_body || "No messages yet")}</p>
        <div class="conversation-footline">
          <span class="channel-badge ${esc(channelClass(item.channel_type))}">${esc(channelLabel(item.channel_type))}</span>
          ${unread ? `<span class="count-badge">${unread} unread</span>` : ""}
          ${priority ? `<span class="priority-badge">${esc(priority)}</span>` : ""}
        </div>
      </article>
    `;
  }).join("");
}

function renderWorkspace() {
  const detail = state.conversation;
  const contact = activeContact();
  const latestQuote = activeQuote(contact);

  $("#workspace-title").textContent = contact?.display_name || "Customer Workspace";
  $("#workspace-kicker").textContent = detail
    ? "Customer Workspace"
    : state.loading
      ? "Loading"
      : "Select a customer";
  $("#workspace-meta").innerHTML = contact
    ? `<span>${esc(channelLabel(detail?.conversation?.channel_type))}</span><span>${esc(contactPoint(contact))}</span>${contact.email ? `<span>${esc(contact.email)}</span>` : ""}`
    : "";

  $("#quote-cta").innerHTML = contact
    ? latestQuote
      ? `<button type="button" class="quote-cta-button" data-action="open-quote">Open quote</button>`
      : `<button type="button" class="quote-cta-button" data-action="quote">Create quote for this customer</button>`
    : "";

  renderThreadMessages(detail);
}

function renderThreadMessages(detail) {
  if (state.loading) {
    $("#thread").innerHTML = loadingRows("Loading customer workspace...");
    setComposerDisabled(true);
    return;
  }
  if (state.error) {
    $("#thread").innerHTML = `<article class="empty-state error">The customer workspace could not load. Try refreshing the page.</article>`;
    setComposerDisabled(true);
    return;
  }
  if (!detail) {
    $("#thread").innerHTML = `<article class="empty-state">Choose a customer to see the active conversation.</article>`;
    setComposerDisabled(true);
    return;
  }
  if (!detail.messages.length) {
    $("#thread").innerHTML = `<article class="empty-state">No messages in this conversation yet.</article>`;
    setComposerDisabled(false);
    return;
  }

  $("#thread").innerHTML = detail.messages.map((message) => `
    <article class="message ${esc(message.direction)}">
      <p>${esc(message.body)}</p>
      <small>${esc(directionLabel(message.direction))} via ${esc(channelLabel(message.channel))} ${esc(fmt(message.created_at))}</small>
    </article>
  `).join("");
  setComposerDisabled(false);
  $("#thread").scrollTop = $("#thread").scrollHeight;
}

function renderSummary() {
  const detail = state.conversation;
  const contact = activeContact();

  if (state.loading) {
    $("#summary").innerHTML = loadingRows("Loading contact context...");
    return;
  }
  if (state.error) {
    $("#summary").innerHTML = `<article class="empty-state error">Contact context is unavailable.</article>`;
    return;
  }
  if (!detail) {
    $("#summary").innerHTML = `<article class="empty-state">Select a customer for contact details, recent activity, and next best action.</article>`;
    return;
  }
  if (!contact) {
    $("#summary").innerHTML = `
      <article class="empty-state">
        <strong>Create contact from this conversation.</strong>
        <p>No existing phone or email match is attached yet.</p>
        <button type="button" data-action="create-contact">Create contact from this conversation</button>
      </article>
    `;
    return;
  }

  const accountSummary = state.contactDetail?.account_summary || detail.account_summary;
  const latestQuote = activeQuote(contact);
  const timeline = dedupeActivity(state.contactDetail?.timeline || detail.timeline || []);
  const recentActivity = timeline.slice(0, 3);
  const hiddenActivityCount = Math.max(0, timeline.length - recentActivity.length);
  const nextAction = nextBestAction(contact, latestQuote, timeline, accountSummary);

  $("#summary").innerHTML = `
    <section class="summary-section contact-block">
      <strong>${esc(contact.display_name)}</strong>
      <p>${esc(contact.mobile_phone || "No phone on file")}</p>
      <p>${esc(contact.email || "No email on file")}</p>
      <p>${esc(site(contact.primary_site))}</p>
    </section>

    <section class="summary-section">
      <h3>Status</h3>
      <div class="status-grid">
        <span>Customer</span>
        <strong>${esc(displayStatus(contact, latestQuote, timeline))}</strong>
        <span>Quote</span>
        <strong>${esc(quoteStatus(latestQuote))}</strong>
      </div>
    </section>

    ${latestQuote ? `
      <section class="summary-section quote-summary">
        <h3>Linked Quote</h3>
        <strong>${esc(latestQuote.quote_number || latestQuote.title || `Quote ${latestQuote.id}`)}</strong>
        <p>${esc(quoteStatus(latestQuote))}</p>
      </section>
    ` : ""}

    <section class="summary-section next-action">
      <h3>Next Best Action</h3>
      <p>${esc(nextAction)}</p>
    </section>

    <section class="summary-section">
      <h3>Recent Activity</h3>
      ${recentActivity.length ? recentActivity.map((item) => `
        <article class="activity-item">
          <strong>${esc(item.title || activityType(item.activity_type))}${item.system_generated ? ` <span class="auto-badge">Auto</span>` : ""}</strong>
          ${activitySummary(item) ? `<p>${esc(activitySummary(item))}</p>` : ""}
          <small>${esc(fmt(item.created_at))}</small>
        </article>
      `).join("") : `<p class="muted">No recent activity yet.</p>`}
      ${hiddenActivityCount ? `<button type="button" class="text-button" data-action="view-activity">View all activity</button>` : ""}
    </section>
  `;
}

async function handleClick(event) {
  const conversation = event.target.closest("[data-conversation]");
  if (conversation) {
    await selectConversation(conversation.dataset.conversation, { contactId: conversation.dataset.contact });
    return;
  }

  const action = event.target.closest("[data-action]");
  if (action) {
    await handleQuickAction(action.dataset.action);
  }
}

async function selectConversation(conversationId, options = {}) {
  if (!conversationId) return;
  state.selectedConversationId = conversationId;
  state.conversation = null;
  state.contactDetail = null;
  if (options.renderAfter !== false) render();

  try {
    if (state.usingDemoFallback) {
      state.conversation = demoConversationDetail(conversationId);
    } else {
      state.conversation = await getJson(`/crm/api/conversations/${state.selectedConversationId}`);
    }

    const conversationContact = options.contactId || state.conversation?.contact?.id || state.conversation?.conversation?.contact_id;
    state.selectedContactId = resolveContactId(state.conversation, conversationContact);
    if (state.selectedContactId) {
      state.contactDetail = state.usingDemoFallback
        ? demoContactDetail(state.selectedContactId)
        : await getJson(`/crm/api/contacts/${state.selectedContactId}`);
    }
    state.conversations = state.conversations.map((item) =>
      item.id === conversationId ? { ...item, unread_count: 0 } : item
    );
    state.error = null;
  } catch (error) {
    state.error = error.message || "Unable to load that customer workspace.";
  }

  if (options.renderAfter !== false) render();
}

async function handleQuickAction(action) {
  const contact = activeContact();
  if (action === "send-text") {
    $("#message-form input[name='body']").focus();
    return;
  }
  if (action === "open-quote") {
    const quote = activeQuote(contact);
    toast(quote ? `${quote.quote_number || quote.title || "Quote"} is linked to this customer.` : "No linked quote yet.");
    return;
  }
  if (action === "quote") {
    if (!contact?.id) {
      toast("Select a matched contact before creating a quote.");
      return;
    }
    if (state.usingDemoFallback) {
      toast("Demo quote handoff is ready for backend wiring.");
      return;
    }
    try {
      const result = await postJson(`/api/contacts/${contact.id}/start-quote`, {});
      await refreshActiveConversation();
      toast(result.quote_url ? "Quote handoff started for this customer." : "Quote handoff started.");
    } catch (error) {
      toast(error.message || "Quote handoff could not start.");
    }
    return;
  }
  if (action === "follow-up") {
    if (!contact?.id) {
      toast("Select a matched contact before creating a follow-up.");
      return;
    }
    if (state.usingDemoFallback) {
      toast("Demo follow-up is ready for backend wiring.");
      return;
    }
    try {
      await postJson(`/crm/api/contacts/${contact.id}/follow-ups`, {
        title: `Follow up with ${contact.display_name || "customer"}`,
        priority: "normal"
      });
      await refreshActiveConversation();
      toast("Follow-up created.");
    } catch (error) {
      toast(error.message || "Follow-up could not be created.");
    }
    return;
  }
  if (action === "log-call") {
    toast("Call logging is ready for provider/backend wiring.");
    return;
  }
  if (action === "create-contact") {
    toast("Contact creation needs an explicit backend flow to avoid duplicates.");
    return;
  }
  if (action === "view-activity") {
    toast("Showing the latest activity here. Full activity view can wire to the timeline endpoint next.");
    return;
  }
  toast("Action unavailable.");
}

async function seedDemo() {
  try {
    await postJson("/crm/api/dev/seed-demo", {});
    state.selectedConversationId = null;
    state.selectedContactId = null;
    toast("Demo conversations loaded.");
    await loadAll();
  } catch (error) {
    toast(error.message || "Demo data could not be loaded.");
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = String(new FormData(form).get("body") || "").trim();
  if (!state.selectedConversationId || !body) return;

  try {
    if (state.usingDemoFallback) {
      appendDemoMessage(body);
    } else {
      await postJson(`/crm/api/conversations/${state.selectedConversationId}/messages`, { body });
    }
    form.reset();
    await refreshActiveConversation();
    toast("Reply added.");
  } catch (error) {
    toast(error.message || "Message could not be sent.");
  }
}

async function refreshActiveConversation() {
  if (!state.usingDemoFallback) {
    const [conversations, contacts, quotes, calls] = await Promise.all([
      getJson("/crm/api/conversations"),
      getJson("/crm/api/contacts"),
      getJson("/crm/api/quotes"),
      getJson("/crm/api/calls")
    ]);
    state.conversations = conversations.map(hydrateConversation);
    state.contacts = contacts;
    state.quotes = quotes;
    state.calls = calls;
  }
  if (state.selectedConversationId) {
    await selectConversation(state.selectedConversationId, { renderAfter: false });
  }
  render();
}

function handleSearch(event) {
  state.search = event.target.value;
  const visible = filteredConversations();
  if (visible.length && !visible.some((item) => item.id === state.selectedConversationId)) {
    selectConversation(visible[0].id, { contactId: visible[0].contact_id });
    return;
  }
  renderInbox();
}

function filteredConversations() {
  const query = normalizeText(state.search);
  if (!query) return state.conversations;
  return state.conversations.filter((item) => {
    const contact = contactForConversation(item);
    const haystack = [
      item.display_name,
      item.mobile_phone,
      item.email,
      item.last_message_body,
      contact?.display_name,
      contact?.mobile_phone,
      contact?.email
    ].map(normalizeText).join(" ");
    return haystack.includes(query);
  });
}

function hydrateConversation(item) {
  const contact = contactForConversation(item);
  return {
    ...item,
    display_name: item.display_name || contact?.display_name,
    mobile_phone: item.mobile_phone || contact?.mobile_phone,
    email: item.email || contact?.email
  };
}

function resolveContactId(detail, explicitContactId) {
  if (explicitContactId) return explicitContactId;
  const contact = detail?.contact;
  const matched = findMatchedContact(contact?.mobile_phone, contact?.email);
  return matched?.id || contact?.id || null;
}

function findMatchedContact(phone, email) {
  const normalizedPhone = normalizePhone(phone);
  const normalizedEmail = normalizeText(email);
  return state.contacts.find((contact) => {
    const phoneMatches = normalizedPhone && normalizePhone(contact.mobile_phone) === normalizedPhone;
    const emailMatches = normalizedEmail && normalizeText(contact.email) === normalizedEmail;
    return phoneMatches || emailMatches;
  }) || null;
}

function contactForConversation(item) {
  return state.contacts.find((contact) => contact.id === item.contact_id)
    || findMatchedContact(item.mobile_phone, item.email)
    || null;
}

function activeContact() {
  return state.contactDetail?.contact
    || state.conversation?.contact
    || state.contacts.find((contact) => contact.id === state.selectedContactId)
    || contactForConversation(state.conversations.find((item) => item.id === state.selectedConversationId) || {});
}

function activeQuote(contact = activeContact()) {
  if (!contact?.id) return null;
  return contact.latest_quote || (state.contactDetail?.quotes || []).find(Boolean) || findLatestQuote(contact.id);
}

function clearActiveCustomer() {
  state.conversation = null;
  state.contactDetail = null;
  state.selectedContactId = null;
}

function setComposerDisabled(disabled) {
  $("#message-form input").disabled = disabled;
  $("#message-form button").disabled = disabled;
}

function loadingRows(message) {
  return `
    <article class="empty-state loading">
      <span class="loader"></span>
      <p>${esc(message)}</p>
    </article>
  `;
}

function findLatestQuote(contactId) {
  if (!contactId) return null;
  return state.quotes.find((quote) => quote.contact_id === contactId) || null;
}

function nextBestAction(contact, latestQuote, recentActivity, accountSummary) {
  if (accountSummary?.recommended_next_action) return accountSummary.recommended_next_action;
  const unread = state.conversations.find((item) => item.contact_id === contact.id)?.unread_count;
  if (unread) return "Reply to the unread message before anything else.";
  if (latestQuote && ["sent", "awaiting_follow_up", "draft"].includes(latestQuote.status)) {
    return "Follow up on the active quote.";
  }
  if (recentActivity.some((item) => item.activity_type === "call.missed")) {
    return "Return the missed call and log the outcome.";
  }
  if (!contact.email || !contact.mobile_phone) {
    return "Complete the contact details before creating new work.";
  }
  return "Review the timeline and draft a response.";
}

function displayStatus(contact, latestQuote, timeline) {
  if (timeline.some((item) => item.activity_type === "follow_up.assigned")) return "Follow-up Needed";
  if (latestQuote) return "Quoted";
  const status = String(contact.status || "").toLowerCase();
  if (["active", "customer"].includes(status)) return "Active";
  return "Lead";
}

function priorityText(item) {
  if (item.priority && item.priority !== "new_contact") {
    return item.priority_score ? `Priority ${item.priority_score}` : "Priority";
  }
  const text = `${item.status || ""} ${item.last_message_body || ""}`.toLowerCase();
  if (text.includes("missed") || text.includes("urgent") || text.includes("quote")) return "Priority";
  return "";
}

function channelLabel(value) {
  const normalized = String(value || "sms").toLowerCase();
  if (normalized.includes("email")) return "Email";
  if (normalized.includes("call") || normalized.includes("voice")) return "Call";
  return "SMS";
}

function channelClass(value) {
  return channelLabel(value).toLowerCase();
}

function directionLabel(value) {
  return String(value || "").toLowerCase() === "outbound" ? "Sent" : "Received";
}

function quoteStatus(quote) {
  if (!quote) return "No linked quote";
  const total = Number(quote.grand_total || quote.total || 0).toFixed(2);
  return `${titleCase(quote.status || "quote")} - $${total}`;
}

function contactPoint(contact) {
  return contact?.mobile_phone || contact?.email || "No contact point";
}

function activityType(value) {
  return titleCase(String(value || "activity").replace(/[._-]/g, " "));
}

function activitySummary(item) {
  const type = String(item.activity_type || "");
  if (type.startsWith("message.")) return "";
  return item.body || "";
}

function dedupeActivity(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = `${item.activity_type}|${item.title}|${item.body}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function site(value) {
  if (!value) return "No service site";
  return [value.address_line_1, value.city, value.state, value.zip].filter(Boolean).join(", ");
}

function demoConversationDetail(conversationId) {
  const conversation = state.conversations.find((item) => item.id === conversationId);
  const contact = contactForConversation(conversation || {});
  const messages = (demoThreads[conversationId] || []).map(([direction, body, minutesAgo], index) => ({
    id: `${conversationId}-${index}`,
    direction,
    channel: conversation?.channel_type || "sms",
    body,
    delivery_status: direction === "outbound" ? "mock_sent" : "received",
    created_at: new Date(Date.now() - minutesAgo * 60 * 1000).toISOString()
  }));
  return {
    conversation,
    contact,
    messages,
    timeline: messages.slice().reverse().map((message) => ({
      id: `activity-${message.id}`,
      activity_type: `message.${message.direction}`,
      title: message.direction === "outbound" ? "Outbound reply" : "Inbound message",
      body: message.body,
      created_at: message.created_at
    }))
  };
}

function demoContactDetail(contactId) {
  const contact = state.contacts.find((item) => item.id === contactId);
  const conversation = state.conversations.find((item) => item.contact_id === contactId);
  const detail = demoConversationDetail(conversation?.id);
  return {
    contact,
    sites: contact?.primary_site ? [contact.primary_site] : [],
    conversations: conversation ? [conversation] : [],
    quotes: state.quotes.filter((quote) => quote.contact_id === contactId),
    tasks: [],
    account_summary: {},
    timeline: detail.timeline
  };
}

function appendDemoMessage(body) {
  const now = new Date().toISOString();
  if (!demoThreads[state.selectedConversationId]) demoThreads[state.selectedConversationId] = [];
  demoThreads[state.selectedConversationId].push(["outbound", body, 0]);
  state.conversations = state.conversations.map((item) =>
    item.id === state.selectedConversationId
      ? { ...item, last_message_body: body, last_message_at: now, last_message_direction: "outbound", unread_count: 0 }
      : item
  );
}

function fmt(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function shortTime(value) {
  if (!value) return "";
  const date = new Date(value);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat(undefined, sameDay
    ? { hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric" }
  ).format(date);
}

function titleCase(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizePhone(value) {
  return String(value || "").replace(/\D/g, "");
}

async function getJson(url) {
  const response = await fetch(url, { credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "CRM request failed");
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "CRM request failed");
  return data;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(toast.timeout);
  toast.timeout = setTimeout(() => node.classList.add("hidden"), 2200);
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
