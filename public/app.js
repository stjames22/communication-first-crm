const state = {
  view: "dashboard",
  dashboard: null,
  contacts: [],
  conversations: [],
  conversationDetail: null,
  contactDetail: null,
  calls: [],
  quotes: [],
  admin: {},
  selectedConversationId: null,
  selectedContactId: null,
  selectedQuoteId: null,
  contactTab: "overview",
  offline: false
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const quoteLineItems = [
  { name: "Bark mulch installation", quantity: 8, unitPrice: 145, itemType: "service" },
  { name: "Bed cleanup", quantity: 1, unitPrice: 485, itemType: "service" },
  { name: "Manual delivery access adjustment", quantity: 1, unitPrice: 200, itemType: "adjustment" }
];

document.addEventListener("click", handleDocumentClick);
$("#message-form").addEventListener("submit", handleSendMessage);
$("#quote-form").addEventListener("submit", handleSaveQuote);
$("#quote-contact").addEventListener("change", handleQuoteContactChange);
$("#conversation-search").addEventListener("input", renderConversations);
$("#seed-demo").addEventListener("click", handleSeedDemo);
$("#quick-quote").addEventListener("click", () => switchView("quote"));

void initialize();

async function initialize() {
  renderLineItems();
  await loadHealth();
  await loadWorkspace();
}

async function loadWorkspace() {
  try {
    const [
      dashboard,
      contacts,
      conversations,
      tasks,
      calls,
      quotes,
      users,
      templates,
      integrations,
      quoteDefaults,
      routing
    ] = await Promise.all([
      fetchJson("/api/dashboard"),
      fetchJson("/api/contacts"),
      fetchJson("/api/conversations"),
      fetchJson("/api/tasks"),
      fetchJson("/api/calls"),
      fetchJson("/api/quotes"),
      fetchJson("/api/settings/users"),
      fetchJson("/api/settings/templates"),
      fetchJson("/api/settings/integration-settings"),
      fetchJson("/api/settings/quote-defaults"),
      fetchJson("/api/settings/phone-routing")
    ]);

    state.dashboard = dashboard;
    state.contacts = contacts;
    state.conversations = conversations;
    state.tasks = tasks;
    state.calls = calls;
    state.quotes = quotes;
    state.admin = { users, templates, integrations, quoteDefaults, routing };
    state.offline = false;
  } catch (error) {
    state.offline = true;
    hydrateOfflineData();
    notify("API data is unavailable, showing built-in CRM sample.");
  }

  state.selectedConversationId ||= state.conversations[0]?.id || null;
  state.selectedContactId ||= state.contacts[0]?.id || state.conversations[0]?.contact_id || null;
  state.selectedQuoteId ||= state.quotes[0]?.id || null;

  await loadSelectedRecords();
  renderAll();
}

async function loadSelectedRecords() {
  if (state.offline) {
    state.conversationDetail = makeOfflineConversationDetail();
    state.contactDetail = makeOfflineContactDetail();
    return;
  }

  if (state.selectedConversationId) {
    try {
      state.conversationDetail = await fetchJson(`/api/conversations/${state.selectedConversationId}`);
    } catch {
      state.conversationDetail = null;
    }
  }

  if (state.selectedContactId) {
    try {
      state.contactDetail = await fetchJson(`/api/contacts/${state.selectedContactId}`);
    } catch {
      state.contactDetail = null;
    }
  }
}

async function loadHealth() {
  try {
    const health = await fetchJson("/health");
    $("#health").textContent = health.ok ? "API online" : "API degraded";
    $("#health").className = health.ok ? "health ok" : "health warn";
  } catch {
    $("#health").textContent = "API offline";
    $("#health").className = "health warn";
  }
}

function renderAll() {
  renderDashboard();
  renderConversations();
  renderThread();
  renderContactSummary("#inbox-summary", getActiveContactSummary());
  renderContactRecord();
  renderQuoteWorkspace();
  renderCalls();
  renderAdmin();
}

function renderDashboard() {
  const metrics = state.dashboard?.metrics || {};
  const metricItems = [
    ["Unread texts", metrics.unreadTexts ?? 0],
    ["Missed calls", metrics.missedCalls ?? 0],
    ["New leads", metrics.newLeads ?? 0],
    ["Quotes follow-up", metrics.quotesAwaitingFollowUp ?? 0],
    ["Tasks today", metrics.tasksDueToday ?? 0]
  ];

  $("#metrics").innerHTML = metricItems
    .map(([label, value]) => `<article class="panel metric"><span>${escapeHtml(label)}</span><strong>${value}</strong></article>`)
    .join("");

  renderTimeline("#dashboard-activity", state.dashboard?.recentActivity || []);

  $("#dashboard-queue").innerHTML =
    state.tasks.length === 0
      ? `<div class="empty">No open work yet.</div>`
      : state.tasks
          .slice(0, 8)
          .map(
            (task) => `
              <article class="list-item">
                <div class="row"><strong>${escapeHtml(task.title)}</strong><span class="badge draft">${escapeHtml(task.priority || "normal")}</span></div>
                <small>${escapeHtml(task.display_name || "Contact")} ${task.due_at ? "due " + formatDate(task.due_at) : "no due date"}</small>
              </article>`
          )
          .join("");
}

function renderConversations() {
  const search = ($("#conversation-search").value || "").toLowerCase();
  const conversations = state.conversations.filter((conversation) =>
    [conversation.display_name, conversation.mobile_phone, conversation.last_message_body]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(search)
  );

  $("#conversation-list").innerHTML =
    conversations.length === 0
      ? `<div class="empty">No conversations yet.</div>`
      : conversations
          .map(
            (conversation) => `
              <article class="conversation-card ${conversation.id === state.selectedConversationId ? "active" : ""}" data-conversation-id="${conversation.id}">
                <div class="row">
                  <strong>${escapeHtml(conversation.display_name || "Unknown contact")}</strong>
                  ${conversation.unread_count ? `<span class="badge draft">${conversation.unread_count} unread</span>` : ""}
                </div>
                <p>${escapeHtml(conversation.last_message_body || "No messages yet")}</p>
                <small>${escapeHtml(conversation.mobile_phone || "")} ${conversation.sort_at ? formatDate(conversation.sort_at) : ""}</small>
              </article>`
          )
          .join("");
}

function renderThread() {
  const detail = state.conversationDetail;
  if (!detail) {
    $("#thread-header").innerHTML = `<div><strong>Select a conversation</strong><small>Thread will appear here.</small></div>`;
    $("#thread").innerHTML = `<div class="empty">No thread selected.</div>`;
    return;
  }

  $("#thread-header").innerHTML = `
    <div>
      <strong>${escapeHtml(detail.contactSummary.display_name)}</strong>
      <small>${escapeHtml(detail.contactSummary.mobile_phone)} ${detail.contactSummary.latest_quote ? quoteBadge(detail.contactSummary.latest_quote.status) : ""}</small>
    </div>
    <button data-action="add-task" type="button">Create Task</button>
  `;

  $("#thread").innerHTML =
    detail.messages.length === 0
      ? `<div class="empty">No messages yet.</div>`
      : detail.messages
          .map(
            (message) => `
              <article class="message ${message.direction}">
                ${escapeHtml(message.body)}
                <small>${escapeHtml(message.direction)} ${formatDate(message.created_at)} ${escapeHtml(message.delivery_status || "")}</small>
              </article>`
          )
          .join("");
}

function renderContactSummary(targetSelector, summary) {
  const target = $(targetSelector);
  if (!summary) {
    target.innerHTML = `<div class="empty">Select a contact to see context.</div>`;
    return;
  }

  target.innerHTML = `
    <div class="summary-card">
      <p class="eyebrow">Contact summary</p>
      <h3>${escapeHtml(summary.display_name)}</h3>
      <div class="summary-line"><span>Phone</span><strong>${escapeHtml(summary.mobile_phone || "")}</strong></div>
      <div class="summary-line"><span>Email</span><strong>${escapeHtml(summary.email || "No email")}</strong></div>
      <div class="summary-line"><span>Site</span><strong>${escapeHtml(formatSite(summary.primary_site))}</strong></div>
      <div class="tags">${(summary.tags || []).map((tag) => `<span class="tag" style="background:${escapeAttr(tag.color || "#64748b")}">${escapeHtml(tag.name)}</span>`).join("")}</div>
      <div class="summary-line"><span>Quote</span><strong>${summary.latest_quote ? `${escapeHtml(summary.latest_quote.quote_number)} ${quoteBadge(summary.latest_quote.status)}` : "No quote"}</strong></div>
      <div class="quick-actions">
        <button data-action="call" type="button">Call</button>
        <button data-action="text" type="button">Text</button>
        <button data-action="note" type="button">Add Note</button>
        <button data-action="add-task" type="button">Create Task</button>
        <button data-action="quote" type="button">Create Quote</button>
      </div>
    </div>
  `;
}

function renderContactRecord() {
  const detail = state.contactDetail;
  if (!detail) {
    $("#contact-header").innerHTML = `<div><strong>No contact selected</strong></div>`;
    $("#contact-tab-content").innerHTML = `<div class="empty">Choose a conversation or load seed data.</div>`;
    renderTimeline("#contact-timeline", []);
    return;
  }

  const { contact } = detail;
  $("#contact-header").innerHTML = `
    <div>
      <strong>${escapeHtml(contact.display_name)}</strong>
      <small>${escapeHtml(contact.mobile_phone)} ${escapeHtml(contact.email || "")}</small>
    </div>
    <span class="badge">${escapeHtml(contact.status)}</span>
  `;

  $$("#contact-tabs .tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === state.contactTab));

  const content = {
    overview: renderKeyValues([
      ["Status", contact.status],
      ["Assigned", contact.assigned_user_name || "Unassigned"],
      ["Preferred", contact.preferred_contact_method],
      ["Source", contact.source || "Unknown"]
    ]),
    conversation: renderSimpleList(detail.conversations, (item) => `${item.channel_type} conversation, ${item.status}`),
    calls: renderSimpleList(detail.calls, (item) => `${item.direction} ${item.status} ${item.disposition || ""}`),
    quotes: renderSimpleList(detail.quotes, (item) => `${item.quote_number} ${quoteBadge(item.status)} ${formatCurrency(item.grand_total)}`),
    sites: renderSimpleList(detail.sites, (item) => `${item.label}: ${formatSite(item)}`),
    notes: renderSimpleList(detail.notes, (item) => item.body || item.title),
    tasks: renderSimpleList(detail.tasks, (item) => `${item.title} ${item.due_at ? "due " + formatDate(item.due_at) : ""}`),
    attachments: renderSimpleList(detail.attachments, (item) => `${item.file_name} ${item.mime_type}`)
  };

  $("#contact-tab-content").innerHTML = content[state.contactTab] || content.overview;
  renderTimeline("#contact-timeline", state.offline ? state.dashboard.recentActivity : []);
  if (!state.offline && state.selectedContactId) {
    fetchJson(`/api/contacts/${state.selectedContactId}/timeline`)
      .then((timeline) => renderTimeline("#contact-timeline", timeline))
      .catch(() => renderTimeline("#contact-timeline", []));
  }
}

function renderQuoteWorkspace() {
  const contactSelect = $("#quote-contact");
  contactSelect.innerHTML = state.contacts
    .map((contact) => `<option value="${escapeAttr(contact.id)}">${escapeHtml(contact.display_name)}</option>`)
    .join("");
  contactSelect.value = state.selectedContactId || state.contacts[0]?.id || "";
  renderQuoteSiteOptions();
  renderQuoteReview();

  const quote = state.quotes.find((item) => item.id === state.selectedQuoteId) || state.quotes[0];
  const summary = getSummaryForContact(contactSelect.value);
  renderContactSummary("#quote-summary", {
    ...summary,
    latest_quote: quote || summary?.latest_quote || null
  });
}

function renderQuoteSiteOptions() {
  const contactId = $("#quote-contact").value || state.selectedContactId;
  const contact = state.contacts.find((item) => item.id === contactId);
  const sites = contact?.sites || state.contactDetail?.sites || [contact?.primary_site].filter(Boolean);
  $("#quote-site").innerHTML = sites
    .filter(Boolean)
    .map((site) => `<option value="${escapeAttr(site.id)}">${escapeHtml(site.label || "Site")} - ${escapeHtml(formatSite(site))}</option>`)
    .join("");
}

function renderQuoteReview() {
  const delivery = Number($(`#quote-form [name="deliveryTotal"]`)?.value || 0);
  const tax = Number($(`#quote-form [name="taxTotal"]`)?.value || 0);
  const subtotal = quoteLineItems.reduce((sum, item) => sum + Number(item.quantity) * Number(item.unitPrice), 0);
  $("#quote-review").innerHTML = renderKeyValues([
    ["Subtotal", formatCurrency(subtotal)],
    ["Delivery", formatCurrency(delivery)],
    ["Tax", formatCurrency(tax)],
    ["Grand total", formatCurrency(subtotal + delivery + tax)]
  ]);
}

function renderLineItems() {
  $("#line-items").innerHTML = quoteLineItems
    .map(
      (item, index) => `
        <div class="line-item">
          <input data-line="${index}" data-field="name" value="${escapeAttr(item.name)}" />
          <input data-line="${index}" data-field="quantity" type="number" value="${item.quantity}" />
          <input data-line="${index}" data-field="unitPrice" type="number" value="${item.unitPrice}" />
        </div>`
    )
    .join("");

  $("#line-items").addEventListener("input", (event) => {
    const input = event.target;
    const index = Number(input.dataset.line);
    const field = input.dataset.field;
    if (Number.isInteger(index) && field) {
      quoteLineItems[index][field] = field === "name" ? input.value : Number(input.value || 0);
      renderQuoteReview();
    }
  });
  $("#quote-form").addEventListener("input", renderQuoteReview);
}

function renderCalls() {
  $("#calls-list").innerHTML =
    state.calls.length === 0
      ? `<div class="empty">No calls logged.</div>`
      : state.calls
          .map(
            (call) => `
              <article class="list-item">
                <div class="row">
                  <strong>${escapeHtml(call.display_name || call.from_number || "Unknown")}</strong>
                  <span class="badge ${call.status === "missed" ? "declined" : ""}">${escapeHtml(call.status)}</span>
                </div>
                <p>${escapeHtml(call.disposition || "No disposition")} ${call.notes ? "- " + escapeHtml(call.notes) : ""}</p>
                <small>${escapeHtml(call.direction)} ${formatDate(call.started_at)} ${call.duration_seconds || 0}s</small>
              </article>`
          )
          .join("");

  renderContactSummary("#calls-summary", getSummaryForContact(state.selectedContactId) || getActiveContactSummary());
}

function renderAdmin() {
  $("#admin-users").innerHTML = renderSimpleList(state.admin.users || [], (item) => `${item.full_name} - ${item.role}`);
  $("#admin-templates").innerHTML = renderSimpleList(state.admin.templates || [], (item) => `${item.name}: ${item.body}`);
  $("#admin-integrations").innerHTML = renderSimpleList(state.admin.integrations?.persisted || [], (item) => `${item.provider_type}: ${item.provider_name} ${item.enabled ? "enabled" : "disabled"}`);
  $("#admin-quote-defaults").innerHTML = renderSimpleList(state.admin.quoteDefaults || [], (item) => `${item.label}: delivery ${formatCurrency(item.default_delivery_total)}`);
  $("#admin-routing").innerHTML = renderSimpleList(state.admin.routing || [], (item) => `${item.label}: ${item.inbound_number} to ${item.destination_value}`);
}

async function handleDocumentClick(event) {
  const nav = event.target.closest("[data-view]");
  if (nav) {
    switchView(nav.dataset.view);
    return;
  }

  const conversation = event.target.closest("[data-conversation-id]");
  if (conversation) {
    state.selectedConversationId = conversation.dataset.conversationId;
    const selected = state.conversations.find((item) => item.id === state.selectedConversationId);
    state.selectedContactId = selected?.contact_id || state.selectedContactId;
    await loadSelectedRecords();
    renderAll();
    return;
  }

  const tab = event.target.closest("[data-tab]");
  if (tab) {
    state.contactTab = tab.dataset.tab;
    renderContactRecord();
    return;
  }

  const quoteAction = event.target.closest("[data-quote-action]");
  if (quoteAction && quoteAction.dataset.quoteAction !== "save") {
    await handleQuoteAction(quoteAction.dataset.quoteAction);
    return;
  }

  const action = event.target.closest("[data-action]");
  if (action) {
    await handleQuickAction(action.dataset.action);
  }
}

function switchView(view) {
  state.view = view;
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `${view}-view`));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#view-title").textContent = {
    dashboard: "Dashboard",
    inbox: "Shared Inbox",
    contact: "Contact Record",
    quote: "Quote Workspace",
    calls: "Calls",
    admin: "Admin"
  }[view];
}

