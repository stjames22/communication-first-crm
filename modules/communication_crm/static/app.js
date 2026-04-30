const state = {
  conversations: [],
  selectedConversationId: null,
  selectedContactId: null,
  conversationDetail: null,
  contactDetail: null,
  search: "",
  loadingInbox: true,
  loadingConversation: false,
  sending: false,
  error: "",
  requestId: 0
};

const $ = (selector) => document.querySelector(selector);

document.addEventListener("click", handleClick);
document.addEventListener("keydown", handleKeydown);
$("#message-form").addEventListener("submit", sendReply);
$("#conversation-search").addEventListener("input", (event) => {
  state.search = event.target.value;
  renderInbox();
});

loadInbox();

async function loadInbox({ keepSelection = true } = {}) {
  state.loadingInbox = true;
  state.error = "";
  renderInbox();

  try {
    const conversations = await getJson("/crm/api/conversations");
    state.conversations = conversations.map(normalizeConversation);
    if (!keepSelection || !state.conversations.some((item) => item.id === state.selectedConversationId)) {
      state.selectedConversationId = filteredConversations()[0]?.id || state.conversations[0]?.id || null;
    }
    state.loadingInbox = false;
    renderInbox();
    if (state.selectedConversationId) {
      await selectConversation(state.selectedConversationId, { skipIfCurrentLoaded: true });
    } else {
      clearConversation();
      renderAll();
    }
  } catch (error) {
    state.loadingInbox = false;
    state.error = error.message || "Inbox could not load.";
    clearConversation();
    renderAll();
  }
}

async function selectConversation(conversationId, options = {}) {
  if (!conversationId) return;
  if (
    options.skipIfCurrentLoaded &&
    state.conversationDetail?.conversation?.id === conversationId &&
    state.contactDetail?.contact
  ) {
    renderAll();
    return;
  }

  const requestId = ++state.requestId;
  state.selectedConversationId = conversationId;
  state.selectedContactId = conversationById(conversationId)?.contact_id || null;
  state.loadingConversation = true;
  state.error = "";
  state.conversationDetail = null;
  state.contactDetail = null;
  renderAll();

  try {
    const detail = await getJson(`/crm/api/conversations/${encodeURIComponent(conversationId)}`);
    if (requestId !== state.requestId) return;

    const contactId = detail?.contact?.id || detail?.conversation?.contact_id || state.selectedContactId;
    const contactDetail = contactId ? await getJson(`/crm/api/contacts/${encodeURIComponent(contactId)}`) : null;
    if (requestId !== state.requestId) return;

    state.conversationDetail = detail;
    state.contactDetail = contactDetail;
    state.selectedContactId = contactId || null;
    state.conversations = state.conversations.map((item) =>
      item.id === conversationId
        ? {
            ...item,
            unread_count: 0,
            display_name: detail?.contact?.display_name || item.display_name,
            mobile_phone: detail?.contact?.mobile_phone || item.mobile_phone,
            email: detail?.contact?.email || item.email
          }
        : item
    );
  } catch (error) {
    if (requestId !== state.requestId) return;
    state.error = error.message || "Conversation could not load.";
  } finally {
    if (requestId === state.requestId) {
      state.loadingConversation = false;
      renderAll();
      focusComposer();
    }
  }
}

function renderAll() {
  renderInbox();
  renderConversation();
  renderContext();
}

function renderInbox() {
  const node = $("#conversations");
  if (state.loadingInbox) {
    node.innerHTML = loadingState("Loading inbox...");
    return;
  }
  if (state.error && !state.conversations.length) {
    node.innerHTML = `<article class="empty-state error">${esc(state.error)}</article>`;
    return;
  }
  if (!state.conversations.length) {
    node.innerHTML = `<article class="empty-state">No messages yet.</article>`;
    return;
  }

  const conversations = filteredConversations();
  if (!conversations.length) {
    node.innerHTML = `<article class="empty-state">No matching messages.</article>`;
    return;
  }

  node.innerHTML = conversations.map((item) => {
    const active = item.id === state.selectedConversationId;
    const unread = Number(item.unread_count || 0);
    return `
      <article class="conversation-card ${active ? "active" : ""}" data-conversation="${esc(item.id)}" tabindex="0" aria-selected="${active ? "true" : "false"}">
        <div class="conversation-topline">
          <strong>${esc(item.display_name || "Unknown customer")}</strong>
          <time>${esc(shortTime(item.last_message_at))}</time>
        </div>
        <p>${esc(item.last_message_body || "No message preview")}</p>
        <div class="conversation-footline">
          ${unread ? `<span class="count-badge">${unread}</span>` : ""}
          <span>${esc(channelLabel(item.channel_type))}</span>
        </div>
      </article>
    `;
  }).join("");
}

