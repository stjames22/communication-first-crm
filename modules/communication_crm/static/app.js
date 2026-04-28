const state = {
  dashboard: null,
  contacts: [],
  conversations: [],
  conversation: null,
  quotes: [],
  calls: [],
  links: [],
  selectedConversationId: null,
  selectedContactId: null,
  contactDetail: null
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

document.addEventListener("click", handleClick);
$("#seed").addEventListener("click", seedDemo);
$("#message-form").addEventListener("submit", sendMessage);

loadAll();

async function loadAll() {
  const [dashboard, contacts, conversations, quotes, calls, links] = await Promise.all([
    getJson("/crm/api/dashboard"),
    getJson("/crm/api/contacts"),
    getJson("/crm/api/conversations"),
    getJson("/crm/api/quotes"),
    getJson("/crm/api/calls"),
    getJson("/crm/api/external-links")
  ]);
  state.dashboard = dashboard;
  state.contacts = contacts;
  state.conversations = conversations;
  state.quotes = quotes;
  state.calls = calls;
  state.links = links;
  state.selectedConversationId ||= conversations[0]?.id || null;
  state.selectedContactId ||= contacts[0]?.id || null;
  if (state.selectedConversationId) {
    state.conversation = await getJson(`/crm/api/conversations/${state.selectedConversationId}`);
  }
  if (state.selectedContactId) {
    state.contactDetail = await getJson(`/crm/api/contacts/${state.selectedContactId}`);
  }
  render();
}

function render() {
  renderDashboard();
  renderInbox();
  renderContacts();
  renderQuotes();
  renderCalls();
}

function renderDashboard() {
  const metrics = state.dashboard?.metrics || {};
  const items = [
    ["Unread texts", metrics.unreadTexts || 0],
    ["Missed calls", metrics.missedCalls || 0],
    ["New leads", metrics.newLeads || 0],
    ["Quote follow-up", metrics.quotesAwaitingFollowUp || 0],
    ["Open tasks", metrics.tasksDueToday || 0]
  ];
  $("#metrics").innerHTML = items.map(([label, value]) => `<article class="card metric"><span>${esc(label)}</span><strong>${value}</strong></article>`).join("");
  $("#recent-conversations").innerHTML = rows(state.conversations.slice(0, 6), (item) => `
    <article class="row clickable" data-contact="${esc(item.contact_id)}" data-view-target="contacts">
      <strong>${esc(item.display_name)}</strong>
      <p>${esc(item.last_message_body || "No messages yet")}</p>
      <small>${esc(item.mobile_phone || "")} ${item.unread_count ? item.unread_count + " unread" : ""}</small>
    </article>
  `, false);
  $("#activity").innerHTML = rows(state.dashboard?.recentActivity || [], (item) => `
    <strong>${esc(item.title)}</strong>
    <p>${esc(item.body || "")}</p>
    <small>${esc(item.activity_type || "")} ${fmt(item.created_at)}</small>
  `);
  $("#external-links").innerHTML = rows(state.links, (item) => `
    <strong>${esc(item.external_system)} ${esc(item.external_id)}</strong>
    <p>${esc(item.internal_type)} ${esc(item.internal_id)}</p>
  `);
}

function renderInbox() {
  $("#conversations").innerHTML = rows(state.conversations, (item) => `
    <article class="row clickable ${item.id === state.selectedConversationId ? "active" : ""}" data-conversation="${esc(item.id)}">
      <strong>${esc(item.display_name)}</strong>
      <p>${esc(item.last_message_body || "No messages yet")}</p>
      <small>${esc(item.mobile_phone || "")} ${item.unread_count ? item.unread_count + " unread" : ""}</small>
    </article>
  `, false);

  const detail = state.conversation;
  $("#thread-title").textContent = detail?.contact?.display_name || "Thread";
  $("#thread").innerHTML = detail
    ? detail.messages.map((message) => `
      <article class="message ${esc(message.direction)}">
        ${esc(message.body)}
        <small>${esc(message.direction)} ${fmt(message.created_at)}</small>
      </article>
    `).join("")
    : `<p>No conversation selected.</p>`;

  $("#summary").innerHTML = detail
    ? `
      <strong>${esc(detail.contact.display_name)}</strong>
      <p>${esc(detail.contact.mobile_phone || "")}</p>
      <p>${esc(detail.contact.email || "No email")}</p>
      <p><span class="badge ${esc(detail.contact.status)}">${esc(detail.contact.status)}</span></p>
      <button type="button" data-start-quote="${esc(detail.contact.id)}">Start Quote</button>
      <p>${esc(site(detail.contact.primary_site))}</p>
      <h2>Timeline</h2>
      ${rows(detail.timeline || [], (item) => `<strong>${esc(item.title)}</strong><p>${esc(item.body || "")}</p>`)}
    `
    : "<p>Select a conversation.</p>";
}

function renderContacts() {
  $("#contact-list").innerHTML = rows(state.contacts, (item) => `
    <article class="row clickable ${item.id === state.selectedContactId ? "active" : ""}" data-contact="${esc(item.id)}">
      <strong>${esc(item.display_name)}</strong>
      <p>${esc(item.mobile_phone || "")} ${esc(item.email || "")}</p>
      <p>${esc(site(item.primary_site))}</p>
      <span class="badge ${esc(item.status)}">${esc(item.status)}</span>
    </article>
  `, false);

  const detail = state.contactDetail;
  $("#contact-title").textContent = detail?.contact?.display_name || "Contact Detail";
  $("#contact-detail").innerHTML = detail
    ? `
      <div class="contact-actions">
        <button type="button" data-start-quote="${esc(detail.contact.id)}">Start Quote</button>
      </div>
      <p>${esc(detail.contact.mobile_phone || "")}</p>
      <p>${esc(detail.contact.email || "No email")}</p>
      <p><span class="badge ${esc(detail.contact.status)}">${esc(detail.contact.status)}</span></p>
      <h2>Timeline</h2>
      ${rows(detail.timeline || [], (item) => `
        <strong>${esc(item.title)}</strong>
        <p>${esc(item.body || "")}</p>
        <small>${esc(item.activity_type || "")} ${fmt(item.created_at)}</small>
      `)}
    `
    : "<p>Select a contact.</p>";
}

function renderQuotes() {
  $("#quote-list").innerHTML = rows(state.quotes, (item) => `
    <strong>${esc(item.quote_number)} ${esc(item.title)}</strong>
    <p>$${Number(item.grand_total || 0).toFixed(2)}</p>
    <span class="badge ${esc(item.status)}">${esc(item.status)}</span>
  `);
}

function renderCalls() {
  $("#call-list").innerHTML = rows(state.calls, (item) => `
    <strong>${esc(item.direction)} ${esc(item.status)}</strong>
    <p>${esc(item.from_number)} to ${esc(item.to_number)}</p>
    <small>${fmt(item.started_at)}</small>
  `);
}

async function handleClick(event) {
  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    switchView(viewButton.dataset.view);
    return;
  }
  const conversation = event.target.closest("[data-conversation]");
  if (conversation) {
    state.selectedConversationId = conversation.dataset.conversation;
    state.conversation = await getJson(`/crm/api/conversations/${state.selectedConversationId}`);
    renderInbox();
  }
  const contact = event.target.closest("[data-contact]");
  if (contact) {
    state.selectedContactId = contact.dataset.contact;
    state.contactDetail = await getJson(`/crm/api/contacts/${state.selectedContactId}`);
    if (contact.dataset.viewTarget) {
      switchView(contact.dataset.viewTarget);
    }
    renderContacts();
    return;
  }
  const startQuote = event.target.closest("[data-start-quote]");
  if (startQuote) {
    const result = await postJson(`/api/contacts/${startQuote.dataset.startQuote}/start-quote`, {});
    toast("Quote handoff ready.");
    window.location.href = result.quote_url;
  }
}

