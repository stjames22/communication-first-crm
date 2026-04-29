from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db

from . import lead_monitor_service

router = APIRouter()


@router.get("/lead-monitor", response_class=HTMLResponse)
def lead_monitor_page() -> HTMLResponse:
    return HTMLResponse(LEAD_MONITOR_HTML)


@router.post("/api/lead-monitor/analyze")
def analyze_lead(payload: dict = Body(...)):
    try:
        return lead_monitor_service.analyze_lead_signal(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lead-monitor/leads")
def create_lead(payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        return lead_monitor_service.create_lead_signal(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/lead-monitor/leads")
def list_leads(db: Session = Depends(get_db)):
    return lead_monitor_service.list_lead_signals(db)


@router.patch("/api/lead-monitor/leads/{lead_id}")
def update_lead(lead_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        lead = lead_monitor_service.update_lead_signal(db, lead_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not lead:
        raise HTTPException(status_code=404, detail="Lead signal not found")
    return lead


@router.post("/api/lead-monitor/leads/{lead_id}/attach-customer")
def attach_lead_to_customer(lead_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    contact_id = str(payload.get("contact_id") or "").strip()
    if not contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")
    result = lead_monitor_service.attach_lead_to_customer(db, lead_id, contact_id)
    if not result:
        raise HTTPException(status_code=404, detail="Lead signal or customer not found")
    return result


@router.get("/api/lead-monitor/customers")
def search_customers(q: str = Query(default=""), db: Session = Depends(get_db)):
    return lead_monitor_service.search_customers(db, q)


LEAD_MONITOR_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Lead Signal Monitor</title>
  <link rel="icon" href="data:," />
  <style>
    :root {
      --bg: #edf1f3;
      --panel: #ffffff;
      --panel-soft: #f7f9fa;
      --ink: #1d252b;
      --muted: #66727b;
      --line: #d7dee2;
      --line-strong: #b8c4ca;
      --brand: #235d7a;
      --brand-dark: #18445a;
      --danger: #9d2f3a;
    }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--ink); background: var(--bg); font-family: "Avenir Next", "Segoe UI", Arial, sans-serif; }
    button, input, select, textarea { border: 1px solid var(--line); border-radius: 8px; font: inherit; }
    button { min-height: 38px; padding: 9px 12px; color: var(--brand-dark); background: #f7fafb; cursor: pointer; font-weight: 700; }
    button.primary { color: #fff; border-color: var(--brand); background: var(--brand); }
    button.danger { color: var(--danger); background: #fff5f6; }
    input, select, textarea { width: 100%; min-width: 0; padding: 11px 12px; color: var(--ink); background: var(--panel); }
    textarea { min-height: 170px; resize: vertical; line-height: 1.45; }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 1.55rem; }
    h2 { font-size: 1.1rem; }
    h3 { color: var(--muted); font-size: 0.82rem; text-transform: uppercase; }
    .page { min-height: 100vh; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 22px; border-bottom: 1px solid var(--line); background: var(--panel); }
    .nav { display: flex; align-items: center; gap: 10px; }
    .nav a { color: var(--brand-dark); font-weight: 800; text-decoration: none; }
    .nav a.active { color: var(--ink); }
    .eyebrow { margin-bottom: 4px; color: var(--brand); font-size: 0.7rem; font-weight: 800; letter-spacing: 0; text-transform: uppercase; }
    .guardrail { max-width: 780px; color: #3f4b52; line-height: 1.45; }
    .layout { display: grid; grid-template-columns: minmax(360px, 540px) minmax(360px, 1fr); gap: 1px; background: var(--line); }
    .panel { min-height: calc(100vh - 86px); padding: 18px; background: var(--panel); }
    .panel.soft { background: var(--panel-soft); }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
    .field.full { grid-column: 1 / -1; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 0.82rem; font-weight: 700; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .card { display: grid; gap: 12px; margin-top: 16px; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
    .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
    .metric { padding: 10px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    .metric strong { display: block; margin-top: 4px; }
    .reply { padding: 12px; border-left: 3px solid var(--brand); background: #eef6f9; line-height: 1.45; }
    .lead-list { display: grid; gap: 10px; margin-top: 14px; }
    .lead-row { display: grid; grid-template-columns: 66px 1fr auto; gap: 12px; align-items: start; padding: 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
    .score { display: grid; place-items: center; width: 52px; height: 52px; border-radius: 8px; color: #fff; background: var(--brand); font-weight: 900; }
    .badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .badge { padding: 4px 8px; border-radius: 999px; color: var(--brand-dark); background: #e3eef3; font-size: 0.75rem; font-weight: 800; }
    .muted { color: var(--muted); }
    .attach { display: grid; grid-template-columns: 1fr auto; gap: 8px; margin-top: 8px; }
    .results { display: grid; gap: 8px; margin-top: 8px; }
    .customer-result { display: flex; justify-content: space-between; gap: 10px; padding: 9px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    .hidden { display: none; }
    .toast { position: fixed; right: 18px; bottom: 18px; max-width: 360px; padding: 12px 14px; border-radius: 8px; color: #fff; background: #1e2b32; }
    @media (max-width: 920px) { .layout { display: block; } .panel { min-height: auto; } .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  </style>
</head>
<body>
  <main class="page">
    <header class="topbar">
      <div>
        <p class="eyebrow">Communication First CRM</p>
        <h1>Lead Signal Monitor</h1>
        <p class="guardrail">This tool is for permission-based monitoring and manual lead triage. It does not scrape private Facebook groups or automatically post replies.</p>
      </div>
      <nav class="nav" aria-label="Primary">
        <a href="/crm/workspace">Inbox</a>
        <a class="active" href="/lead-monitor">Lead Monitor</a>
      </nav>
    </header>
    <section class="layout">
      <div class="panel">
        <h2>Manual lead capture</h2>
        <form id="analyze-form" class="form-grid">
          <label>Source type
            <select name="source_type">
              <option value="facebook">Facebook</option>
              <option value="facebook_group">Facebook Group</option>
              <option value="google">Google</option>
              <option value="reddit">Reddit</option>
              <option value="other">Other</option>
            </select>
          </label>
          <label>Area/location
            <input name="area_location" placeholder="Portland, Bend, Salem..." />
          </label>
          <label class="full">Source URL
            <input name="source_url" placeholder="Optional link to the copied source" />
          </label>
          <label class="full">Pasted text
            <textarea name="raw_text" required placeholder="Paste the post, comment, review, or local forum text here."></textarea>
          </label>
          <div class="actions full">
            <button class="primary" type="submit">Analyze Lead</button>
          </div>
        </form>
        <div id="result"></div>
      </div>
      <div class="panel soft">
        <h2>Lead inbox</h2>
        <div id="lead-list" class="lead-list"></div>
      </div>
    </section>
  </main>
  <div id="toast" class="toast hidden"></div>
  <script>
    const state = { analysis: null, form: null, leads: [], activeLeadId: null };
    const $ = (selector) => document.querySelector(selector);
    $("#analyze-form").addEventListener("submit", analyzeLead);
    document.addEventListener("click", handleClick);
    loadLeads();

    async function analyzeLead(event) {
      event.preventDefault();
      const form = Object.fromEntries(new FormData(event.currentTarget).entries());
      if (!String(form.raw_text || "").trim()) return toast("Pasted text is required.");
      const analysis = await postJson("/api/lead-monitor/analyze", form);
      state.analysis = analysis;
      state.form = form;
      renderResult();
    }

    function renderResult() {
      const item = state.analysis;
      if (!item) return;
      $("#result").innerHTML = `
        <section class="card">
          <div class="metrics">
            <div class="metric"><span class="muted">Score</span><strong>${esc(item.lead_score)}</strong></div>
            <div class="metric"><span class="muted">Type</span><strong>${esc(item.lead_type)}</strong></div>
            <div class="metric"><span class="muted">Urgency</span><strong>${esc(item.urgency)}</strong></div>
            <div class="metric"><span class="muted">Action</span><strong>${esc(item.recommended_action)}</strong></div>
          </div>
          <div><h3>Location</h3><p>${esc(item.location_detected || "Not detected")}</p></div>
          <div><h3>Summary</h3><p>${esc(item.intent_summary)}</p></div>
          <div><h3>Suggested reply - review before using.</h3><p class="reply">${esc(item.suggested_reply)}</p></div>
          <div class="badges">${item.matched_keywords.map((word) => `<span class="badge">${esc(word)}</span>`).join("")}</div>
          <div class="actions">
            <button class="primary" type="button" data-action="save-lead">Save as Lead</button>
            <button type="button" data-action="show-attach">Attach to Existing Customer</button>
            <button class="danger" type="button" data-action="dismiss-result">Dismiss</button>
          </div>
          <div id="attach-box" class="hidden">
            <div class="attach">
              <input id="customer-query" placeholder="Search name, phone, email, address" />
              <button type="button" data-action="search-customers">Search</button>
            </div>
            <div id="customer-results" class="results"></div>
          </div>
        </section>
      `;
    }

    async function loadLeads() {
      state.leads = await getJson("/api/lead-monitor/leads");
      renderLeads();
    }

    function renderLeads() {
      if (!state.leads.length) {
        $("#lead-list").innerHTML = `<article class="card muted">No saved leads yet.</article>`;
        return;
      }
      $("#lead-list").innerHTML = state.leads.map((lead) => `
        <article class="lead-row">
          <div class="score">${esc(lead.lead_score)}</div>
          <div>
            <strong>${esc(lead.intent_summary)}</strong>
            <div class="badges">
              <span class="badge">${esc(lead.source_type)}</span>
              <span class="badge">${esc(lead.lead_type)}</span>
              <span class="badge">${esc(lead.urgency)}</span>
              <span class="badge">${esc(lead.location_detected || "no location")}</span>
              <span class="badge">${esc(lead.status)}</span>
            </div>
          </div>
          <div class="actions">
            <button type="button" data-action="open-lead" data-lead="${esc(lead.id)}">Open</button>
            <button type="button" data-action="mark-contacted" data-lead="${esc(lead.id)}">Mark Contacted</button>
            <button class="danger" type="button" data-action="dismiss-lead" data-lead="${esc(lead.id)}">Dismiss</button>
          </div>
        </article>
      `).join("");
    }

    async function handleClick(event) {
      const button = event.target.closest("[data-action]");
      if (!button) return;
      const action = button.dataset.action;
      const leadId = button.dataset.lead;
      if (action === "save-lead") return saveLead();
      if (action === "show-attach") return $("#attach-box").classList.toggle("hidden");
      if (action === "dismiss-result") { $("#result").innerHTML = ""; state.analysis = null; return; }
      if (action === "search-customers") return searchCustomers();
      if (action === "attach-customer") return attachCustomer(button.dataset.contact);
      if (action === "mark-contacted") return patchLead(leadId, "contacted");
      if (action === "dismiss-lead") return patchLead(leadId, "dismissed");
      if (action === "open-lead") return openLead(leadId);
    }

    async function saveLead() {
      if (!state.analysis || !state.form) return;
      const saved = await postJson("/api/lead-monitor/leads", { ...state.form, analysis: state.analysis });
      state.activeLeadId = saved.id;
      toast("Lead saved.");
      await loadLeads();
      openLead(saved.id);
    }

    async function searchCustomers() {
      const q = $("#customer-query").value;
      const customers = await getJson(`/api/lead-monitor/customers?q=${encodeURIComponent(q)}`);
      $("#customer-results").innerHTML = customers.length ? customers.map((customer) => `
        <div class="customer-result">
          <span><strong>${esc(customer.display_name)}</strong><br><span class="muted">${esc(customer.mobile_phone || customer.email || "")}</span></span>
          <button type="button" data-action="attach-customer" data-contact="${esc(customer.id)}">Attach</button>
        </div>
      `).join("") : `<p class="muted">No matching customers found.</p>`;
    }

    async function attachCustomer(contactId) {
      let lead = state.leads.find((item) => item.id === state.activeLeadId);
      if (!lead && state.analysis) lead = await postJson("/api/lead-monitor/leads", { ...state.form, analysis: state.analysis });
      if (!lead) return toast("Save a lead before attaching.");
      await postJson(`/api/lead-monitor/leads/${lead.id}/attach-customer`, { contact_id: contactId });
      toast("Lead attached to customer activity.");
      await loadLeads();
    }

    async function patchLead(id, status) {
      await fetchJson(`/api/lead-monitor/leads/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      await loadLeads();
      toast(`Lead marked ${status}.`);
    }

    function openLead(id) {
      const lead = state.leads.find((item) => item.id === id);
      if (!lead) return;
      state.analysis = lead;
      state.form = {
        source_type: lead.source_type,
        source_url: lead.source_url || "",
        area_location: lead.area_location || "",
        raw_text: lead.raw_text
      };
      state.activeLeadId = lead.id;
      renderResult();
    }

    async function getJson(url) { return fetchJson(url); }
    async function postJson(url, payload) { return fetchJson(url, { method: "POST", body: JSON.stringify(payload) }); }
    async function fetchJson(url, options = {}) {
      const response = await fetch(url, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...options });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Lead monitor request failed");
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
      return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }
  </script>
</body>
</html>"""
