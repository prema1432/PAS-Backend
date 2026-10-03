"""
Admin CRUD router — exposes read/delete operations on customers and login_events.
Served at /admin/* and used by the dashboard at /admin.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse

from app.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


def _serialize(doc: dict) -> dict:
    """Convert ObjectId and other non-JSON types to strings."""
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Customers CRUD
# ---------------------------------------------------------------------------

@router.get("/customers", summary="List all customers")
async def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    db = get_db()
    docs = await db["customers"].find().skip(skip).limit(limit).to_list(length=limit)
    total = await db["customers"].count_documents({})
    return {"total": total, "skip": skip, "limit": limit, "data": [_serialize(d) for d in docs]}


@router.get("/customers/{phone}", summary="Get customer by phone number")
async def get_customer(phone: str = Path(...)) -> dict:
    db = get_db()
    doc = await db["customers"].find_one({"phone_number": phone})
    if not doc:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return _serialize(doc)


@router.delete("/customers/{phone}", summary="Delete customer by phone number")
async def delete_customer(phone: str = Path(...)) -> dict:
    db = get_db()
    result = await db["customers"].delete_one({"phone_number": phone})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found.")
    # Also clean up their login events
    await db["login_events"].delete_many({"phone_number": phone})
    return {"message": f"Customer {phone} and their login events deleted."}


@router.patch("/customers/{phone}", summary="Update customer fields")
async def update_customer(phone: str = Path(...), body: dict[str, Any] = None) -> dict:
    if not body:
        raise HTTPException(status_code=400, detail="No fields provided.")
    # Prevent overwriting the primary key
    body.pop("phone_number", None)
    body.pop("_id", None)
    db = get_db()
    result = await db["customers"].update_one({"phone_number": phone}, {"$set": body})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found.")
    doc = await db["customers"].find_one({"phone_number": phone})
    return _serialize(doc)


# ---------------------------------------------------------------------------
# Login Events (read + delete only)
# ---------------------------------------------------------------------------

@router.get("/login-events", summary="List login events")
async def list_login_events(
    phone: str = Query(None, description="Filter by phone number"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    db = get_db()
    query = {"phone_number": phone} if phone else {}
    docs = await db["login_events"].find(query).sort("timestamp", -1).skip(skip).limit(limit).to_list(length=limit)
    total = await db["login_events"].count_documents(query)
    return {"total": total, "skip": skip, "limit": limit, "data": [_serialize(d) for d in docs]}


@router.delete("/login-events/{event_id}", summary="Delete a login event by ID")
async def delete_login_event(event_id: str = Path(...)) -> dict:
    db = get_db()
    result = await db["login_events"].delete_one({"_id": ObjectId(event_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Event not found.")
    return {"message": f"Login event {event_id} deleted."}


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(content=DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PAS Admin</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}
header{display:flex;align-items:center;gap:1rem;padding:1rem 2rem;border-bottom:1px solid #1e293b;background:#0f1117;position:sticky;top:0;z-index:10}
header .logo{width:36px;height:36px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:1rem}
header h1{font-size:1.1rem;font-weight:700}
header p{color:#64748b;font-size:0.78rem}
.tabs{display:flex;gap:0;padding:0 2rem;border-bottom:1px solid #1e293b;background:#0f1117}
.tab{padding:0.75rem 1.25rem;cursor:pointer;font-size:0.85rem;color:#64748b;border-bottom:2px solid transparent;transition:.15s}
.tab.active{color:#a5b4fc;border-bottom-color:#6366f1}
.tab:hover{color:#e2e8f0}
.content{padding:1.5rem 2rem}
.toolbar{display:flex;align-items:center;gap:0.75rem;margin-bottom:1rem;flex-wrap:wrap}
.toolbar input{background:#1a1f2e;border:1px solid #1e293b;border-radius:8px;padding:0.5rem 0.75rem;color:#e2e8f0;font-size:0.82rem;width:220px}
.toolbar input:focus{outline:none;border-color:#6366f1}
.btn{display:inline-flex;align-items:center;gap:0.4rem;padding:0.45rem 0.9rem;border-radius:8px;font-size:0.8rem;cursor:pointer;border:none;font-weight:600;transition:.15s}
.btn-primary{background:#6366f1;color:#fff}.btn-primary:hover{background:#4f46e5}
.btn-danger{background:#7f1d1d;color:#fca5a5;border:1px solid #991b1b}.btn-danger:hover{background:#991b1b}
.btn-ghost{background:transparent;color:#94a3b8;border:1px solid #1e293b}.btn-ghost:hover{border-color:#475569;color:#e2e8f0}
.stats{display:flex;gap:1rem;margin-bottom:1.25rem;flex-wrap:wrap}
.stat{background:#1a1f2e;border:1px solid #1e293b;border-radius:10px;padding:0.75rem 1.25rem}
.stat-val{font-size:1.4rem;font-weight:700;color:#a5b4fc}
.stat-lbl{font-size:0.72rem;color:#64748b;margin-top:2px}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #1e293b}
table{width:100%;border-collapse:collapse;font-size:0.8rem}
thead tr{background:#1a1f2e}
th{padding:0.6rem 0.85rem;text-align:left;font-weight:600;color:#94a3b8;font-size:0.72rem;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}
td{padding:0.6rem 0.85rem;border-top:1px solid #1e293b;vertical-align:top;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:#1a1f2e}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:0.68rem;font-weight:600}
.badge-free{background:#1e3a5f;color:#7dd3fc}
.badge-paid{background:#14532d;color:#86efac}
.badge-self{background:#1e293b;color:#94a3b8}
.badge-admin{background:#4c1d95;color:#c4b5fd}
.badge-mobile{background:#1a1f2e;color:#fbbf24}
.badge-pc{background:#1a1f2e;color:#60a5fa}
.badge-bot{background:#1a1f2e;color:#f87171}
.pager{display:flex;align-items:center;gap:0.5rem;margin-top:1rem;font-size:0.8rem;color:#64748b}
.modal-bg{display:none;position:fixed;inset:0;background:#000a;z-index:100;align-items:center;justify-content:center}
.modal-bg.open{display:flex}
.modal{background:#1a1f2e;border:1px solid #334155;border-radius:12px;padding:1.5rem;width:min(600px,95vw);max-height:85vh;overflow-y:auto}
.modal h3{font-size:1rem;font-weight:700;margin-bottom:1rem}
.modal pre{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:1rem;font-size:0.75rem;color:#a5b4fc;overflow:auto;max-height:400px;white-space:pre-wrap;word-break:break-all}
.modal-close{float:right;cursor:pointer;color:#64748b;font-size:1.2rem;line-height:1}
.loading{color:#64748b;padding:2rem;text-align:center}
.empty{color:#475569;padding:2rem;text-align:center}
.confirm-bg{display:none;position:fixed;inset:0;background:#000a;z-index:200;align-items:center;justify-content:center}
.confirm-bg.open{display:flex}
.confirm{background:#1a1f2e;border:1px solid #991b1b;border-radius:12px;padding:1.5rem;width:360px;text-align:center}
.confirm h3{font-size:1rem;margin-bottom:0.5rem}
.confirm p{color:#94a3b8;font-size:0.82rem;margin-bottom:1.25rem}
.confirm-btns{display:flex;gap:0.75rem;justify-content:center}
</style>
</head>
<body>

<header>
  <div class="logo">P</div>
  <div>
    <h1>PAS Admin</h1>
    <p>MongoDB CRUD Dashboard</p>
  </div>
  <div style="margin-left:auto;display:flex;gap:0.5rem">
    <a href="/docs" style="color:#a5b4fc;font-size:0.78rem;text-decoration:none;padding:0.4rem 0.75rem;border:1px solid #1e293b;border-radius:6px">Swagger UI</a>
    <a href="/" style="color:#a5b4fc;font-size:0.78rem;text-decoration:none;padding:0.4rem 0.75rem;border:1px solid #1e293b;border-radius:6px">API Docs</a>
  </div>
</header>

<div class="tabs">
  <div class="tab active" onclick="switchTab('customers')">👤 Customers</div>
  <div class="tab" onclick="switchTab('events')">📋 Login Events</div>
</div>

<!-- CUSTOMERS TAB -->
<div id="tab-customers" class="content">
  <div class="stats">
    <div class="stat"><div class="stat-val" id="c-total">—</div><div class="stat-lbl">Total Customers</div></div>
    <div class="stat"><div class="stat-val" id="c-free">—</div><div class="stat-lbl">Free</div></div>
    <div class="stat"><div class="stat-val" id="c-paid">—</div><div class="stat-lbl">Paid</div></div>
  </div>
  <div class="toolbar">
    <input id="c-search" type="text" placeholder="Search phone number…" oninput="filterCustomers()"/>
    <button class="btn btn-ghost" onclick="loadCustomers()">↻ Refresh</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Phone</th><th>Source</th><th>Plan</th><th>Referral Code</th>
          <th>Time Left (s)</th><th>Expiry</th><th>Last Login</th><th>Actions</th>
        </tr>
      </thead>
      <tbody id="c-tbody"><tr><td colspan="8" class="loading">Loading…</td></tr></tbody>
    </table>
  </div>
  <div class="pager">
    <button class="btn btn-ghost" id="c-prev" onclick="customerPage(-1)">← Prev</button>
    <span id="c-page-info"></span>
    <button class="btn btn-ghost" id="c-next" onclick="customerPage(1)">Next →</button>
  </div>
</div>

<!-- LOGIN EVENTS TAB -->
<div id="tab-events" class="content" style="display:none">
  <div class="stats">
    <div class="stat"><div class="stat-val" id="e-total">—</div><div class="stat-lbl">Total Events</div></div>
    <div class="stat"><div class="stat-val" id="e-mobile">—</div><div class="stat-lbl">Mobile Logins</div></div>
  </div>
  <div class="toolbar">
    <input id="e-search" type="text" placeholder="Filter by phone…" oninput="filterEvents()"/>
    <button class="btn btn-ghost" onclick="loadEvents()">↻ Refresh</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Phone</th><th>IP</th><th>Country</th><th>City</th>
          <th>Browser</th><th>OS</th><th>Device</th><th>Type</th><th>Timestamp</th><th>Actions</th>
        </tr>
      </thead>
      <tbody id="e-tbody"><tr><td colspan="10" class="loading">Loading…</td></tr></tbody>
    </table>
  </div>
  <div class="pager">
    <button class="btn btn-ghost" id="e-prev" onclick="eventPage(-1)">← Prev</button>
    <span id="e-page-info"></span>
    <button class="btn btn-ghost" id="e-next" onclick="eventPage(1)">Next →</button>
  </div>
</div>

<!-- Detail Modal -->
<div class="modal-bg" id="detail-modal" onclick="closeModal(event)">
  <div class="modal">
    <span class="modal-close" onclick="closeDetailModal()">✕</span>
    <h3 id="modal-title">Details</h3>
    <pre id="modal-body"></pre>
  </div>
</div>

<!-- Confirm Delete -->
<div class="confirm-bg" id="confirm-modal">
  <div class="confirm">
    <h3>⚠️ Delete Record</h3>
    <p id="confirm-msg">Are you sure?</p>
    <div class="confirm-btns">
      <button class="btn btn-ghost" onclick="closeConfirm()">Cancel</button>
      <button class="btn btn-danger" onclick="confirmDelete()">Delete</button>
    </div>
  </div>
</div>

<script>
const PER_PAGE = 20;
let cSkip = 0, eSkip = 0;
let cData = [], eData = [], cTotal = 0, eTotal = 0;
let deleteCallback = null;

// Tab switching
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', (tab==='customers'&&i===0)||(tab==='events'&&i===1)));
  document.getElementById('tab-customers').style.display = tab==='customers' ? '' : 'none';
  document.getElementById('tab-events').style.display = tab==='events' ? '' : 'none';
  if(tab==='events' && eData.length===0) loadEvents();
}

// ---- CUSTOMERS ----
async function loadCustomers() {
  document.getElementById('c-tbody').innerHTML = '<tr><td colspan="8" class="loading">Loading…</td></tr>';
  const r = await fetch(`/admin/customers?skip=${cSkip}&limit=${PER_PAGE}`);
  const d = await r.json();
  cData = d.data; cTotal = d.total;
  renderCustomers(cData);
  // stats
  document.getElementById('c-total').textContent = cTotal;
  document.getElementById('c-free').textContent = cData.filter(c=>c.payment_type==='free').length;
  document.getElementById('c-paid').textContent = cData.filter(c=>c.payment_type==='paid').length;
  document.getElementById('c-page-info').textContent = `${cSkip+1}–${Math.min(cSkip+PER_PAGE,cTotal)} of ${cTotal}`;
  document.getElementById('c-prev').disabled = cSkip === 0;
  document.getElementById('c-next').disabled = cSkip + PER_PAGE >= cTotal;
}

function renderCustomers(rows) {
  const tb = document.getElementById('c-tbody');
  if (!rows.length) { tb.innerHTML='<tr><td colspan="8" class="empty">No customers found.</td></tr>'; return; }
  tb.innerHTML = rows.map(c => `
    <tr>
      <td title="${c.phone_number}">${c.phone_number}</td>
      <td><span class="badge badge-${c.source}">${c.source}</span></td>
      <td><span class="badge badge-${c.payment_type}">${c.payment_type}</span></td>
      <td title="${c.referral_code_generated||''}">${c.referral_code_generated||'—'}</td>
      <td>${c.time_remaining_seconds ?? 0}</td>
      <td>${c.time_expiry ? c.time_expiry.replace('T',' ').split('.')[0] : '—'}</td>
      <td>${c.last_login ? c.last_login.replace('T',' ').split('.')[0] : '—'}</td>
      <td style="display:flex;gap:0.4rem">
        <button class="btn btn-ghost" style="padding:2px 8px;font-size:0.72rem" onclick='showDetail(${JSON.stringify(c)})'>View</button>
        <button class="btn btn-danger" style="padding:2px 8px;font-size:0.72rem" onclick="askDelete('customer','${c.phone_number}')">Del</button>
      </td>
    </tr>`).join('');
}

function filterCustomers() {
  const q = document.getElementById('c-search').value.toLowerCase();
  renderCustomers(q ? cData.filter(c => c.phone_number.includes(q)) : cData);
}

function customerPage(dir) {
  cSkip = Math.max(0, cSkip + dir * PER_PAGE);
  loadCustomers();
}

// ---- EVENTS ----
async function loadEvents() {
  document.getElementById('e-tbody').innerHTML = '<tr><td colspan="10" class="loading">Loading…</td></tr>';
  const phone = document.getElementById('e-search').value;
  const q = phone ? `&phone=${encodeURIComponent(phone)}` : '';
  const r = await fetch(`/admin/login-events?skip=${eSkip}&limit=${PER_PAGE}${q}`);
  const d = await r.json();
  eData = d.data; eTotal = d.total;
  renderEvents(eData);
  document.getElementById('e-total').textContent = eTotal;
  document.getElementById('e-mobile').textContent = eData.filter(e=>e.is_mobile).length;
  document.getElementById('e-page-info').textContent = `${eSkip+1}–${Math.min(eSkip+PER_PAGE,eTotal)} of ${eTotal}`;
  document.getElementById('e-prev').disabled = eSkip === 0;
  document.getElementById('e-next').disabled = eSkip + PER_PAGE >= eTotal;
}

function renderEvents(rows) {
  const tb = document.getElementById('e-tbody');
  if (!rows.length) { tb.innerHTML='<tr><td colspan="10" class="empty">No events found.</td></tr>'; return; }
  tb.innerHTML = rows.map(e => {
    const geo = e.geolocation || {};
    const devType = e.is_bot ? '<span class="badge badge-bot">bot</span>'
                  : e.is_mobile ? '<span class="badge badge-mobile">mobile</span>'
                  : '<span class="badge badge-pc">pc</span>';
    return `<tr>
      <td>${e.phone_number}</td>
      <td>${e.ip_address||'—'}</td>
      <td>${geo.country||'—'}</td>
      <td>${geo.city||'—'}</td>
      <td>${e.browser?.family||'—'} ${e.browser?.version||''}</td>
      <td>${e.os?.family||'—'}</td>
      <td>${e.device?.brand||''} ${e.device?.model||''}</td>
      <td>${devType}</td>
      <td>${e.timestamp ? e.timestamp.replace('T',' ').split('.')[0] : '—'}</td>
      <td style="display:flex;gap:0.4rem">
        <button class="btn btn-ghost" style="padding:2px 8px;font-size:0.72rem" onclick='showDetail(${JSON.stringify(e)})'>View</button>
        <button class="btn btn-danger" style="padding:2px 8px;font-size:0.72rem" onclick="askDelete('event','${e._id}')">Del</button>
      </td>
    </tr>`;
  }).join('');
}

function filterEvents() { loadEvents(); }
function eventPage(dir) {
  eSkip = Math.max(0, eSkip + dir * PER_PAGE);
  loadEvents();
}

// ---- DETAIL MODAL ----
function showDetail(obj) {
  document.getElementById('modal-title').textContent = obj.phone_number || obj._id || 'Details';
  document.getElementById('modal-body').textContent = JSON.stringify(obj, null, 2);
  document.getElementById('detail-modal').classList.add('open');
}
function closeDetailModal() { document.getElementById('detail-modal').classList.remove('open'); }
function closeModal(e) { if(e.target.id==='detail-modal') closeDetailModal(); }

// ---- DELETE ----
function askDelete(type, id) {
  document.getElementById('confirm-msg').textContent = `Delete ${type} "${id}"? This cannot be undone.`;
  deleteCallback = async () => {
    const url = type==='customer' ? `/admin/customers/${encodeURIComponent(id)}` : `/admin/login-events/${id}`;
    await fetch(url, {method:'DELETE'});
    closeConfirm();
    type==='customer' ? loadCustomers() : loadEvents();
  };
  document.getElementById('confirm-modal').classList.add('open');
}
function confirmDelete() { if(deleteCallback) deleteCallback(); }
function closeConfirm() { document.getElementById('confirm-modal').classList.remove('open'); deleteCallback=null; }

// Init
loadCustomers();
</script>
</body>
</html>"""
