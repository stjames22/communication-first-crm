const state = {
  contacts: [],
  conversations: [],
  conversation: null,
  contactDetail: null,
  quotes: [],
  calls: [],
  selectedConversationId: null,
  selectedContactId: null,
  loading: true,
  error: null
};

const $ = (selector) => document.querySelector(selector);

document.addEventListener("click", handleClick);
$("#seed").addEventListener("click", seedDemo);
$("#message-form").addEventListener("submit", sendMessage);

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
    state.conversations = conversations;
    state.quotes = quotes;
    state.calls = calls;

    if (!conversations.some((item) => item.id === state.selectedConversationId)) {
      state.selectedConversationId = conversations[0]?.id || null;
    }

    if (state.selectedConversationId) {
      await selectConversation(state.selectedConversationId, { renderAfter: false });
    } else {
      state.conversation = null;
      state.contactDetail = null;
      state.selectedContactId = null;
    }
  } catch (error) {
    state.error = error.message || "Unable to load the communication inbox.";
  } finally {
    state.loading = false;
    render();
  }
}

function render() {
  renderInbox();
  renderThread();
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
  if (!state.conversations.length) {
    $("#conversations").innerHTML = `
      <article class="empty-state">
        <strong>No conversations yet.</strong>
        <p>Load demo data or wait for a call, text, or email to arrive.</p>
      </article>
    `;
    return;
  }

  $("#conversations").innerHTML = state.conversations.map((item) => {
    const active = item.id === state.selectedConversationId ? "active" : "";
    const unread = Number(item.unread_count || 0);
    const priority = priorityText(item);
    return `
      <article class="conversation-card ${active}" data-conversation="${esc(item.id)}" data-contact="${esc(item.contact_id || "")}">
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

function renderThread() {
  const detail = state.conversation;
  const contact = state.contactDetail?.contact || detail?.contact;

  $("#thread-title").textContent = contact?.display_name || "Conversation";
  $("#thread-kicker").textContent = detail
    ? channelLabel(detail.conversation?.channel_type)
    : state.loading
      ? "Loading"
      : "Select a conversation";
  $("#thread-meta").innerHTML = detail
    ? `<span>${esc(contactPoint(contact))}</span><span>${esc(shortTime(detail.conversation?.last_message_at))}</span>`
    : "";

  if (state.loading) {
    $("#thread").innerHTML = loadingRows("Loading thread...");
    setComposerDisabled(true);
    return;
  }
  if (state.error) {
    $("#thread").innerHTML = `<article class="empty-state error">The thread could not load. Try refreshing the page.</article>`;
    setComposerDisabled(true);
    return;
  }
  if (!detail) {
    $("#thread").innerHTML = `<article class="empty-state">Choose a conversation to see messages and reply.</article>`;
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
  const contact = state.contactDetail?.contact || detail?.contact;

  if (state.loading) {
    $("#summary").innerHTML = loadingRows("Loading contact context...");
    return;
  }
  if (state.error) {
    $("#summary").innerHTML = `<article class="empty-state error">Contact context is unavailable.</article>`;
    return;
  }
  if (!detail) {
    $("#summary").innerHTML = `<article class="empty-state">Select a conversation for contact details, recent activity, and the next best action.</article>`;
    return;
  }
  if (!contact) {
    $("#summary").innerHTML = `
      <article class="empty-state">
        <strong>Create contact from this conversation.</strong>
        <p>No existing phone or email match is attached yet.</p>
      </article>
    `;
    return;
  }

  const accountSummary = state.contactDetail?.account_summary;
  const latestQuote = contact.latest_quote || findLatestQuote(contact.id);
  const recentActivity = (state.contactDetail?.timeline || detail.timeline || []).slice(0, 4);
  const nextAction = nextBestAction(contact, latestQuote, recentActivity, accountSummary);

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
        <strong>${esc(titleCase(contact.status || "Unknown"))}</strong>
        <span>Quote</span>
        <strong>${esc(quoteStatus(latestQuote))}</strong>
      </div>
    </section>

    <section class="summary-section next-action">
      <h3>Next Best Action</h3>
      <p>${esc(nextAction)}</p>
    </section>

    <section class="summary-section">
      <h3>Recent Activity</h3>
      ${recentActivity.length ? recentActivity.map((item) => `
        <article class="activity-item">
          <strong>${esc(item.title)}${item.system_generated ? ` <span class="auto-badge">Auto</span>` : ""}</strong>
          <p>${esc(item.body || activityType(item.activity_type))}</p>
          <small>${esc(fmt(item.created_at))}</small>
        </article>
      `).join("") : `<p class="muted">No recent activity yet.</p>`}
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
    handleQuickAction(action.dataset.action);
  }
}

async function selectConversation(conversationId, options = {}) {
  if (!conversationId) return;
  state.selectedConversationId = conversationId;
  state.conversation = null;
  state.contactDetail = null;
  if (options.renderAfter !== false) render();

  try {
    state.conversation = await getJson(`/crm/api/conversations/${state.selectedConversationId}`);
    state.selectedContactId = options.contactId || state.conversation?.contact?.id || null;
    if (state.selectedContactId) {
      state.contactDetail = await getJson(`/crm/api/contacts/${state.selectedContactId}`);
    }
    state.conversations = state.conversations.map((item) =>
      item.id === conversationId ? { ...item, unread_count: 0 } : item
    );
    state.error = null;
  } catch (error) {
    state.error = error.message || "Unable to load that conversation.";
  }

  if (options.renderAfter !== false) render();
}

function handleQuickAction(action) {
  if (action === "send-text") {
    $("#message-form input[name='body']").focus();
    return;
  }
  const messages = {
    "log-call": "Call logging is ready for the next backend action.",
    "follow-up": "Follow-up creation is ready for the next backend action.",
    quote: "Quote creation can stay linked through the estimator handoff."
  };
  toast(messages[action] || "Action unavailable.");
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
  const body = new FormData(form).get("body");
  if (!state.selectedConversationId || !String(body || "").trim()) return;

  try {
    await postJson(`/crm/api/conversations/${state.selectedConversationId}/messages`, { body });
    form.reset();
    await refreshActiveConversation();
    toast("Reply added.");
  } catch (error) {
    toast(error.message || "Message could not be sent.");
  }
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
    await selectConversation(state.selectedConversationId, { renderAfter: false });
  }
  render();
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
  return "Keep the conversation moving with a clear next step.";
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
  const total = Number(quote.grand_total || 0).toFixed(2);
  return `${titleCase(quote.status)} - $${total}`;
}

function contactPoint(contact) {
  return contact?.mobile_phone || contact?.email || "No contact point";
}

function activityType(value) {
  return titleCase(String(value || "activity").replace(/[._-]/g, " "));
}

function site(value) {
  if (!value) return "No service site";
  return [value.address_line_1, value.city, value.state, value.zip].filter(Boolean).join(", ");
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