async function handleSeedDemo() {
  try {
    await fetchJson("/api/dev/seed-demo", { method: "POST" });
    notify("Seed data loaded.");
    state.offline = false;
    await loadWorkspace();
  } catch (error) {
    notify(getErrorMessage(error));
  }
}

async function handleSendMessage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = new FormData(form).get("body");

  if (!state.selectedConversationId || !body) {
    return;
  }

  if (state.offline) {
    state.conversationDetail.messages.push({
      id: `offline-${Date.now()}`,
      direction: "outbound",
      body,
      delivery_status: "mock",
      created_at: new Date().toISOString()
    });
    form.reset();
    renderThread();
    notify("Mock text added to thread.");
    return;
  }

  try {
    await fetchJson(`/api/conversations/${state.selectedConversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body })
    });
    form.reset();
    await loadWorkspace();
    notify("Text sent and activity logged.");
  } catch (error) {
    notify(getErrorMessage(error));
  }
}

async function handleSaveQuote(event) {
  event.preventDefault();
  await createOrUpdateQuote("save");
}

async function handleQuoteAction(action) {
  if (["version", "sms", "email", "pdf", "accept", "decline"].includes(action) && !state.selectedQuoteId) {
    await createOrUpdateQuote("save");
  }

  if (action === "version") {
    await createOrUpdateQuote("version");
    return;
  }

  if (action === "duplicate") {
    state.selectedQuoteId = null;
    notify("Quote duplicated into a fresh draft workspace.");
    return;
  }

  if (action === "pdf") {
    if (state.selectedQuoteId) {
      window.open(`/api/quotes/${state.selectedQuoteId}/pdf`, "_blank");
    }
    return;
  }

  const endpointByAction = {
    sms: "send-sms",
    email: "send-email",
    accept: "accept",
    decline: "decline"
  };

  if (!endpointByAction[action] || state.offline) {
    notify("Action recorded in the mock workspace.");
    return;
  }

  try {
    await fetchJson(`/api/quotes/${state.selectedQuoteId}/${endpointByAction[action]}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    await loadWorkspace();
    notify("Quote event saved to the activity timeline.");
  } catch (error) {
    notify(getErrorMessage(error));
  }
}