function renderConversation() {
  const contact = activeContact();
  const detail = state.conversationDetail;

  $("#workspace-title").textContent = contact?.display_name || "Conversation";
  $("#workspace-kicker").textContent = state.loadingConversation ? "Loading" : detail ? "Open conversation" : "Select a message";
  $("#workspace-meta").innerHTML = contact
    ? [contact.mobile_phone, contact.email].filter(Boolean).map((item) => `<span>${esc(item)}</span>`).join("")
    : "";

  if (state.loadingConversation) {
    $("#thread").innerHTML = loadingState("Opening conversation...");
    setComposerDisabled(true);
    return;
  }
  if (state.error && state.selectedConversationId) {
    $("#thread").innerHTML = `<article class="empty-state error">${esc(state.error)}</article>`;
    setComposerDisabled(true);
    return;
  }
  if (!detail) {
    $("#thread").innerHTML = `<article class="empty-state">Choose a message from the inbox.</article>`;
    setComposerDisabled(true);
    return;
  }

  const messages = Array.isArray(detail.messages) ? detail.messages : [];
  if (!messages.length) {
    $("#thread").innerHTML = `<article class="empty-state">No messages in this conversation.</article>`;
  } else {
    $("#thread").innerHTML = messages.map((message) => `
      <article class="message ${esc(message.direction)}">
        <p>${esc(message.body)}</p>
        <small>${esc(directionLabel(message.direction))} ${esc(fmt(message.created_at))}</small>
      </article>
    `).join("");
    $("#thread").scrollTop = $("#thread").scrollHeight;
  }
  setComposerDisabled(state.sending);
}

function renderContext() {
  const contact = activeContact();
  const detail = state.conversationDetail;
  const timeline = dedupeById(state.contactDetail?.timeline || detail?.timeline || []);
  const lastInbound = lastMessage("inbound");
  const lastOutbound = lastMessage("outbound");

  if (state.loadingConversation) {
    $("#summary").innerHTML = loadingState("Loading context...");
    return;
  }
  if (!detail || !contact) {
    $("#summary").innerHTML = `<article class="empty-state">Select a message to see the essentials.</article>`;
    return;
  }

  $("#summary").innerHTML = `
    <section class="summary-section contact-block">
      <strong>${esc(contact.display_name || "Unknown customer")}</strong>
      ${contact.mobile_phone ? `<p>${esc(contact.mobile_phone)}</p>` : ""}
      ${contact.email ? `<p>${esc(contact.email)}</p>` : ""}
    </section>

    <section class="summary-section signal-block">
      <h3>Signal</h3>
      <p>${esc(signalText(contact, detail, lastInbound, lastOutbound))}</p>
    </section>

    <section class="summary-section">
      <h3>Last message</h3>
      ${lastInbound ? `<p>${esc(lastInbound.body)}</p><small>${esc(fmt(lastInbound.created_at))}</small>` : `<p class="muted">No inbound message yet.</p>`}
    </section>

    <section class="summary-section">
      <h3>Last reply</h3>
      ${lastOutbound ? `<p>${esc(lastOutbound.body)}</p><small>${esc(fmt(lastOutbound.created_at))}</small>` : `<p class="muted">No reply yet.</p>`}
    </section>

    <section class="summary-section">
      <h3>Recent log</h3>
      ${timeline.slice(0, 3).map((item) => `
        <article class="activity-item">
          <strong>${esc(item.title || activityType(item.activity_type))}</strong>
          ${item.body ? `<p>${esc(trimWords(item.body, 18))}</p>` : ""}
          <small>${esc(fmt(item.created_at))}</small>
        </article>
      `).join("") || `<p class="muted">No activity logged.</p>`}
    </section>
  `;
}

async function sendReply(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = form.elements.body;
  const body = String(input.value || "").trim();
  const conversationId = state.selectedConversationId;
  if (!conversationId || !body || state.sending) return;

  state.sending = true;
  setComposerDisabled(true);
  try {
    await postJson(`/crm/api/conversations/${encodeURIComponent(conversationId)}/messages`, { body });
    input.value = "";
    await refreshConversation(conversationId);
    toast("Reply sent and logged.");
  } catch (error) {
    toast(error.message || "Reply could not be sent.");
  } finally {
    state.sending = false;
    setComposerDisabled(!state.conversationDetail);
    focusComposer();
  }
}

async function draftReply() {
  const contact = activeContact();
  if (!contact?.id) {
    toast("Select a customer first.");
    return;
  }
  const button = $("#draft-reply");
  button.disabled = true;
  try {
    const result = await postJson(`/crm/api/contacts/${encodeURIComponent(contact.id)}/draft-reply`, {});
    const draft = result?.activity?.body || "";
    if (draft) {
      $("#message-form").elements.body.value = draft;
      focusComposer();
      toast("Draft added. Review before sending.");
    } else {
      toast("No draft available.");
    }
  } catch (error) {
    toast(error.message || "Draft could not be created.");
  } finally {
    button.disabled = !state.conversationDetail;
  }
}

