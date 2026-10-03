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
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh;display:flex}

/* ── Sidebar ── */
.sidebar{width:220px;min-height:100vh;background:#0d1117;border-right:1px solid #1e293b;display:flex;flex-direction:column;flex-shrink:0;position:fixed;top:0;left:0;bottom:0;z-index:20}
.sidebar-logo{display:flex;align-items:center;gap:0.75rem;padding:1.25rem 1rem;border-bottom:1px solid #1e293b}
.sidebar-logo .logo{width:34px;height:34px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:1rem;flex-shrink:0}
.sidebar-logo h1{font-size:0.95rem;font-weight:700;line-height:1.2}
.sidebar-logo p{font-size:0.68rem;color:#475569}
.sidebar-nav{flex:1;padding:0.75rem 0.5rem;display:flex;flex-direction:column;gap:2px}
.nav-section{font-size:0.65rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#334155;padding:0.5rem 0.5rem 0.25rem}
.nav-item{display:flex;align-items:center;gap:0.6rem;padding:0.55rem 0.75rem;border-radius:8px;cursor:pointer;font-size:0.82rem;color:#64748b;transition:.12s;border:none;background:none;width:100%;text-align:left}
.nav-item:hover{background:#1a1f2e;color:#e2e8f0}
.nav-item.active{background:#1e1b4b;color:#a5b4fc}
.nav-item .icon{font-size:1rem;width:18px;text-align:center;flex-shrink:0}
.sidebar-footer{padding:0.75rem;border-top:1px solid #1e293b;display:flex;flex-direction:column;gap:0.4rem}
.sidebar-footer a{display:flex;align-items:center;gap:0.5rem;padding:0.45rem 0.75rem;border-radius:6px;font-size:0.75rem;color:#475569;text-decoration:none;transition:.12s}
.sidebar-footer a:hover{color:#94a3b8;background:#1a1f2e}

/* ── Main ── */
.main{margin-left:220px;flex:1;display:flex;flex-direction:column;min-height:100vh}
.topbar{display:flex;align-items:center;gap:1rem;padding:0.9rem 1.75rem;border-bottom:1px solid #1e293b;background:#0f1117;position:sticky;top:0;z-index:10}
.topbar h2{font-size:1rem;font-weight:600}
.topbar p{font-size:0.75rem;color:#475569;margin-top:1px}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:0.5rem}
.dot{width:8px;height:8px;border-radius:50%;background:#22c55e;flex-shrink:0}
.dot-label{font-size:0.72rem;color:#22c55e}

.page{display:none;padding:1.5rem 1.75rem;flex:1}
.page.active{display:block}

/* ── Stats ── */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem;margin-bottom:1.5rem}
.stat{background:#1a1f2e;border:1px solid #1e293b;border-radius:10px;padding:1rem 1.25rem}
.stat-val{font-size:1.6rem;font-weight:700;color:#a5b4fc}
.stat-lbl{font-size:0.7rem;color:#64748b;margin-top:3px;text-transform:uppercase;letter-spacing:.5px}

/* ── Toolbar ── */
.toolbar{display:flex;align-items:center;gap:0.6rem;margin-bottom:1rem;flex-wrap:wrap}
.toolbar input{background:#1a1f2e;border:1px solid #1e293b;border-radius:8px;padding:0.45rem 0.75rem;color:#e2e8f0;font-size:0.8rem;width:220px}
.toolbar input:focus{outline:none;border-color:#6366f1}
.btn{display:inline-flex;align-items:center;gap:0.35rem;padding:0.45rem 0.85rem;border-radius:8px;font-size:0.78rem;cursor:pointer;border:none;font-weight:600;transition:.12s;white-space:nowrap}
.btn-ghost{background:transparent;color:#94a3b8;border:1px solid #1e293b}.btn-ghost:hover{border-color:#475569;color:#e2e8f0}
.btn-danger{background:#7f1d1d;color:#fca5a5;border:1px solid #991b1b}.btn-danger:hover{background:#991b1b}
.btn-sm{padding:2px 8px;font-size:0.7rem}

/* ── Table ── */
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #1e293b;background:#1a1f2e}
table{width:100%;border-collapse:collapse;font-size:0.78rem}
thead tr{background:#0f172a}
th{padding:0.6rem 0.9rem;text-align:left;font-weight:600;color:#64748b;font-size:0.68rem;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}
td{padding:0.6rem 0.9rem;border-top:1px solid #1e293b;vertical-align:middle;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:#0f172a}
.actions{display:flex;gap:0.35rem}

/* ── Badges ── */
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:0.66rem;font-weight:600}
.badge-free{background:#1e3a5f;color:#7dd3fc}
.badge-paid{background:#14532d;color:#86efac}
.badge-self{background:#1e293b;color:#94a3b8}
.badge-admin{background:#4c1d95;color:#c4b5fd}
.badge-mobile{background:#292524;color:#fbbf24}
.badge-pc{background:#1a1f2e;color:#60a5fa}
.badge-bot{background:#1a1f2e;color:#f87171}
.badge-new{background:#14532d;color:#86efac}

/* ── Pager ── */
.pager{display:flex;align-items:center;gap:0.5rem;margin-top:1rem;font-size:0.78rem;color:#475569}

/* ── Modals ── */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center}
.overlay.open{display:flex}
.modal{background:#1a1f2e;border:1px solid #334155;border-radius:12px;padding:1.5rem;width:min(580px,95vw);max-height:85vh;overflow-y:auto}
.modal-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem}
.modal-head h3{font-size:0.95rem;font-weight:700}
.modal-head button{background:none;border:none;color:#64748b;cursor:pointer;font-size:1.1rem;line-height:1}
.modal pre{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:1rem;font-size:0.72rem;color:#a5b4fc;overflow:auto;max-height:420px;white-space:pre-wrap;word-break:break-all}
.confirm-box{background:#1a1f2e;border:1px solid #991b1b;border-radius:12px;padding:1.5rem;width:360px;text-align:center}
.confirm-box h3{font-size:0.95rem;margin-bottom:0.5rem}
.confirm-box p{color:#94a3b8;font-size:0.8rem;margin-bottom:1.25rem;line-height:1.5}
.confirm-btns{display:flex;gap:0.75rem;justify-content:center}

/* ── Overview page ── */
.overview-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:1.5rem}
@media(max-width:700px){.overview-grid{grid-template-columns:1fr}}
.info-card{background:#1a1f2e;border:1px solid #1e293b;border-radius:10px;padding:1rem 1.25rem}
.info-card h4{font-size:0.78rem;font-weight:600;color:#94a3b8;margin-bottom:0.75rem;text-transform:uppercase;letter-spacing:.5px}
.info-row{display:flex;justify-content:space-between;align-items:center;padding:0.35rem 0;border-bottom:1px solid #1e293b;font-size:0.78rem}
.info-row:last-child{border-bottom:none}
.info-row span:first-child{color:#64748b}
.info-row span:last-child{color:#e2e8f0;font-weight:500}

@media(max-width:640px){.sidebar{width:100%;min-height:auto;position:relative;flex-direction:row;flex-wrap:wrap}.main{margin-left:0}}
</style>
</head>
<body>

<!-- ── SIDEBAR ── -->
<aside class="sidebar">
  <div class="sidebar-logo">
    <div class="logo">P</div>
    <div><h1>PAS Admin</h1><p>Backend Dashboard</p></div>
  </div>

  <nav class="sidebar-nav">
    <div class="nav-section">Main</div>
    <button class="nav-item active" onclick="showPage('overview',this)">
      <span class="icon">🏠</span> Overview
    </button>
    <button class="nav-item" onclick="showPage('customers',this)">
      <span class="icon">👤</span> Customers
    </button>
    <button class="nav-item" onclick="showPage('events',this)">
      <span class="icon">📋</span> Login Events
    </button>
    <div class="nav-section" style="margin-top:0.5rem">API</div>
    <a class="nav-item" href="/docs" target="_blank">
      <span class="icon">📄</span> Swagger UI
    </a>
    <a class="nav-item" href="/redoc" target="_blank">
      <span class="icon">📘</span> ReDoc
    </a>
    <a class="nav-item" href="/openapi.json" target="_blank">
      <span class="icon">⚙️</span> OpenAPI JSON
    </a>
  </nav>

  <div class="sidebar-footer">
    <a href="/health">💚 Health Check</a>
    <a href="/">🏠 API Docs</a>
  </div>
</aside>

<!-- ── MAIN ── -->
<div class="main">

  <!-- Top bar -->
  <div class="topbar">
    <div>
      <h2 id="page-title">Overview</h2>
      <p id="page-sub">PAS Backend v0.1.0</p>
    </div>
    <div class="topbar-right">
      <span class="dot"></span>
      <span class="dot-label">Live</span>
    </div>
  </div>

  <!-- ── OVERVIEW PAGE ── -->
  <div id="page-overview" class="page active">
    <div class="stats" id="ov-stats">
      <div class="stat"><div class="stat-val" id="ov-customers">—</div><div class="stat-lbl">Customers</div></div>
      <div class="stat"><div class="stat-val" id="ov-events">—</div><div class="stat-lbl">Login Events</div></div>
      <div class="stat"><div class="stat-val" id="ov-free">—</div><div class="stat-lbl">Free Users</div></div>
      <div class="stat"><div class="stat-val" id="ov-paid">—</div><div class="stat-lbl">Paid Users</div></div>
    </div>
    <div class="overview-grid">
      <div class="info-card">
        <h4>Collections</h4>
        <div class="info-row"><span>customers</span><span id="ov-c2">—</span></div>
        <div class="info-row"><span>login_events</span><span id="ov-e2">—</span></div>
      </div>
      <div class="info-card">
        <h4>New Customer Defaults</h4>
        <div class="info-row"><span>Free time</span><span>30 minutes</span></div>
        <div class="info-row"><span>Trial expiry</span><span>10 days</span></div>
        <div class="info-row"><span>Default plan</span><span>Free</span></div>
        <div class="info-row"><span>JWT expiry</span><span>60 minutes</span></div>
      </div>
      <div class="info-card">
        <h4>Endpoints</h4>
        <div class="info-row"><span>POST /customer/login</span><span class="badge badge-free">auth</span></div>
        <div class="info-row"><span>GET /customer/me</span><span class="badge" style="background:#1e3a5f;color:#7dd3fc">🔒 JWT</span></div>
        <div class="info-row"><span>GET /health</span><span class="badge badge-self">open</span></div>
        <div class="info-row"><span>GET /admin/*</span><span class="badge badge-admin">admin</span></div>
      </div>
      <div class="info-card">
        <h4>Login Event Capture</h4>
        <div class="info-row"><span>IP Address</span><span>✅</span></div>
        <div class="info-row"><span>Geolocation</span><span>✅</span></div>
        <div class="info-row"><span>Browser / OS</span><span>✅</span></div>
        <div class="info-row"><span>Device type</span><span>✅</span></div>
      </div>
    </div>
  </div>

  <!-- ── CUSTOMERS PAGE ── -->
  <div id="page-customers" class="page">
    <div class="stats">
      <div class="stat"><div class="stat-val" id="c-total">—</div><div class="stat-lbl">Total</div></div>
      <div class="stat"><div class="stat-val" id="c-free">—</div><div class="stat-lbl">Free</div></div>
      <div class="stat"><div class="stat-val" id="c-paid">—</div><div class="stat-lbl">Paid</div></div>
    </div>
    <div class="toolbar">
      <input id="c-search" type="text" placeholder="Search phone…" oninput="filterCustomers()"/>
      <button class="btn btn-ghost" onclick="loadCustomers()">↻ Refresh</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Phone</th><th>Source</th><th>Plan</th><th>Referral Code</th>
          <th>Time Left (s)</th><th>Expiry</th><th>Last Login</th><th>Actions</th>
        </tr></thead>
        <tbody id="c-tbody"><tr><td colspan="8" style="padding:2rem;text-align:center;color:#475569">Loading…</td></tr></tbody>
      </table>
    </div>
    <div class="pager">
      <button class="btn btn-ghost btn-sm" id="c-prev" onclick="customerPage(-1)">← Prev</button>
      <span id="c-page-info"></span>
      <button class="btn btn-ghost btn-sm" id="c-next" onclick="customerPage(1)">Next →</button>
    </div>
  </div>

  <!-- ── EVENTS PAGE ── -->
  <div id="page-events" class="page">
    <div class="stats">
      <div class="stat"><div class="stat-val" id="e-total">—</div><div class="stat-lbl">Total Events</div></div>
      <div class="stat"><div class="stat-val" id="e-mobile">—</div><div class="stat-lbl">Mobile</div></div>
    </div>
    <div class="toolbar">
      <input id="e-search" type="text" placeholder="Filter by phone…" oninput="filterEvents()"/>
      <button class="btn btn-ghost" onclick="loadEvents()">↻ Refresh</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Phone</th><th>IP</th><th>Country</th><th>City</th>
          <th>Browser</th><th>OS</th><th>Device</th><th>Type</th><th>Time</th><th>Actions</th>
        </tr></thead>
        <tbody id="e-tbody"><tr><td colspan="10" style="padding:2rem;text-align:center;color:#475569">Loading…</td></tr></tbody>
      </table>
    </div>
    <div class="pager">
      <button class="btn btn-ghost btn-sm" id="e-prev" onclick="eventPage(-1)">← Prev</button>
      <span id="e-page-info"></span>
      <button class="btn btn-ghost btn-sm" id="e-next" onclick="eventPage(1)">Next →</button>
    </div>
  </div>

</div><!-- /main -->

<!-- Detail Modal -->
<div class="overlay" id="detail-overlay" onclick="closeDetail(event)">
  <div class="modal">
    <div class="modal-head">
      <h3 id="modal-title">Details</h3>
      <button onclick="closeDetailModal()">✕</button>
    </div>
    <pre id="modal-body"></pre>
  </div>
</div>

<!-- Confirm Delete -->
<div class="overlay" id="confirm-overlay">
  <div class="confirm-box">
    <h3>⚠️ Confirm Delete</h3>
    <p id="confirm-msg"></p>
    <div class="confirm-btns">
      <button class="btn btn-ghost" onclick="closeConfirm()">Cancel</button>
      <button class="btn btn-danger" onclick="confirmDelete()">Delete</button>
    </div>
  </div>
</div>

<script>
const PER = 20;
let cSkip=0, eSkip=0, cData=[], eData=[], cTotal=0, eTotal=0, delCb=null;
const titles = {overview:['Overview','PAS Backend v0.1.0'], customers:['Customers','Manage customer records'], events:['Login Events','Authentication history']};

function showPage(id, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  if(el) el.classList.add('active');
  document.getElementById('page-title').textContent = titles[id][0];
  document.getElementById('page-sub').textContent = titles[id][1];
  if(id==='customers' && !cData.length) loadCustomers();
  if(id==='events' && !eData.length) loadEvents();
}

// ── Overview ──
async function loadOverview() {
  const [cr, er] = await Promise.all([
    fetch('/admin/customers?limit=1').then(r=>r.json()),
    fetch('/admin/login-events?limit=1').then(r=>r.json()),
  ]);
  document.getElementById('ov-customers').textContent = cr.total;
  document.getElementById('ov-events').textContent = er.total;
  document.getElementById('ov-c2').textContent = cr.total + ' docs';
  document.getElementById('ov-e2').textContent = er.total + ' docs';
  // load full for free/paid
  const all = await fetch('/admin/customers?limit=200').then(r=>r.json());
  const free = all.data.filter(c=>c.payment_type==='free').length;
  const paid = all.data.filter(c=>c.payment_type==='paid').length;
  document.getElementById('ov-free').textContent = free;
  document.getElementById('ov-paid').textContent = paid;
}

// ── Customers ──
async function loadCustomers() {
  document.getElementById('c-tbody').innerHTML = '<tr><td colspan="8" style="padding:2rem;text-align:center;color:#475569">Loading…</td></tr>';
  const d = await fetch(`/admin/customers?skip=${cSkip}&limit=${PER}`).then(r=>r.json());
  cData=d.data; cTotal=d.total;
  renderCustomers(cData);
  document.getElementById('c-total').textContent=cTotal;
  document.getElementById('c-free').textContent=cData.filter(c=>c.payment_type==='free').length;
  document.getElementById('c-paid').textContent=cData.filter(c=>c.payment_type==='paid').length;
  document.getElementById('c-page-info').textContent=`${cSkip+1}–${Math.min(cSkip+PER,cTotal)} of ${cTotal}`;
  document.getElementById('c-prev').disabled=cSkip===0;
  document.getElementById('c-next').disabled=cSkip+PER>=cTotal;
}

function renderCustomers(rows) {
  const tb = document.getElementById('c-tbody');
  if(!rows.length){tb.innerHTML='<tr><td colspan="8" style="padding:2rem;text-align:center;color:#475569">No records</td></tr>';return;}
  tb.innerHTML = rows.map(c=>`<tr>
    <td title="${c.phone_number}">${c.phone_number}</td>
    <td><span class="badge badge-${c.source}">${c.source}</span></td>
    <td><span class="badge badge-${c.payment_type}">${c.payment_type}</span></td>
    <td>${c.referral_code_generated||'—'}</td>
    <td>${c.time_remaining_seconds??0}</td>
    <td>${c.time_expiry?c.time_expiry.replace('T',' ').split('.')[0]:'—'}</td>
    <td>${c.last_login?c.last_login.replace('T',' ').split('.')[0]:'—'}</td>
    <td><div class="actions">
      <button class="btn btn-ghost btn-sm" onclick='showDetail(${JSON.stringify(c)})'>View</button>
      <button class="btn btn-danger btn-sm" onclick="askDel('customer','${c.phone_number}')">Del</button>
    </div></td>
  </tr>`).join('');
}

function filterCustomers(){const q=document.getElementById('c-search').value.toLowerCase();renderCustomers(q?cData.filter(c=>c.phone_number.includes(q)):cData);}
function customerPage(d){cSkip=Math.max(0,cSkip+d*PER);loadCustomers();}

// ── Events ──
async function loadEvents(){
  document.getElementById('e-tbody').innerHTML='<tr><td colspan="10" style="padding:2rem;text-align:center;color:#475569">Loading…</td></tr>';
  const phone=document.getElementById('e-search').value;
  const q=phone?`&phone=${encodeURIComponent(phone)}`:'';
  const d=await fetch(`/admin/login-events?skip=${eSkip}&limit=${PER}${q}`).then(r=>r.json());
  eData=d.data;eTotal=d.total;
  renderEvents(eData);
  document.getElementById('e-total').textContent=eTotal;
  document.getElementById('e-mobile').textContent=eData.filter(e=>e.is_mobile).length;
  document.getElementById('e-page-info').textContent=`${eSkip+1}–${Math.min(eSkip+PER,eTotal)} of ${eTotal}`;
  document.getElementById('e-prev').disabled=eSkip===0;
  document.getElementById('e-next').disabled=eSkip+PER>=eTotal;
}

function renderEvents(rows){
  const tb=document.getElementById('e-tbody');
  if(!rows.length){tb.innerHTML='<tr><td colspan="10" style="padding:2rem;text-align:center;color:#475569">No records</td></tr>';return;}
  tb.innerHTML=rows.map(e=>{
    const geo=e.geolocation||{};
    const type=e.is_bot?'<span class="badge badge-bot">bot</span>':e.is_mobile?'<span class="badge badge-mobile">mobile</span>':'<span class="badge badge-pc">pc</span>';
    return `<tr>
      <td>${e.phone_number}</td>
      <td>${e.ip_address||'—'}</td>
      <td>${geo.country||'—'}</td>
      <td>${geo.city||'—'}</td>
      <td>${e.browser?.family||'—'} ${e.browser?.version||''}</td>
      <td>${e.os?.family||'—'}</td>
      <td>${(e.device?.brand||'')+' '+(e.device?.model||'')}</td>
      <td>${type}</td>
      <td>${e.timestamp?e.timestamp.replace('T',' ').split('.')[0]:'—'}</td>
      <td><div class="actions">
        <button class="btn btn-ghost btn-sm" onclick='showDetail(${JSON.stringify(e)})'>View</button>
        <button class="btn btn-danger btn-sm" onclick="askDel('event','${e._id}')">Del</button>
      </div></td>
    </tr>`;
  }).join('');
}

function filterEvents(){loadEvents();}
function eventPage(d){eSkip=Math.max(0,eSkip+d*PER);loadEvents();}

// ── Modals ──
function showDetail(obj){
  document.getElementById('modal-title').textContent=obj.phone_number||obj._id||'Details';
  document.getElementById('modal-body').textContent=JSON.stringify(obj,null,2);
  document.getElementById('detail-overlay').classList.add('open');
}
function closeDetailModal(){document.getElementById('detail-overlay').classList.remove('open');}
function closeDetail(e){if(e.target.id==='detail-overlay')closeDetailModal();}

function askDel(type,id){
  document.getElementById('confirm-msg').textContent=`Delete ${type} "${id}"? This cannot be undone.`;
  delCb=async()=>{
    const url=type==='customer'?`/admin/customers/${encodeURIComponent(id)}`:`/admin/login-events/${id}`;
    await fetch(url,{method:'DELETE'});
    closeConfirm();
    type==='customer'?loadCustomers():loadEvents();
  };
  document.getElementById('confirm-overlay').classList.add('open');
}
function confirmDelete(){if(delCb)delCb();}
function closeConfirm(){document.getElementById('confirm-overlay').classList.remove('open');delCb=null;}

loadOverview();
</script>
</body>
</html>"""