async function createOrUpdateQuote(mode) {
  const form = $("#quote-form");
  const data = new FormData(form);
  const payload = {
    contactId: data.get("contactId"),
    serviceSiteId: data.get("serviceSiteId"),
    title: data.get("title"),
    notes: data.get("notes"),
    deliveryTotal: Number(data.get("deliveryTotal") || 0),
    taxTotal: Number(data.get("taxTotal") || 0),
    lineItems: quoteLineItems
  };

  if (state.offline) {
    notify(mode === "version" ? "Mock quote version saved." : "Mock quote draft saved.");
    return;
  }

  try {
    const url = mode === "version" && state.selectedQuoteId ? `/api/quotes/${state.selectedQuoteId}/versions` : "/api/quotes";
    const result = await fetchJson(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    state.selectedQuoteId = result.quote?.id || result.id || state.selectedQuoteId;
    await loadWorkspace();
    notify(mode === "version" ? "New quote version saved." : "Quote draft saved.");
  } catch (error) {
    notify(getErrorMessage(error));
  }
}

function handleQuoteContactChange() {
  state.selectedContactId = $("#quote-contact").value;
  renderQuoteSiteOptions();
  renderQuoteWorkspace();
}

async function handleQuickAction(action) {
  if (action === "quote") {
    switchView("quote");
    return;
  }

  if (action === "text") {
    switchView("inbox");
    $("#message-form input").focus();
    return;
  }

  if (action === "call") {
    if (state.offline || !state.selectedContactId) {
      notify("Call would start through integration_service.");
      return;
    }
    await fetchJson("/api/calls/outbound", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contactId: state.selectedContactId })
    });
    await loadWorkspace();
    notify("Outbound call placeholder logged.");
    return;
  }

  if (action === "add-task") {
    notify("Task creation route is ready at POST /tasks.");
    return;
  }

  if (action === "note") {
    notify("Notes write to the unified activity timeline at POST /contacts/{id}/notes.");
  }
}

