"""
PAS Backend — FastAPI entry point.

Startup / shutdown lifecycle manages the MongoDB connection pool.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import close, connect
from app.routers import customer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await connect()
    yield
    # --- shutdown ---
    await close()


app = FastAPI(
    title="PAS Backend API",
    version="0.1.0",
    description="Personal AI Assistant — customer authentication service.",
    lifespan=lifespan,
)

app.include_router(customer.router)


"""
PAS Backend — FastAPI entry point.

Startup / shutdown lifecycle manages the MongoDB connection pool.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.database import close, connect
from app.routers import customer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await connect()
    yield
    # --- shutdown ---
    await close()


app = FastAPI(
    title="PAS Backend API",
    version="0.1.0",
    description="Personal AI Assistant — customer authentication service.",
    lifespan=lifespan,
)

app.include_router(customer.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PAS Backend API</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
      padding: 2rem;
    }

    header {
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 2.5rem;
      border-bottom: 1px solid #1e293b;
      padding-bottom: 1.5rem;
    }
    header .logo {
      width: 44px; height: 44px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 1.2rem; color: #fff;
    }
    header h1 { font-size: 1.5rem; font-weight: 700; }
    header p  { color: #64748b; font-size: 0.85rem; margin-top: 2px; }
    .badge {
      margin-left: auto;
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 999px;
      padding: 4px 12px;
      font-size: 0.75rem;
      color: #94a3b8;
    }
    .badge span { color: #6366f1; font-weight: 600; }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.5rem;
    }
    @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }

    .card {
      background: #1a1f2e;
      border: 1px solid #1e293b;
      border-radius: 12px;
      overflow: hidden;
    }
    .card-header {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid #1e293b;
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .card-header h2 { font-size: 0.95rem; font-weight: 600; }
    .card-header p  { font-size: 0.78rem; color: #64748b; margin-top: 2px; }

    .method {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: .5px;
      flex-shrink: 0;
    }
    .POST { background: #1d4ed8; color: #bfdbfe; }
    .GET  { background: #065f46; color: #a7f3d0; }

    .endpoint {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid #1e293b;
    }
    .endpoint:last-child { border-bottom: none; }

    .endpoint-path {
      font-family: 'Consolas', 'Fira Code', monospace;
      font-size: 0.85rem;
      color: #a5b4fc;
      margin-bottom: 0.4rem;
    }
    .endpoint-desc { font-size: 0.82rem; color: #94a3b8; margin-bottom: 0.75rem; }

    .step {
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      margin-bottom: 0.5rem;
    }
    .step:last-child { margin-bottom: 0; }
    .step-label {
      font-size: 0.72rem;
      font-weight: 600;
      color: #6366f1;
      text-transform: uppercase;
      letter-spacing: .5px;
      margin-bottom: 0.4rem;
    }
    .step p { font-size: 0.8rem; color: #94a3b8; line-height: 1.5; }

    pre {
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      font-family: 'Consolas', 'Fira Code', monospace;
      font-size: 0.78rem;
      color: #a5b4fc;
      overflow-x: auto;
      white-space: pre;
    }

    .fields { list-style: none; }
    .fields li {
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      padding: 0.6rem 1.25rem;
      border-bottom: 1px solid #1e293b;
      font-size: 0.82rem;
    }
    .fields li:last-child { border-bottom: none; }
    .field-name {
      font-family: 'Consolas', 'Fira Code', monospace;
      color: #f8fafc;
      min-width: 200px;
      flex-shrink: 0;
    }
    .field-type {
      color: #38bdf8;
      min-width: 80px;
      flex-shrink: 0;
      font-family: 'Consolas', 'Fira Code', monospace;
      font-size: 0.75rem;
    }
    .field-desc { color: #64748b; }
    .required { color: #f87171; font-size: 0.7rem; margin-left: 4px; }
    .optional { color: #64748b; font-size: 0.7rem; margin-left: 4px; }

    .links {
      display: flex;
      gap: 0.75rem;
      margin-top: 2rem;
      flex-wrap: wrap;
    }
    .links a {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      background: #1a1f2e;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 0.6rem 1rem;
      color: #a5b4fc;
      text-decoration: none;
      font-size: 0.82rem;
      transition: border-color 0.15s;
    }
    .links a:hover { border-color: #6366f1; color: #c7d2fe; }

    .section-title {
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #475569;
      padding: 0.75rem 1.25rem 0.25rem;
    }

    .tag {
      display: inline-block;
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 4px;
      padding: 1px 6px;
      font-size: 0.68rem;
      color: #94a3b8;
      margin-left: 6px;
    }
  </style>
</head>
<body>

<header>
  <div class="logo">P</div>
  <div>
    <h1>PAS Backend API</h1>
    <p>Personal AI Assistant — Customer Authentication Service</p>
  </div>
  <div class="badge">v<span>0.1.0</span></div>
</header>

<div class="grid">

  <!-- ENDPOINTS -->
  <div style="display:flex;flex-direction:column;gap:1.5rem;">

    <div class="card">
      <div class="card-header">
        <div>
          <h2>Endpoints</h2>
          <p>3 routes available</p>
        </div>
      </div>

      <!-- /customer/login -->
      <div class="endpoint">
        <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem;">
          <span class="method POST">POST</span>
          <span class="endpoint-path">/customer/login</span>
        </div>
        <div class="endpoint-desc">Two-step login — single endpoint for new and existing customers.</div>

        <div class="step">
          <div class="step-label">Step 1 — Request OTP</div>
          <p>Send <code>phone_number</code> only. New customers get a JWT immediately. Existing customers receive an OTP.</p>
        </div>
        <div class="step">
          <div class="step-label">Step 2 — Submit OTP → get JWT</div>
          <p>Send <code>phone_number</code> + <code>otp</code>. Returns <code>jwt_token</code> on success.</p>
        </div>

        <div class="section-title" style="padding-left:0;margin-top:0.75rem;">Request body</div>
        <pre>{
  "phone_number": "+919876543210",  // required
  "device_id":    "abc123",         // optional
  "otp":          "382910"          // optional (Step 2 only)
}</pre>
      </div>

      <!-- /customer/me -->
      <div class="endpoint">
        <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem;">
          <span class="method GET">GET</span>
          <span class="endpoint-path">/customer/me</span>
          <span class="tag">🔒 Bearer</span>
        </div>
        <div class="endpoint-desc">Returns the full profile of the authenticated customer. JWT required.</div>
        <pre>Authorization: Bearer &lt;jwt_token&gt;</pre>
      </div>

      <!-- /health -->
      <div class="endpoint">
        <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem;">
          <span class="method GET">GET</span>
          <span class="endpoint-path">/health</span>
        </div>
        <div class="endpoint-desc">Server health check. Returns <code>{"status":"ok"}</code>.</div>
      </div>
    </div>

    <!-- Login flow -->
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Login Flow</h2>
          <p>Status codes &amp; behaviour</p>
        </div>
      </div>
      <div class="endpoint">
        <div class="step">
          <div class="step-label" style="color:#4ade80;">200 — New customer</div>
          <p>Account created · 30 min free · 10-day expiry · OTP + JWT returned immediately</p>
        </div>
        <div class="step" style="margin-top:0.5rem;">
          <div class="step-label" style="color:#4ade80;">200 — Existing, Step 1</div>
          <p>OTP returned · <code>jwt_token</code> is null · submit OTP for JWT</p>
        </div>
        <div class="step" style="margin-top:0.5rem;">
          <div class="step-label" style="color:#4ade80;">200 — Existing, Step 2</div>
          <p>OTP validated · JWT returned · login complete</p>
        </div>
        <div class="step" style="margin-top:0.5rem;">
          <div class="step-label" style="color:#f87171;">403 — Expired</div>
          <p>Free/Paid limit expired · recharge required</p>
        </div>
        <div class="step" style="margin-top:0.5rem;">
          <div class="step-label" style="color:#f87171;">400 — Invalid OTP</div>
          <p>OTP does not match stored value</p>
        </div>
        <div class="step" style="margin-top:0.5rem;">
          <div class="step-label" style="color:#f87171;">401 — Unauthorized</div>
          <p>Missing or invalid Bearer token on protected routes</p>
        </div>
      </div>
    </div>

  </div>

  <!-- MODELS -->
  <div style="display:flex;flex-direction:column;gap:1.5rem;">

    <!-- Customer document -->
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Customer Model</h2>
          <p>MongoDB · <code>customers</code> collection</p>
        </div>
      </div>
      <ul class="fields">
        <li><span class="field-name">phone_number</span><span class="field-type">string</span><span class="field-desc">Primary key, unique<span class="required">*</span></span></li>
        <li><span class="field-name">otp</span><span class="field-type">string</span><span class="field-desc">Current 6-digit OTP (null after use)<span class="optional">opt</span></span></li>
        <li><span class="field-name">otp_expires_at</span><span class="field-type">datetime</span><span class="field-desc">OTP TTL timestamp<span class="optional">opt</span></span></li>
        <li><span class="field-name">source</span><span class="field-type">enum</span><span class="field-desc">self · admin</span></li>
        <li><span class="field-name">payment_type</span><span class="field-type">enum</span><span class="field-desc">free · paid</span></li>
        <li><span class="field-name">activation_date</span><span class="field-type">datetime</span><span class="field-desc">When account was created</span></li>
        <li><span class="field-name">created_by</span><span class="field-type">string</span><span class="field-desc">Phone number or admin ID</span></li>
        <li><span class="field-name">updated_by</span><span class="field-type">string</span><span class="field-desc">Last modifier</span></li>
        <li><span class="field-name">referral_code_used</span><span class="field-type">string</span><span class="field-desc">Code entered at signup<span class="optional">opt</span></span></li>
        <li><span class="field-name">referral_code_generated</span><span class="field-type">string</span><span class="field-desc">This customer's referral code</span></li>
        <li><span class="field-name">time_remaining_seconds</span><span class="field-type">int</span><span class="field-desc">Seconds of access left (1800 on signup)</span></li>
        <li><span class="field-name">time_expiry</span><span class="field-type">datetime</span><span class="field-desc">Hard expiry (activation + 10 days)</span></li>
        <li><span class="field-name">last_login</span><span class="field-type">datetime</span><span class="field-desc">Last successful login UTC</span></li>
        <li><span class="field-name">login_session_id</span><span class="field-type">uuid</span><span class="field-desc">Matches JWT sid claim</span></li>
        <li><span class="field-name">device_id</span><span class="field-type">string</span><span class="field-desc">Client device identifier<span class="optional">opt</span></span></li>
        <li><span class="field-name">created_at</span><span class="field-type">datetime</span><span class="field-desc">Record creation timestamp</span></li>
        <li><span class="field-name">updated_at</span><span class="field-type">datetime</span><span class="field-desc">Last update timestamp</span></li>
      </ul>
    </div>

    <!-- Login event -->
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Login Event Model</h2>
          <p>MongoDB · <code>login_events</code> collection</p>
        </div>
      </div>
      <ul class="fields">
        <li><span class="field-name">customer_id</span><span class="field-type">string</span><span class="field-desc">Ref to customers._id</span></li>
        <li><span class="field-name">phone_number</span><span class="field-type">string</span><span class="field-desc">Denormalised for fast lookup</span></li>
        <li><span class="field-name">session_id</span><span class="field-type">uuid</span><span class="field-desc">Matches JWT sid claim</span></li>
        <li><span class="field-name">timestamp</span><span class="field-type">datetime</span><span class="field-desc">UTC login time</span></li>
        <li><span class="field-name">ip_address</span><span class="field-type">string</span><span class="field-desc">X-Forwarded-For aware</span></li>
        <li><span class="field-name">geolocation</span><span class="field-type">object</span><span class="field-desc">country · region · city · lat/lon · ISP</span></li>
        <li><span class="field-name">user_agent_raw</span><span class="field-type">string</span><span class="field-desc">Full UA string</span></li>
        <li><span class="field-name">browser</span><span class="field-type">object</span><span class="field-desc">family · version</span></li>
        <li><span class="field-name">os</span><span class="field-type">object</span><span class="field-desc">family · version</span></li>
        <li><span class="field-name">device</span><span class="field-type">object</span><span class="field-desc">family · brand · model</span></li>
        <li><span class="field-name">is_mobile / is_tablet</span><span class="field-type">bool</span><span class="field-desc">Device type flags</span></li>
        <li><span class="field-name">is_pc / is_bot</span><span class="field-type">bool</span><span class="field-desc">Device type flags</span></li>
        <li><span class="field-name">accept_language</span><span class="field-type">string</span><span class="field-desc">Browser language header</span></li>
        <li><span class="field-name">referer / origin</span><span class="field-type">string</span><span class="field-desc">Request origin headers</span></li>
      </ul>
    </div>

  </div>
</div>

<div class="links">
  <a href="/docs">📄 Swagger UI</a>
  <a href="/redoc">📘 ReDoc</a>
  <a href="/openapi.json">⚙️ OpenAPI JSON</a>
  <a href="/health">💚 Health Check</a>
</div>

</body>
</html>""")