function switchView(view) {
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === view));
  $$("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#title").textContent = { dashboard: "Dashboard", inbox: "Inbox", contacts: "Contacts", quotes: "Quotes", calls: "Calls" }[view] || "Communication CRM";
}

async function seedDemo() {
  await postJson("/crm/api/dev/seed-demo", {});
  toast("CRM demo data ready.");
  await loadAll();
}

async function sendMessage(event) {
  event.preventDefault();
  const body = new FormData(event.currentTarget).get("body");
  if (!state.selectedConversationId || !body) return;
  await postJson(`/crm/api/conversations/${state.selectedConversationId}/messages`, { body });
  event.currentTarget.reset();
  state.conversation = await getJson(`/crm/api/conversations/${state.selectedConversationId}`);
  toast("Outbound text logged in CRM timeline.");
  await loadAll();
}

function rows(items, render, wrap = true) {
  if (!items.length) return "<p>No CRM records yet.</p>";
  return items.map((item) => wrap ? `<article class="row">${render(item)}</article>` : render(item)).join("");
}

function site(value) {
  if (!value) return "No service site";
  return [value.address_line_1, value.city, value.state, value.zip].filter(Boolean).join(", ");
}

function fmt(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
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
  toast.timeout = setTimeout(() => node.classList.add("hidden"), 2600);
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