function getActiveContactSummary() {
  return state.conversationDetail?.contactSummary || getSummaryForContact(state.selectedContactId);
}

function getSummaryForContact(contactId) {
  const contact = state.contacts.find((item) => item.id === contactId);
  if (!contact) {
    return null;
  }
  return {
    id: contact.id,
    display_name: contact.display_name,
    mobile_phone: contact.mobile_phone,
    email: contact.email,
    primary_site: contact.primary_site || contact.sites?.[0] || null,
    tags: contact.tags || [],
    latest_quote: contact.latest_quote || state.quotes.find((quote) => quote.contact_id === contact.id) || null
  };
}

function renderTimeline(selector, activities) {
  $(selector).innerHTML =
    activities.length === 0
      ? `<div class="empty">No timeline activity yet.</div>`
      : activities
          .map(
            (activity) => `
              <article class="timeline-item">
                <strong>${escapeHtml(activity.title)}</strong>
                <p>${escapeHtml(activity.body || activity.display_name || "")}</p>
                <small>${escapeHtml(activity.activity_type || "")} ${formatDate(activity.created_at)}</small>
              </article>`
          )
          .join("");
}

function renderSimpleList(items, mapItem) {
  return items.length === 0
    ? `<div class="empty">Nothing here yet.</div>`
    : items.map((item) => `<article class="list-item">${mapItem(item)}<small>${item.created_at ? formatDate(item.created_at) : ""}</small></article>`).join("");
}