async function refreshConversation(conversationId) {
  const requestId = ++state.requestId;
  const [detail, conversations] = await Promise.all([
    getJson(`/crm/api/conversations/${encodeURIComponent(conversationId)}`),
    getJson("/crm/api/conversations")
  ]);
  if (requestId !== state.requestId) return;
  const contactId = detail?.contact?.id || detail?.conversation?.contact_id;
  const contactDetail = contactId ? await getJson(`/crm/api/contacts/${encodeURIComponent(contactId)}`) : null;
  if (requestId !== state.requestId) return;
  state.conversationDetail = detail;
  state.contactDetail = contactDetail;
  state.selectedContactId = contactId || null;
  state.conversations = conversations.map(normalizeConversation);
  renderAll();
}

async function handleClick(event) {
  const card = event.target.closest("[data-conversation]");
  if (card) {
    await selectConversation(card.dataset.conversation);
    return;
  }
  const action = event.target.closest("[data-action]");
  if (!action) return;
  if (action.dataset.action === "draft-reply") {
    await draftReply();
  }
}

function handleKeydown(event) {
  const card = event.target.closest("[data-conversation]");
  if (!card) return;
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    selectConversation(card.dataset.conversation);
  }
}

function filteredConversations() {
  const query = normalizeText(state.search);
  if (!query) return state.conversations;
  return state.conversations.filter((item) => [
    item.display_name,
    item.mobile_phone,
    item.email,
    item.last_message_body
  ].map(normalizeText).join(" ").includes(query));
}

function normalizeConversation(item) {
  return {
    ...item,
    display_name: item.display_name || item.contact?.display_name || "Unknown customer",
    mobile_phone: item.mobile_phone || item.contact?.mobile_phone || "",
    email: item.email || item.contact?.email || ""
  };
}

function conversationById(id) {
  return state.conversations.find((item) => item.id === id) || null;
}

function activeContact() {
  return state.contactDetail?.contact || state.conversationDetail?.contact || null;
}

function clearConversation() {
  state.selectedConversationId = null;
  state.selectedContactId = null;
  state.conversationDetail = null;
  state.contactDetail = null;
}

function setComposerDisabled(disabled) {
  $("#message-form").elements.body.disabled = disabled;
  $("#message-form").querySelector("button").disabled = disabled;
  $("#draft-reply").disabled = disabled || !activeContact();
}

function focusComposer() {
  if (!state.conversationDetail || state.sending) return;
  const input = $("#message-form").elements.body;
  input.focus();
}

function lastMessage(direction) {
  const messages = state.conversationDetail?.messages || [];
  return [...messages].reverse().find((message) => String(message.direction || "").toLowerCase() === direction) || null;
}

function signalText(contact, detail, lastInbound, lastOutbound) {
  const unread = Number(detail?.conversation?.unread_count || conversationById(state.selectedConversationId)?.unread_count || 0);
  if (unread) return `${unread} unread. Reply first.`;
  if (lastInbound && !lastOutbound) return "New conversation. Reply first.";
  if (lastInbound && lastOutbound && new Date(lastInbound.created_at) > new Date(lastOutbound.created_at)) {
    return "Customer is waiting on a reply.";
  }
  return "Conversation is current.";
}

function dedupeById(items) {
  const seen = new Set();
  return (items || []).filter((item) => {
    const key = item.id || `${item.activity_type}|${item.created_at}|${item.body}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function loadingState(message) {
  return `
    <article class="empty-state loading">
      <span class="loader"></span>
      <p>${esc(message)}</p>
    </article>
  `;
}

function channelLabel(value) {
  const normalized = String(value || "sms").toLowerCase();
  if (normalized.includes("email")) return "Email";
  if (normalized.includes("call") || normalized.includes("voice")) return "Call";
  return "SMS";
}

function directionLabel(value) {
  return String(value || "").toLowerCase() === "outbound" ? "Sent" : "Received";
}

function activityType(value) {
  return titleCase(String(value || "activity").replace(/[._-]/g, " "));
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

function trimWords(value, count) {
  const words = String(value || "").split(/\s+/).filter(Boolean);
  return words.length > count ? `${words.slice(0, count).join(" ")}...` : words.join(" ");
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

async function getJson(url) {
  const response = await fetch(url, { credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
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
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(toast.timeout);
  toast.timeout = setTimeout(() => node.classList.add("hidden"), 1800);
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
