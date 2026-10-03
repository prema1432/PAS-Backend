# PAS Backend

FastAPI + async Motor (MongoDB) customer authentication service.

## Setup

```bash
# Install uv if you don't have it
pip install uv

# Sync dependencies into a virtual env
uv sync

# Copy and edit the env file
cp .env .env.local   # edit MONGODB_URI and JWT_SECRET

# Create MongoDB indexes (run once)
uv run python scripts/create_indexes.py

# Start the dev server
uv run uvicorn app.main:app --reload --port 8000
```

## API

### `POST /customer/login`

Single input: `phone_number` (+ optional `device_id`).

| Scenario | Behaviour |
|---|---|
| New phone number | Creates customer record, returns OTP + JWT immediately |
| Existing, active subscription | Returns fresh OTP; call `/verify-otp` to get JWT |
| Existing, expired subscription | HTTP 403 — "recharge" message |

**Request**
```json
{ "phone_number": "+919876543210", "device_id": "abc123" }
```

**Response (new customer)**
```json
{
  "is_new_customer": true,
  "message": "New account created. OTP sent ...",
  "otp": "382910",
  "jwt_token": "eyJ..."
}
```

**Response (existing, active)**
```json
{
  "is_new_customer": false,
  "message": "OTP sent. Verify with /customer/verify-otp ...",
  "otp": "748201",
  "jwt_token": null
}
```

---

### `POST /customer/verify-otp`

Validates the OTP for an existing customer and returns a JWT.

**Request**
```json
{ "phone_number": "+919876543210", "otp": "748201" }
```

**Response**
```json
{ "message": "Login successful.", "jwt_token": "eyJ..." }
```

---

### `GET /health`

Returns `{"status": "ok"}`.

---

## Customer document fields

| Field | Description |
|---|---|
| `phone_number` | Primary key, unique |
| `otp` / `otp_expires_at` | Current OTP + 5-min TTL |
| `source` | `self` (default) or `admin` |
| `payment_type` | `free` (default) or `paid` |
| `activation_date` | When account was activated |
| `created_by` / `updated_by` | Phone or admin id |
| `referral_code_used` | Code entered at signup |
| `referral_code_generated` | This customer's own referral code |
| `time_remaining_seconds` | Seconds of access remaining |
| `time_expiry` | Absolute expiry timestamp |
| `last_login` | Last successful login timestamp |
| `login_session_id` | UUID per session |
| `device_id` | Device identifier from client |

> **Production note:** The `otp` field is returned in the API response for development convenience. In production, deliver it via SMS and remove it from the response body.