function renderKeyValues(rows) {
  return rows
    .map(([label, value]) => `<div class="summary-line"><span>${escapeHtml(label)}</span><strong>${value || "None"}</strong></div>`)
    .join("");
}

function quoteBadge(status) {
  return `<span class="badge ${escapeAttr(status || "draft")}">${escapeHtml(status || "draft")}</span>`;
}

function formatSite(site) {
  if (!site) {
    return "No service site";
  }
  return [site.address_line_1, site.city, site.state, site.zip].filter(Boolean).join(", ");
}

function formatDate(value) {
  if (!value) {
    return "";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatCurrency(value) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(Number(value || 0));
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function notify(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(notify.timeout);
  notify.timeout = window.setTimeout(() => toast.classList.add("hidden"), 3200);
}

function getErrorMessage(error) {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

function hydrateOfflineData() {
  const now = new Date().toISOString();
  const site = {
    id: "site-demo",
    label: "Home",
    address_line_1: "2217 SE Alder St",
    city: "Portland",
    state: "OR",
    zip: "97214",
    delivery_zone: "Central"
  };
  const contact = {
    id: "contact-demo",
    display_name: "Kyle Bennett",
    mobile_phone: "+15035550141",
    email: "kyle@example.com",
    status: "lead",
    source: "website",
    preferred_contact_method: "sms",
    primary_site: site,
    sites: [site],
    tags: [{ name: "New Lead", color: "#2563eb" }],
    latest_quote: { id: "quote-demo", quote_number: "BBQ-2026-0001", status: "draft", grand_total: 1940 }
  };
  state.contacts = [contact];
  state.conversations = [
    {
      id: "conversation-demo",
      contact_id: contact.id,
      display_name: contact.display_name,
      mobile_phone: contact.mobile_phone,
      unread_count: 1,
      last_message_body: "Please include delivery and a simple edging option.",
      sort_at: now
    }
  ];
  state.calls = [
    {
      id: "call-demo",
      contact_id: contact.id,
      display_name: contact.display_name,
      direction: "inbound",
      status: "missed",
      started_at: now,
      duration_seconds: 0
    }
  ];
  state.quotes = [
    {
      id: "quote-demo",
      contact_id: contact.id,
      quote_number: "BBQ-2026-0001",
      title: "Barkboys backyard refresh",
      status: "draft",
      grand_total: 1940
    }
  ];
  state.tasks = [
    {
      id: "task-demo",
      contact_id: contact.id,
      display_name: contact.display_name,
      title: "Reply with Barkboys quote draft",
      priority: "high",
      due_at: now
    }
  ];
  state.dashboard = {
    metrics: {
      unreadTexts: 1,
      missedCalls: 1,
      newLeads: 1,
      quotesAwaitingFollowUp: 1,
      tasksDueToday: 1
    },
    recentActivity: [
      {
        title: "Inbound text received",
        body: "Please include delivery and a simple edging option.",
        activity_type: "message.inbound",
        created_at: now
      },
      {
        title: "Missed call",
        body: "Missed inbound call from Kyle Bennett.",
        activity_type: "call.missed",
        created_at: now
      }
    ]
  };
  state.admin = {
    users: [{ full_name: "Jamie Stone", role: "admin" }],
    templates: [{ name: "Quote ready", body: "Your Barkboys quote is ready." }],
    integrations: { persisted: [{ provider_type: "sms", provider_name: "twilio", enabled: false }] },
    quoteDefaults: [{ label: "Barkboys default", default_delivery_total: 95 }],
    routing: [{ label: "Main Barkboys line", inbound_number: "+15035550000", destination_value: "sales" }]
  };
}

function makeOfflineConversationDetail() {
  const contact = state.contacts[0];
  return {
    contactSummary: getSummaryForContact(contact.id),
    messages: [
      {
        id: "m1",
        direction: "inbound",
        body: "Hi, can Barkboys quote mulch and cleanup for my front beds?",
        delivery_status: "received",
        created_at: new Date(Date.now() - 30 * 60 * 1000).toISOString()
      },
      {
        id: "m2",
        direction: "outbound",
        body: "Absolutely. I can build that from your site notes and send a quote here.",
        delivery_status: "sent",
        created_at: new Date(Date.now() - 20 * 60 * 1000).toISOString()
      },
      {
        id: "m3",
        direction: "inbound",
        body: "Great. Please include delivery and a simple edging option.",
        delivery_status: "received",
        created_at: new Date(Date.now() - 12 * 60 * 1000).toISOString()
      }
    ],
    recentActivity: state.dashboard.recentActivity
  };
}

function makeOfflineContactDetail() {
  const contact = state.contacts[0];
  return {
    contact,
    sites: contact.sites,
    conversations: state.conversations,
    calls: state.calls,
    quotes: state.quotes,
    notes: [],
    tasks: state.tasks,
    attachments: [],
    tags: contact.tags
  };
}
