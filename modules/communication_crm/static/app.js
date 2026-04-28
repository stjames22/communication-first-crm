const state = {
  contacts: [],
  conversations: [],
  conversation: null,
  quotes: [],
  calls: [],
  selectedConversationId: null,
  selectedContactId: null,
  contactDetail: null,
  activeView: "conversations"
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

document.addEventListener("click", handleClick);
$("#seed").addEventListener("click", seedDemo);
$("#message-form").addEventListener("submit", sendMessage);
$("#note-form").addEventListener("submit", saveContactNote);

loadAll();

async function loadAll() {
  const [contacts, conversations, quotes, calls] = await Promise.all([
    getJson("/crm/api/contacts"),
    getJson("/crm/api/conversations"),
    getJson("/crm/api/quotes"),
    getJson("/crm/api/calls")
  ]);

  state.contacts = contacts;
  state.conversations = conversations;
  state.quotes = quotes;
  state.calls = calls;
  state.selectedConversationId ||= conversations[0]?.id || null;

  if (state.selectedConversationId) {
    await selectConversation(state.selectedConversationId);
  } else {
    state.conversation = null;
    state.selectedContactId = contacts[0]?.id || null;
    if (state.selectedContactId) await selectContact(state.selectedContactId);
  }

  render();
}

function render() {
  renderConversations();
  renderContacts();
  renderQuotes();
  renderCalls();
}

function renderConversations() {
  $("#conversation-list").innerHTML = rows(state.conversations, (item) => `
    <article class="conversation-row ${item.id === state.selectedConversationId ? "active" : ""}" data-conversation="${esc(item.id)}" data-contact="${esc(item.contact_id)}">
      <div>
        <strong>${esc(item.display_name)}${priorityBadge(item)}</strong>
        <p>${esc(item.last_message_body || "No messages yet")}</p>
      </div>
      <small>${fmt(item.last_message_at)}</small>
    </article>
  `, false);

  const contact = state.contactDetail?.contact || state.conversation?.contact;
  $("#active-contact-name").textContent = contact?.display_name || "Select a conversation";
  $("#active-contact-meta").textContent = contact
    ? [contact.mobile_phone, contact.email].filter(Boolean).join("  |  ") || "No phone or email"
    : "Recent customer communication will appear here.";
  $("#active-actions").classList.toggle("hidden", !contact);

  renderAccountSummary();
  renderThread();
}

function renderAccountSummary() {
  const summary = state.contactDetail?.account_summary;
  const node = $("#account-summary");
  if (!summary) {
    node.classList.add("hidden");
    node.innerHTML = "";
    return;
  }

  const followups = summary.open_followups || [];
  node.classList.remove("hidden");
  node.innerHTML = `
    <div>
      <strong>Account Summary ${priorityBadge(summary)}</strong>
      <p>${esc(summary.summary || "No account history yet.")}</p>
    </div>
    <div class="summary-grid">
      <span><b>Last</b>${summary.last_interaction_date ? fmt(summary.last_interaction_date) : "None"}</span>
      <span><b>Follow-ups</b>${followups.length ? esc(followups[0].title) : "None open"}</span>
      <span><b>Next</b>${esc(summary.recommended_next_action || "Review timeline and reply.")}</span>
    </div>
  `;
}

function renderThread() {
  const timeline = [...(state.contactDetail?.timeline || [])].reverse();
  $("#thread").innerHTML = timeline.length
    ? timeline.map(renderTimelineItem).join("")
    : `<p class="empty-state">Select a conversation to view the thread.</p>`;
}

function renderTimelineItem(item) {
  const kind = timelineKind(item);
  const label = item.system_generated ? `<span class="auto-badge">Auto</span>` : "";
  return `
    <article class="thread-item ${esc(kind)} ${item.system_generated ? "system-generated" : ""}">
      <div class="thread-bubble">
        <div class="thread-meta">
          <strong>${esc(threadTitle(item, kind))}${label}</strong>
          <small>${fmt(item.created_at)}</small>
        </div>
        <p>${esc(item.body || item.title || "")}</p>
      </div>
    </article>
  `;
}

function timelineKind(item) {
  if (item.system_generated) return "system";
  if (item.activity_type === "message.inbound") return "inbound";
  if (item.activity_type === "message.outbound") return "outbound";
  if (item.activity_type === "note.added") return "note";
  return "system";
}

function threadTitle(item, kind) {
  if (kind === "inbound") return "Customer";
  if (kind === "outbound") return "Team";
  if (kind === "note") return "Note";
  return item.title || "System";
}

function renderContacts() {
  $("#contact-list").innerHTML = rows(state.contacts, (item) => `
    <article class="row clickable ${item.id === state.selectedContactId ? "active" : ""}" data-contact="${esc(item.id)}">
      <strong>${esc(item.display_name)}</strong>
      <p>${esc([item.mobile_phone, item.email].filter(Boolean).join("  |  "))}</p>
      <span class="badge">${esc(item.status)}</span>
    </article>
  `, false);

  const detail = state.contactDetail;
  $("#contact-title").textContent = detail?.contact?.display_name || "Contact Detail";
  $("#contact-detail").innerHTML = detail?.contact
    ? `
      <section class="account-summary-card">
        <strong>Account Summary ${priorityBadge(detail.account_summary)}</strong>
        <p>${esc(detail.account_summary?.summary || "No account history yet.")}</p>
        <small>${esc(detail.account_summary?.recommended_next_action || "")}</small>
      </section>
      <div class="compact-timeline">
        ${[...(detail.timeline || [])].slice(0, 12).map((item) => `
          <article class="row">
            <strong>${esc(item.title)}${item.system_generated ? ` <span class="auto-badge">Auto</span>` : ""}</strong>
            <p>${esc(item.body || "")}</p>
            <small>${fmt(item.created_at)}</small>
          </article>
        `).join("")}
      </div>
    `
    : "<p>Select a contact.</p>";
}

function renderQuotes() {
  $("#quote-list").innerHTML = rows(state.quotes, (item) => `
    <strong>${esc(item.quote_number)} ${esc(item.title)}</strong>
    <p>$${Number(item.grand_total || 0).toFixed(2)}</p>
    <span class="badge">${esc(item.status)}</span>
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

  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    await handleAction(actionButton.dataset.action);
    return;
  }

  const conversation = event.target.closest("[data-conversation]");
  if (conversation) {
    await selectConversation(conversation.dataset.conversation, conversation.dataset.contact);
    render();
    return;
  }

  const contact = event.target.closest("[data-contact]");
  if (contact) {
    await selectContact(contact.dataset.contact);
    render();
  }
}

function switchView(view) {
  state.activeView = view;
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === view));
  $$("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
}

async function seedDemo() {
  await postJson("/crm/api/dev/seed-demo", {});
  state.selectedConversationId = null;
  state.selectedContactId = null;
  await loadAll();
  toast("Demo conversations loaded.");
}

async function selectConversation(conversationId, contactId) {
  if (!conversationId) return;
  state.selectedConversationId = conversationId;
  state.conversation = await getJson(`/crm/api/conversations/${conversationId}`);
  await selectContact(contactId || state.conversation?.contact?.id);
}

async function selectContact(contactId) {
  if (!contactId) return;
  state.selectedContactId = contactId;
  state.contactDetail = await getJson(`/crm/api/contacts/${contactId}`);
}

async function sendMessage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = new FormData(form).get("body");
  if (!state.selectedConversationId || !body) return;
  await postJson(`/crm/api/conversations/${state.selectedConversationId}/messages`, { body });
  form.reset();
  await refreshActiveConversation();
  toast("Reply added.");
}

async function saveContactNote(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = new FormData(form).get("body");
  if (!state.selectedContactId || !body) return;
  await postJson(`/crm/api/contacts/${state.selectedContactId}/notes`, { body });
  form.reset();
  await refreshActiveConversation();
  toast("Note added.");
}

async function refreshActiveConversation() {
  const [conversations, contacts, quotes, calls] = await Promise.all([
    getJson("/crm/api/conversations"),
    getJson("/crm/api/contacts"),
    getJson("/crm/api/quotes"),
    getJson("/crm/api/calls")
  ]);
  state.conversations = conversations;
  state.contacts = contacts;
  state.quotes = quotes;
  state.calls = calls;
  if (state.selectedConversationId) {
    state.conversation = await getJson(`/crm/api/conversations/${state.selectedConversationId}`);
  }
  if (state.selectedContactId) {
    state.contactDetail = await getJson(`/crm/api/contacts/${state.selectedContactId}`);
  }
  render();
}

async function handleAction(action) {
  if (action === "reply" || action === "text") {
    $("#message-form input[name='body']").focus();
    return;
  }
  if (action === "call") {
    toast("Call action ready.");
    return;
  }
  if (action === "email") {
    const email = state.contactDetail?.contact?.email;
    if (email) window.location.href = `mailto:${email}`;
    else toast("No email on this contact.");
    return;
  }
  if (action === "quote") {
    await startQuoteFromSelectedContact();
  }
}

async function startQuoteFromSelectedContact() {
  if (!state.selectedContactId) return;
  const result = await postJson(`/api/contacts/${state.selectedContactId}/start-quote`, {});
  toast("Proposal handoff ready.");
  window.location.href = result.quote_url;
}

function rows(items, render, wrap = true) {
  if (!items.length) return "<p class=\"empty-state\">No records yet.</p>";
  return items.map((item) => wrap ? `<article class="row">${render(item)}</article>` : render(item)).join("");
}

function priorityBadge(item) {
  if (!item?.priority || item.priority === "new_contact") return "";
  return ` <span class="priority-badge">${esc(item.priority_score || "")}</span>`;
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
