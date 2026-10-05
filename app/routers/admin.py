"""
Admin CRUD router — exposes read/export operations on customers, login_events, and recharges.
Served at /admin/* and used by the dashboard at /admin.
"""

from __future__ import annotations

import io
import sys
import time
import json
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from app.auth import create_admin_jwt, decode_jwt, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models import (
    AdminCreateRequest,
    AdminDocument,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminRole,
    AdminUpdateRequest,
    CustomerCreateRequest,
    CustomerDocument,
    CustomerRechargeRequest,
    CustomerUpdateRequest,
    PaymentType,
    SourceType,
    normalize_indian_phone,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _phone_filter(phone: str) -> dict:
    """Matches a phone number flexibly with or without +91."""
    try:
        norm = normalize_indian_phone(phone)
        raw = norm.replace("+91", "")
        return {"$in": [norm, raw, phone]}
    except Exception:
        return {"$in": [phone, f"+91{phone}"]}


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
        elif isinstance(v, list):
            out[k] = [_serialize(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Customers CRUD & Filtering
# ---------------------------------------------------------------------------

@router.get("/customers", summary="List customers with filters & pagination")
async def list_customers(
    search: Optional[str] = Query(None, description="Search phone number or referral code"),
    payment_type: Optional[str] = Query(None, description="Filter by plan: free or paid"),
    source: Optional[str] = Query(None, description="Filter by source"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    db = get_db()
    query: dict[str, Any] = {}
    if search:
        search_clean = search.strip()
        query["$or"] = [
            {"phone_number": {"$regex": re_escape(search_clean), "$options": "i"}},
            {"referral_code_generated": {"$regex": re_escape(search_clean), "$options": "i"}},
        ]
    if payment_type:
        query["payment_type"] = payment_type
    if source:
        query["source"] = source

    docs = await db["customers"].find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    total = await db["customers"].count_documents(query)
    
    enriched = []
    for d in docs:
        item = _serialize(d)
        item["has_active_session"] = bool(d.get("login_session_id"))
        item["active_session_id"] = d.get("login_session_id")
        enriched.append(item)

    return {"total": total, "skip": skip, "limit": limit, "data": enriched}


@router.get("/customers/{phone}/usage-history", summary="Get customer minutes consumption and usage history")
async def get_customer_usage_history(phone: str = Path(...)) -> dict:
    db = get_db()
    cust = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found.")
    
    usage_docs = await db["usage_history"].find({"phone_number": _phone_filter(phone)}).sort("created_at", -1).to_list(length=200)
    event_docs = await db["login_events"].find({"phone_number": _phone_filter(phone)}).sort("timestamp", -1).to_list(length=100)
    
    total_consumed_seconds = sum(h.get("seconds_consumed", 0) for h in usage_docs)
    total_consumed_minutes = round(total_consumed_seconds / 60, 1)

    return {
        "phone_number": phone,
        "current_time_remaining_seconds": cust.get("time_remaining_seconds", 0),
        "current_time_remaining_minutes": round(cust.get("time_remaining_seconds", 0) / 60, 1),
        "has_active_session": bool(cust.get("login_session_id")),
        "active_session_id": cust.get("login_session_id"),
        "device_id": cust.get("device_id"),
        "total_consumed_minutes": total_consumed_minutes,
        "total_consumed_seconds": total_consumed_seconds,
        "total_sessions_count": len(event_docs),
        "usage_history": [_serialize(h) for h in usage_docs],
        "recent_login_events": [_serialize(e) for e in event_docs],
    }


@router.post("/customers/{phone}/force-logout", summary="Force logout all active sessions for a customer")
async def force_logout_customer(phone: str = Path(...)) -> dict:
    db = get_db()
    cust = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found.")

    old_sid = cust.get("login_session_id")
    now = datetime.now(tz=timezone.utc)
    
    await db["customers"].update_one(
        {"phone_number": _phone_filter(phone)},
        {"$set": {"login_session_id": None, "updated_at": now, "updated_by": "admin"}}
    )

    if old_sid:
        await db["usage_history"].insert_one({
            "phone_number": phone,
            "action": "admin_force_logout",
            "description": f"Admin manually force-logged out active session ({old_sid[:8]}…).",
            "minutes_consumed": 0,
            "seconds_consumed": 0,
            "previous_time_remaining_seconds": cust.get("time_remaining_seconds", 0),
            "new_time_remaining_seconds": cust.get("time_remaining_seconds", 0),
            "device_id": cust.get("device_id"),
            "session_id": old_sid,
            "created_at": now,
        })

    return {
        "message": f"Customer {phone} force-logged out successfully. Active session invalidated.",
        "phone_number": phone,
        "status": "terminated"
    }


@router.get("/customers/{phone}", summary="Get customer by phone number")
async def get_customer(phone: str = Path(...)) -> dict:
    db = get_db()
    doc = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    if not doc:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return _serialize(doc)


@router.post("/customers", summary="Add a new customer", status_code=status.HTTP_201_CREATED)
async def create_customer(body: CustomerCreateRequest) -> dict:
    db = get_db()
    existing = await db["customers"].find_one({"phone_number": body.phone_number})
    if existing:
        raise HTTPException(status_code=400, detail=f"Customer with phone {body.phone_number} already exists.")

    otp = body.otp or "".join(random.choices(string.digits, k=6))
    ref_suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    ref_code = body.referral_code_generated or f"PAS{body.phone_number[-4:]}{ref_suffix}"
    now = datetime.now(tz=timezone.utc)
    expiry = now + (timedelta(days=30) if body.payment_type == PaymentType.paid else timedelta(days=10))

    new_doc = CustomerDocument(
        phone_number=body.phone_number,
        otp=otp,
        source=body.source,
        payment_type=body.payment_type,
        activation_date=now if body.payment_type == PaymentType.paid else None,
        created_by="admin",
        updated_by="admin",
        referral_code_generated=ref_code,
        time_remaining_seconds=body.time_remaining_seconds,
        time_expiry=expiry,
        device_id=body.device_id,
        created_at=now,
        updated_at=now,
    )
    insert_res = await db["customers"].insert_one(new_doc.model_dump())
    saved = await db["customers"].find_one({"_id": insert_res.inserted_id})
    return {"message": "Customer created successfully.", "data": _serialize(saved)}


@router.patch("/customers/{phone}", summary="Edit customer")
@router.put("/customers/{phone}", summary="Edit customer")
async def edit_customer(phone: str = Path(...), body: CustomerUpdateRequest = ...) -> dict:
    db = get_db()
    existing = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    if not existing:
        raise HTTPException(status_code=404, detail="Customer not found.")

    update_fields: dict[str, Any] = {}
    now = datetime.now(tz=timezone.utc)

    if body.otp is not None:
        update_fields["otp"] = body.otp
    if body.source is not None:
        update_fields["source"] = body.source.value if hasattr(body.source, "value") else str(body.source)
    if body.payment_type is not None:
        new_plan = body.payment_type.value if hasattr(body.payment_type, "value") else str(body.payment_type)
        update_fields["payment_type"] = new_plan
        if new_plan == "paid" and not existing.get("activation_date"):
            update_fields["activation_date"] = now
    if body.time_remaining_seconds is not None:
        update_fields["time_remaining_seconds"] = int(body.time_remaining_seconds)
    if body.referral_code_generated is not None:
        update_fields["referral_code_generated"] = body.referral_code_generated
    if body.device_id is not None:
        update_fields["device_id"] = body.device_id
    if body.time_expiry is not None:
        update_fields["time_expiry"] = body.time_expiry

    update_fields["updated_at"] = now
    update_fields["updated_by"] = "admin"

    await db["customers"].update_one({"phone_number": _phone_filter(phone)}, {"$set": update_fields})
    updated = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    return {"message": f"Customer {phone} updated successfully.", "data": _serialize(updated)}


@router.post("/customers/{phone}/refresh-otp", summary="Regenerate OTP for a customer")
async def refresh_otp(phone: str = Path(...)) -> dict:
    otp = "".join(random.choices(string.digits, k=6))
    db = get_db()
    result = await db["customers"].update_one(
        {"phone_number": _phone_filter(phone)},
        {"$set": {"otp": otp, "otp_expires_at": None, "updated_at": datetime.now(tz=timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return {"phone_number": phone, "otp": otp}


@router.post("/customers/{phone}/recharge", summary="Recharge customer time and switch plan")
async def recharge_customer(
    phone: str = Path(...),
    body: CustomerRechargeRequest = ...,
) -> dict:
    """
    Recharges a customer:
    - Adjusts time remaining (+ increase or - minus seconds)
    - Switches payment plan (default free to paid)
    - Records transaction in 'recharges' collection linked to the customer
    - Sets source ('manual') and payment_status ('completed' / 'manual')
    """
    db = get_db()
    customer = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    prev_time = customer.get("time_remaining_seconds", 0)
    delta = body.time_delta_seconds
    new_time = max(0, prev_time + delta)

    prev_plan = customer.get("payment_type", "free")
    new_plan = body.payment_type.value if hasattr(body.payment_type, "value") else str(body.payment_type)

    now = datetime.now(tz=timezone.utc)

    # Insert into recharges collection with customer relationship
    recharge_doc = {
        "customer_id": str(customer.get("_id", "")),
        "phone_number": phone,
        "amount": float(body.amount),
        "time_delta_seconds": int(delta),
        "previous_time_remaining_seconds": prev_time,
        "new_time_remaining_seconds": new_time,
        "previous_payment_type": prev_plan,
        "new_payment_type": new_plan,
        "source": body.source or "manual",
        "payment_status": body.payment_status or "completed",
        "notes": body.notes or "",
        "created_by": body.created_by or "admin",
        "created_at": now,
    }

    insert_result = await db["recharges"].insert_one(recharge_doc)
    recharge_doc["_id"] = str(insert_result.inserted_id)

    # Update customer document
    update_set = {
        "time_remaining_seconds": new_time,
        "payment_type": new_plan,
        "updated_at": now,
        "updated_by": body.created_by or "admin",
    }

    if new_plan == "paid" and not customer.get("activation_date"):
        update_set["activation_date"] = now

    if delta > 0:
        curr_exp = customer.get("time_expiry")
        if curr_exp and curr_exp.tzinfo is None:
            curr_exp = curr_exp.replace(tzinfo=timezone.utc)
        if not curr_exp or curr_exp < now:
            update_set["time_expiry"] = now + timedelta(days=30)

    await db["customers"].update_one({"phone_number": _phone_filter(phone)}, {"$set": update_set})
    updated_customer = await db["customers"].find_one({"phone_number": _phone_filter(phone)})

    return {
        "message": f"Customer {phone} recharged successfully. Plan: {new_plan}, time left: {new_time}s.",
        "customer": _serialize(updated_customer),
        "recharge": _serialize(recharge_doc),
    }


@router.get("/customers/{phone}/recharges", summary="List recharges for a customer")
async def get_customer_recharges(phone: str = Path(...)) -> dict:
    db = get_db()
    docs = await db["recharges"].find({"phone_number": _phone_filter(phone)}).sort("created_at", -1).to_list(length=100)
    return {"total": len(docs), "data": [_serialize(d) for d in docs]}


@router.get("/customers/{phone}/login-events", summary="List login events for a customer")
async def get_customer_login_events(
    phone: str = Path(...),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    db = get_db()
    docs = await db["login_events"].find({"phone_number": _phone_filter(phone)}).sort("timestamp", -1).limit(limit).to_list(length=limit)
    total = await db["login_events"].count_documents({"phone_number": _phone_filter(phone)})
    return {"total": total, "phone_number": phone, "data": [_serialize(d) for d in docs]}


# ---------------------------------------------------------------------------
# Recharges (collection & relationship)
# ---------------------------------------------------------------------------

@router.get("/recharges", summary="List all recharges with filters & pagination")
async def list_recharges(
    search: Optional[str] = Query(None, description="Search phone number"),
    phone: Optional[str] = Query(None, description="Filter by exact phone number"),
    payment_status: Optional[str] = Query(None, description="Filter by payment status"),
    source: Optional[str] = Query(None, description="Filter by recharge source"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    db = get_db()
    query: dict[str, Any] = {}
    if phone:
        query["phone_number"] = phone
    elif search:
        query["phone_number"] = {"$regex": re_escape(search.strip()), "$options": "i"}
    if payment_status:
        query["payment_status"] = payment_status
    if source:
        query["source"] = source

    docs = await db["recharges"].find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    total = await db["recharges"].count_documents(query)
    return {"total": total, "skip": skip, "limit": limit, "data": [_serialize(d) for d in docs]}


# ---------------------------------------------------------------------------
# Login Events & Sessions
# ---------------------------------------------------------------------------

@router.get("/login-events", summary="List login events / customer sessions with filters")
async def list_login_events(
    search: Optional[str] = Query(None, description="Search phone number or IP address"),
    phone: Optional[str] = Query(None, description="Filter by phone number"),
    device_type: Optional[str] = Query(None, description="Filter: pc, mobile, tablet, bot"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    db = get_db()
    query: dict[str, Any] = {}
    if phone:
        query["phone_number"] = phone
    elif search:
        s = search.strip()
        query["$or"] = [
            {"phone_number": {"$regex": re_escape(s), "$options": "i"}},
            {"ip_address": {"$regex": re_escape(s), "$options": "i"}},
        ]
    if device_type:
        dt = device_type.lower()
        if dt == "pc":
            query["is_pc"] = True
        elif dt == "mobile":
            query["is_mobile"] = True
        elif dt == "tablet":
            query["is_tablet"] = True
        elif dt == "bot":
            query["is_bot"] = True

    docs = await db["login_events"].find(query).sort("timestamp", -1).skip(skip).limit(limit).to_list(length=limit)
    total = await db["login_events"].count_documents(query)
    return {"total": total, "skip": skip, "limit": limit, "data": [_serialize(d) for d in docs]}


# ---------------------------------------------------------------------------
# Sessions & Terminate Session
# ---------------------------------------------------------------------------

@router.get("/sessions", summary="List customer login sessions with active/terminated status")
async def list_sessions(
    search: Optional[str] = Query(None, description="Search customer phone or session ID"),
    status: Optional[str] = Query(None, description="Filter: all, active, terminated"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    db = get_db()
    query: dict[str, Any] = {}
    st = (status or "all").lower().strip()
    if st == "active":
        query["login_session_id"] = {"$ne": None, "$nin": ["", None]}
    elif st in ("terminated", "inactive", "logged_out"):
        query["$or"] = [
            {"login_session_id": None},
            {"login_session_id": ""},
            {"login_session_id": {"$exists": False}},
        ]

    if search:
        s = search.strip()
        s_filter = [
            {"phone_number": {"$regex": re_escape(s), "$options": "i"}},
            {"login_session_id": {"$regex": re_escape(s), "$options": "i"}},
        ]
        if "$or" in query:
            query = {"$and": [{"$or": query.pop("$or")}, {"$or": s_filter}]}
        else:
            query["$or"] = s_filter

    docs = await db["customers"].find(query).sort("last_login", -1).skip(skip).limit(limit).to_list(length=limit)
    total = await db["customers"].count_documents(query)
    
    enriched = []
    for d in docs:
        item = _serialize(d)
        is_active = bool(d.get("login_session_id"))
        item["session_status"] = "active" if is_active else "terminated"
        enriched.append(item)

    return {"total": total, "skip": skip, "limit": limit, "data": enriched}


@router.post("/sessions/{phone}/terminate", summary="Terminate / invalidate customer login session")
@router.post("/sessions/{phone}/logout", summary="Terminate / invalidate customer login session")
async def terminate_customer_session(phone: str = Path(...)) -> dict:
    """Terminates customer session and logs them out immediately."""
    db = get_db()
    cust = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found.")
    await db["customers"].update_one(
        {"phone_number": _phone_filter(phone)},
        {"$set": {"login_session_id": None, "updated_at": datetime.now(tz=timezone.utc), "updated_by": "admin"}},
    )
    return {"message": f"Login session terminated for customer {phone}. Active token invalidated.", "phone_number": phone, "session_status": "terminated"}


@router.post("/sessions/{phone}/activate", summary="Generate fresh login session for customer")
async def activate_customer_session(phone: str = Path(...)) -> dict:
    """Generates a fresh active login session for a customer."""
    db = get_db()
    cust = await db["customers"].find_one({"phone_number": _phone_filter(phone)})
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found.")
    new_sid = str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc)
    await db["customers"].update_one(
        {"phone_number": _phone_filter(phone)},
        {"$set": {"login_session_id": new_sid, "last_login": now, "updated_at": now, "updated_by": "admin"}},
    )
    return {"message": f"Login session generated for customer {phone}.", "phone_number": phone, "session_id": new_sid, "session_status": "active"}


# ---------------------------------------------------------------------------
# Data Exports (JSON or XLSX format for all pages)
# ---------------------------------------------------------------------------

def re_escape(pattern: str) -> str:
    import re
    return re.escape(pattern)


@router.get("/export", summary="Export data in JSON or XLSX format")
async def export_data(
    resource: str = Query(..., description="Resource: customers, recharges, or events"),
    format: str = Query("json", description="Export format: json or xlsx"),
    search: Optional[str] = Query(None),
    payment_type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    device_type: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    is_active: Optional[str] = Query(None),
) -> Response:
    import io
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    db = get_db()
    res = resource.lower().strip()
    fmt = format.lower().strip()
    if fmt not in ("json", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format. Supported formats: json, xlsx.")

    now_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

    if res == "customers":
        query: dict[str, Any] = {}
        if search:
            s = search.strip()
            query["$or"] = [
                {"phone_number": {"$regex": re_escape(s), "$options": "i"}},
                {"referral_code_generated": {"$regex": re_escape(s), "$options": "i"}},
            ]
        if payment_type:
            query["payment_type"] = payment_type
        if source:
            query["source"] = source

        docs = await db["customers"].find(query).sort("created_at", -1).to_list(length=10000)
        serialized = [_serialize(d) for d in docs]

        if fmt == "json":
            return Response(
                content=json.dumps(serialized, indent=2, default=str),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="customers_{now_str}.json"'},
            )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Customers"
        headers = [
            "Phone Number", "OTP", "Plan", "Source", "Remaining Seconds", "Remaining Time",
            "Referral Code", "Device ID", "Activation Date", "Time Expiry", "Last Login", "Session ID", "Created At"
        ]
        ws.append(headers)
        for c in serialized:
            sec = int(c.get("time_remaining_seconds", 0) or 0)
            mins = round(sec / 60, 1)
            rem_str = f"{sec}s ({mins}m)"
            ws.append([
                c.get("phone_number", ""),
                c.get("otp", ""),
                c.get("payment_type", ""),
                c.get("source", ""),
                sec,
                rem_str,
                c.get("referral_code_generated", ""),
                c.get("device_id", ""),
                c.get("activation_date", ""),
                c.get("time_expiry", ""),
                c.get("last_login", ""),
                c.get("login_session_id", ""),
                c.get("created_at", ""),
            ])

        # Style header row
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        return Response(
            content=bio.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="customers_{now_str}.xlsx"'},
        )

    elif res == "recharges":
        query = {}
        if search:
            query["phone_number"] = {"$regex": re_escape(search.strip()), "$options": "i"}
        if payment_status:
            query["payment_status"] = payment_status
        if source:
            query["source"] = source

        docs = await db["recharges"].find(query).sort("created_at", -1).to_list(length=10000)
        serialized = [_serialize(d) for d in docs]

        if fmt == "json":
            return Response(
                content=json.dumps(serialized, indent=2, default=str),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="recharges_{now_str}.json"'},
            )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Recharges"
        headers = [
            "ID", "Phone Number", "Customer ID", "Amount (INR)", "Time Delta Seconds",
            "Time Delta (Min)", "Prev Plan", "New Plan", "Prev Balance (s)", "New Balance (s)",
            "Source", "Payment Status", "Created At", "Notes", "Created By"
        ]
        ws.append(headers)
        for r in serialized:
            delta = int(r.get("time_delta_seconds", 0) or 0)
            ws.append([
                r.get("_id", ""),
                r.get("phone_number", ""),
                r.get("customer_id", ""),
                float(r.get("amount", 0) or 0),
                delta,
                round(delta / 60, 1),
                r.get("previous_payment_type", ""),
                r.get("new_payment_type", ""),
                r.get("previous_time_remaining_seconds", 0),
                r.get("new_time_remaining_seconds", 0),
                r.get("source", ""),
                r.get("payment_status", ""),
                r.get("created_at", ""),
                r.get("notes", ""),
                r.get("created_by", ""),
            ])

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="D97706", end_color="D97706", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        return Response(
            content=bio.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="recharges_{now_str}.xlsx"'},
        )

    elif res == "events":
        query = {}
        if search:
            s = search.strip()
            query["$or"] = [
                {"phone_number": {"$regex": re_escape(s), "$options": "i"}},
                {"ip_address": {"$regex": re_escape(s), "$options": "i"}},
            ]
        if device_type:
            dt = device_type.lower()
            if dt == "pc":
                query["is_pc"] = True
            elif dt == "mobile":
                query["is_mobile"] = True
            elif dt == "tablet":
                query["is_tablet"] = True
            elif dt == "bot":
                query["is_bot"] = True

        docs = await db["login_events"].find(query).sort("timestamp", -1).to_list(length=10000)
        serialized = [_serialize(d) for d in docs]

        if fmt == "json":
            return Response(
                content=json.dumps(serialized, indent=2, default=str),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="login_events_{now_str}.json"'},
            )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Login Events"
        headers = [
            "ID", "Phone Number", "Session ID", "IP Address", "City", "Country",
            "Browser", "OS", "Device", "Device Type", "Timestamp"
        ]
        ws.append(headers)
        for e in serialized:
            geo = e.get("geolocation") or {}
            loc_city = geo.get("city", "")
            loc_country = geo.get("country", "")
            browser = f"{e.get('browser', {}).get('family', '')} {e.get('browser', {}).get('version', '')}".strip()
            os_name = f"{e.get('os', {}).get('family', '')} {e.get('os', {}).get('version', '')}".strip()
            dev = f"{e.get('device', {}).get('brand', '')} {e.get('device', {}).get('model', '')}".strip()
            dtype = "bot" if e.get("is_bot") else ("mobile" if e.get("is_mobile") else ("tablet" if e.get("is_tablet") else "pc"))
            ws.append([
                e.get("_id", ""),
                e.get("phone_number", ""),
                e.get("session_id", ""),
                e.get("ip_address", ""),
                loc_city,
                loc_country,
                browser,
                os_name,
                dev,
                dtype,
                e.get("timestamp", ""),
            ])

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="0284C7", end_color="0284C7", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        return Response(
            content=bio.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="login_events_{now_str}.xlsx"'},
        )

    elif res == "admins":
        query = {}
        if search:
            s = search.strip()
            query["$or"] = [
                {"email": {"$regex": re_escape(s), "$options": "i"}},
                {"name": {"$regex": re_escape(s), "$options": "i"}},
            ]
        if role:
            query["role"] = role
        if is_active is not None:
            query["is_active"] = is_active.lower() == "true"

        docs = await db["admins"].find(query, {"password_hash": 0}).sort("created_at", -1).to_list(length=10000)
        serialized = [_serialize(d) for d in docs]

        if fmt == "json":
            return Response(
                content=json.dumps(serialized, indent=2, default=str),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="admins_{now_str}.json"'},
            )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Admins"
        headers = ["ID", "Name", "Email", "Role", "Status", "Last Login", "Created At", "Updated At"]
        ws.append(headers)
        for a in serialized:
            ws.append([
                a.get("_id", ""),
                a.get("name", ""),
                a.get("email", ""),
                a.get("role", ""),
                "Active" if a.get("is_active", True) else "Inactive",
                a.get("last_login", "") or "Never",
                a.get("created_at", ""),
                a.get("updated_at", ""),
            ])

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="0284C7", end_color="0284C7", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        return Response(
            content=bio.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="admins_{now_str}.xlsx"'},
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown resource '{resource}'. Allowed: customers, recharges, events, admins.")


# ---------------------------------------------------------------------------
# Store & Settings API Endpoints (Dynamic MongoDB-backed configuration)
# ---------------------------------------------------------------------------

DEFAULT_STORE_CONFIG = {
    "key": "global_settings",
    "backend_url": "http://127.0.0.1:8000",
    "default_plan": "free",
    "trial_time_seconds": 1800,
    "per_page": 20,
    "support_phone": "+919876543210",
    "currency": "INR",
    "session_timeout_minutes": 1440,
    "enable_auto_otp": True,
    "updated_at": datetime.now(tz=timezone.utc),
    "updated_by": "system",
}


@router.get("/store", summary="Get global store and system settings")
async def get_store_settings() -> dict:
    """Returns the dynamic store settings from MongoDB, creating defaults if not yet present."""
    db = get_db()
    doc = await db["system_settings"].find_one({"key": "global_settings"})
    if not doc:
        default_doc = dict(DEFAULT_STORE_CONFIG)
        default_doc["updated_at"] = datetime.now(tz=timezone.utc)
        insert_res = await db["system_settings"].insert_one(default_doc)
        doc = await db["system_settings"].find_one({"_id": insert_res.inserted_id})
    return {"message": "Store settings loaded successfully.", "data": _serialize(doc)}


@router.put("/store", summary="Update global store and system settings")
@router.patch("/store", summary="Update global store and system settings")
async def update_store_settings(payload: dict) -> dict:
    """Dynamically updates store and system configurations in MongoDB."""
    db = get_db()
    allowed_keys = {
        "backend_url", "default_plan", "trial_time_seconds", "per_page",
        "support_phone", "currency", "session_timeout_minutes", "enable_auto_otp"
    }
    update_data: dict[str, Any] = {}
    for k, v in payload.items():
        if k in allowed_keys and v is not None:
            if k in ("trial_time_seconds", "per_page", "session_timeout_minutes"):
                update_data[k] = int(v)
            elif k == "enable_auto_otp":
                update_data[k] = bool(v)
            else:
                update_data[k] = str(v)

    now = datetime.now(tz=timezone.utc)
    update_data["updated_at"] = now
    update_data["updated_by"] = "admin"

    await db["system_settings"].update_one(
        {"key": "global_settings"},
        {"$set": update_data},
        upsert=True
    )
    saved = await db["system_settings"].find_one({"key": "global_settings"})
    return {"message": "Store settings updated successfully.", "data": _serialize(saved)}


@router.post("/store/reset", summary="Reset store settings to system defaults")
async def reset_store_settings() -> dict:
    """Resets system settings back to default values."""
    db = get_db()
    default_doc = dict(DEFAULT_STORE_CONFIG)
    default_doc["updated_at"] = datetime.now(tz=timezone.utc)
    default_doc["updated_by"] = "admin"
    await db["system_settings"].update_one(
        {"key": "global_settings"},
        {"$set": default_doc},
        upsert=True
    )
    saved = await db["system_settings"].find_one({"key": "global_settings"})
    return {"message": "Store settings reset to defaults.", "data": _serialize(saved)}



# ---------------------------------------------------------------------------
# Admin User Management & Crypto-Authenticated Login Endpoints
# ---------------------------------------------------------------------------

async def _ensure_default_admin(db):
    """Seed default superadmin with crypto-hashed password if admins collection is empty."""
    count = await db["admins"].count_documents({})
    if count == 0:
        now = datetime.now(tz=timezone.utc)
        default_admin = {
            "email": "admin@pas.com",
            "name": "Super Administrator",
            "role": "superadmin",
            "password_hash": hash_password("admin123"),
            "is_active": True,
            "last_login": None,
            "created_at": now,
            "updated_at": now,
        }
        await db["admins"].insert_one(default_admin)
        try:
            await db["admins"].create_index("email", unique=True)
        except Exception:
            pass


@router.post("/login", summary="Admin login with email & password, returns JWT token")
@router.post("/auth/login", summary="Admin login with email & password, returns JWT token")
async def admin_login(payload: AdminLoginRequest) -> AdminLoginResponse:
    """Authenticates an admin using crypto-hashed password verification and returns a signed JWT."""
    db = get_db()
    await _ensure_default_admin(db)
    email = payload.email.lower().strip()
    admin = await db["admins"].find_one({"email": email})
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not verify_password(payload.password, admin.get("password_hash", "")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not admin.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account is inactive. Please contact a super administrator.",
        )
    now = datetime.now(tz=timezone.utc)
    await db["admins"].update_one(
        {"_id": admin["_id"]},
        {"$set": {"last_login": now, "updated_at": now}}
    )
    token = create_admin_jwt(
        email=admin["email"],
        role=admin.get("role", "admin"),
        name=admin.get("name", "Admin")
    )
    return AdminLoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.JWT_EXPIRE_MINUTES,
        admin={
            "email": admin["email"],
            "name": admin.get("name", "Admin"),
            "role": admin.get("role", "admin"),
            "is_active": admin.get("is_active", True),
            "last_login": now.isoformat(),
        }
    )


@router.post("/logout", summary="Admin logout session")
@router.post("/auth/logout", summary="Admin logout session")
async def admin_logout_api() -> dict:
    """Terminates admin session."""
    return {"message": "Admin session logged out successfully."}


@router.get("/me", summary="Get authenticated admin profile")
@router.get("/auth/me", summary="Get authenticated admin profile")
async def get_admin_me() -> dict:
    """Returns default or authenticated admin profile."""
    db = get_db()
    admin = await db["admins"].find_one({}, {"password_hash": 0})
    if not admin:
        await _ensure_default_admin(db)
        admin = await db["admins"].find_one({}, {"password_hash": 0})
    return {"admin": _serialize(admin) if admin else None}


@router.get("/admins", summary="List admin users with filters & pagination")
async def list_admins(
    search: Optional[str] = Query(None, description="Search name or email"),
    role: Optional[str] = Query(None, description="Filter by role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    """List admin accounts. Password hashes are strictly omitted for security."""
    db = get_db()
    await _ensure_default_admin(db)
    query: dict[str, Any] = {}
    if search:
        s = search.strip()
        query["$or"] = [
            {"email": {"$regex": re_escape(s), "$options": "i"}},
            {"name": {"$regex": re_escape(s), "$options": "i"}},
        ]
    if role:
        query["role"] = role
    if is_active is not None:
        query["is_active"] = is_active

    total = await db["admins"].count_documents(query)
    cursor = db["admins"].find(query, {"password_hash": 0}).sort("created_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": [_serialize(d) for d in docs]
    }


@router.post("/admins", summary="Create a new admin user with crypto-hashed password")
async def create_admin(payload: AdminCreateRequest) -> dict:
    """Creates a new admin. The plaintext password is securely hashed via bcrypt."""
    db = get_db()
    await _ensure_default_admin(db)
    email = payload.email.lower().strip()
    existing = await db["admins"].find_one({"email": email})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Admin with email '{email}' already exists.",
        )
    now = datetime.now(tz=timezone.utc)
    doc = {
        "email": email,
        "name": payload.name.strip(),
        "role": payload.role.value if hasattr(payload.role, "value") else str(payload.role),
        "password_hash": hash_password(payload.password),
        "is_active": payload.is_active,
        "last_login": None,
        "created_at": now,
        "updated_at": now,
    }
    insert_res = await db["admins"].insert_one(doc)
    created = await db["admins"].find_one({"_id": insert_res.inserted_id}, {"password_hash": 0})
    return {"message": "Admin user created successfully.", "data": _serialize(created)}


@router.put("/admins/{email}", summary="Update an admin user")
async def update_admin(email: str, payload: AdminUpdateRequest) -> dict:
    """Updates admin profile, role, status, or updates password to a newly hashed string."""
    db = get_db()
    norm_email = email.lower().strip()
    admin = await db["admins"].find_one({"email": norm_email})
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found.")

    update_fields: dict[str, Any] = {}
    if payload.name is not None:
        update_fields["name"] = payload.name.strip()
    if payload.role is not None:
        update_fields["role"] = payload.role.value if hasattr(payload.role, "value") else str(payload.role)
    if payload.is_active is not None:
        update_fields["is_active"] = payload.is_active
    if payload.password and payload.password.strip():
        update_fields["password_hash"] = hash_password(payload.password.strip())

    update_fields["updated_at"] = datetime.now(tz=timezone.utc)
    await db["admins"].update_one({"email": norm_email}, {"$set": update_fields})
    updated = await db["admins"].find_one({"email": norm_email}, {"password_hash": 0})
    return {"message": "Admin updated successfully.", "data": _serialize(updated)}


# ---------------------------------------------------------------------------
# Dashboard HTML route
# ---------------------------------------------------------------------------

@router.get("/health-status", tags=["admin"], include_in_schema=False)
async def admin_health_status() -> dict:
    """Detailed health check for admin dashboard sidebar."""
    import sys
    import time
    from app.database import get_db
    
    start_time = time.time()
    db = get_db()
    db_status = "connected"
    db_ping_ms = 0.0
    collections = {}
    try:
        t0 = time.time()
        await db.command("ping")
        db_ping_ms = round((time.time() - t0) * 1000, 1)
        collections = {
            "customers": await db["customers"].count_documents({}),
            "login_events": await db["login_events"].count_documents({}),
            "recharges": await db["recharges"].count_documents({}),
            "admins": await db["admins"].count_documents({}),
        }
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    total_ping_ms = round((time.time() - start_time) * 1000, 1)
    
    return {
        "status": "online",
        "api": {
            "version": "0.1.0",
            "framework": "FastAPI",
            "python": sys.version.split()[0],
            "latency_ms": total_ping_ms,
        },
        "database": {
            "engine": "MongoDB (Motor)",
            "name": getattr(db, "name", "pas_db"),
            "status": db_status,
            "ping_ms": db_ping_ms,
            "collections": collections,
        },
    }


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(content=DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PAS Admin - Unified Router Hub</title>

<!-- Dynamic CDN CSS Framework: Tailwind CSS -->
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {
  theme: {
    extend: {
      colors: {
        dark: {
          950: '#f8fafc',
          900: '#ffffff',
          850: '#f1f5f9',
          800: '#e2e8f0',
          700: '#cbd5e1',
          600: '#94a3b8'
        },
        brand: {
          50: '#eef2ff',
          100: '#e0e7ff',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca'
        }
      }
    }
  }
}
</script>

<!-- HTMX CDN -->
<script src="https://unpkg.com/htmx.org@1.9.12"></script>

<!-- Lucide Icons CDN -->
<script src="https://unpkg.com/lucide@latest"></script>

<style>
/* Custom Scrollbars and Glass effects - Crisp Light Theme */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #f1f5f9; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

.glass-panel {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.04), 0 1px 2px -1px rgb(0 0 0 / 0.04);
}

.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
  z-index: 50;
  align-items: center;
  justify-content: center;
}
.modal-overlay.open { display: flex; }

.nav-link.active {
  background: #eef2ff !important;
  color: #4f46e5 !important;
  font-weight: 600;
}</style>
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen flex antialiased">

<!-- ── ADMIN AUTH GATEWAY (LOCK SCREEN ON LOGOUT) ── -->
<div id="admin-auth-gate" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-md p-4">
  <div class="bg-white border border-slate-200 shadow-2xl w-full max-w-md rounded-2xl p-8 relative text-center">
    <div class="w-14 h-14 mx-auto rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center font-black text-white text-2xl shadow-xl shadow-indigo-500/25 mb-4">
      P
    </div>
    <h2 class="text-xl font-bold text-slate-900 tracking-wide">PAS Admin Portal</h2>
    <p class="text-xs text-slate-500 mt-1 mb-6">Enter administrator credentials to unlock the management dashboard</p>

    <form onsubmit="performAdminGateLogin(event)" class="text-left space-y-4">
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Admin Email</label>
        <div class="relative">
          <i data-lucide="mail" class="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2"></i>
          <input type="email" id="gate-admin-email" value="admin@pas.com" required placeholder="admin@pas.com" class="w-full bg-slate-50 border border-slate-200 rounded-lg pl-9 pr-3.5 py-2.5 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white"/>
        </div>
      </div>

      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Password</label>
        <div class="relative">
          <i data-lucide="lock" class="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2"></i>
          <input type="password" id="gate-admin-password" value="admin123" required placeholder="••••••••" class="w-full bg-slate-50 border border-slate-200 rounded-lg pl-9 pr-3.5 py-2.5 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white"/>
        </div>
      </div>

      <div id="gate-login-error" class="hidden text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5 text-center"></div>

      <button type="submit" id="btn-gate-login" class="w-full py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold shadow-lg shadow-indigo-600/30 flex items-center justify-center gap-2 transition">
        <i data-lucide="log-in" class="w-4 h-4"></i>
        <span>Sign In to Dashboard</span>
      </button>

      <div class="pt-2 text-center text-[11px] text-slate-500">
        Default Superadmin: <code class="text-indigo-600 font-mono">admin@pas.com</code> / <code class="text-indigo-600 font-mono">admin123</code>
      </div>
    </form>
  </div>
</div>

<!-- ── SIDEBAR ── -->
<aside class="w-64 min-h-screen bg-white border-r border-slate-200 flex flex-col fixed top-0 bottom-0 left-0 z-20 shadow-sm">
  <!-- Brand -->
  <div class="flex items-center gap-3 px-5 py-5 border-b border-slate-200">
    <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center font-black text-white text-base shadow-md shadow-indigo-500/20">
      P
    </div>
    <div>
      <h1 class="text-sm font-bold text-slate-900 tracking-wide">PAS Admin</h1>
      <p class="text-[11px] text-slate-500 font-medium">Unified Router Hub</p>
    </div>
  </div>

  <!-- Navigation with Lucide Icons -->
  <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
    <div class="px-3 pb-1 text-[10px] font-bold text-slate-700 uppercase tracking-wider">Management</div>
    
    <button class="nav-link active w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition" onclick="showPage('overview', this)">
      <i data-lucide="layout-dashboard" class="w-4 h-4 text-indigo-400"></i>
      <span>Overview</span>
    </button>
    
    <button class="nav-link w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition" onclick="showPage('customers', this)">
      <i data-lucide="users" class="w-4 h-4 text-emerald-600"></i>
      <span>Customers</span>
    </button>
    
    <button class="nav-link w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition" onclick="showPage('recharges', this)">
      <i data-lucide="zap" class="w-4 h-4 text-amber-400"></i>
      <span>Recharges</span>
    </button>
    
    <button class="nav-link w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition" onclick="showPage('events', this)">
      <i data-lucide="activity" class="w-4 h-4 text-cyan-600"></i>
      <span>Login Events</span>
    </button>

    <button class="nav-link w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition" onclick="showPage('sessions', this)">
      <i data-lucide="shield-check" class="w-4 h-4 text-purple-400"></i>
      <span>Login Sessions</span>
    </button>

    <button class="nav-link w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition" onclick="showPage('admins', this)">
      <i data-lucide="shield" class="w-4 h-4 text-sky-600"></i>
      <span>Admins</span>
    </button>

    <button class="nav-link w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition" onclick="showPage('store', this)">
      <i data-lucide="shopping-bag" class="w-4 h-4 text-rose-400"></i>
      <span>Store & Config</span>
    </button>

    <div class="pt-4 px-3 pb-1 text-[10px] font-bold text-slate-700 uppercase tracking-wider">API & Docs</div>
    <a href="/docs" target="_blank" class="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition">
      <i data-lucide="file-code" class="w-4 h-4 text-indigo-400"></i>
      <span>Swagger UI</span>
      <i data-lucide="external-link" class="w-3 h-3 ml-auto text-slate-700"></i>
    </a>
    <a href="/redoc" target="_blank" class="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition">
      <i data-lucide="book-open" class="w-4 h-4 text-indigo-400"></i>
      <span>ReDoc</span>
      <i data-lucide="external-link" class="w-3 h-3 ml-auto text-slate-700"></i>
    </a>
  </nav>

  <!-- Sidebar Detailed API & DB Health Section -->
  <div class="p-3.5 border-t border-slate-200 bg-slate-50/80 flex flex-col gap-2">
    <div class="flex items-center justify-between px-0.5">
      <div class="flex items-center gap-1.5 text-[11px] font-bold text-slate-700 uppercase tracking-wider">
        <i data-lucide="activity" class="w-3.5 h-3.5 text-indigo-600"></i>
        <span>System & DB Health</span>
      </div>
      <button onclick="loadSystemHealth(true)" title="Refresh Health Diagnostics" class="p-1 rounded hover:bg-slate-200 text-slate-400 hover:text-slate-700 transition">
        <i data-lucide="refresh-cw" class="w-3 h-3" id="health-refresh-icon"></i>
      </button>
    </div>

    <!-- API Status Card -->
    <div class="bg-white rounded-lg p-2.5 border border-slate-200 shadow-xs flex flex-col gap-1.5">
      <div class="flex items-center justify-between text-xs">
        <span class="flex items-center gap-1.5 font-semibold text-slate-700">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" id="health-api-dot"></span>
          API Service
        </span>
        <span class="font-bold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded text-[10px] border border-emerald-200" id="health-api-badge">Online</span>
      </div>
      <div class="flex items-center justify-between text-[10px] text-slate-500">
        <span>Latency: <strong class="text-slate-800 font-mono" id="health-api-latency">-- ms</strong></span>
        <span class="text-slate-500 font-mono" id="health-api-ver">FastAPI</span>
      </div>
    </div>

    <!-- MongoDB Database Status Card -->
    <div class="bg-white rounded-lg p-2.5 border border-slate-200 shadow-xs flex flex-col gap-1.5">
      <div class="flex items-center justify-between text-xs">
        <span class="flex items-center gap-1.5 font-semibold text-slate-700">
          <span class="w-2 h-2 rounded-full bg-emerald-500" id="health-db-dot"></span>
          MongoDB
        </span>
        <span class="font-bold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded text-[10px] border border-emerald-200" id="health-db-badge">Connected</span>
      </div>
      <div class="flex items-center justify-between text-[10px] text-slate-500">
        <span>Ping: <strong class="text-slate-800 font-mono" id="health-db-ping">-- ms</strong></span>
        <span>DB: <strong class="text-indigo-600 font-mono" id="health-db-name">pas_db</strong></span>
      </div>
    </div>
  </div>
</aside>

<!-- ── MAIN CONTENT ── -->
<div class="ml-64 flex-1 flex flex-col min-h-screen bg-slate-50">

  <!-- Top bar -->
  <header class="h-16 px-8 border-b border-slate-200 bg-white/80 backdrop-blur-md flex items-center justify-between sticky top-0 z-10">
    <div class="flex items-center gap-3">
      <div id="page-icon-wrapper" class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
        <i data-lucide="layout-dashboard" class="w-4 h-4" id="page-icon"></i>
      </div>
      <div>
        <h2 id="page-title" class="text-sm font-bold text-slate-900 tracking-wide">Overview</h2>
        <p id="page-sub" class="text-[11px] text-slate-500">PAS Authentication & Subscriptions Dashboard</p>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <!-- Universal Export button on top bar -->
      <!-- Admin Login Token Tester -->
      <button onclick="openAdminLoginModal()" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-sky-200 bg-sky-50 hover:bg-sky-100 text-sky-700 text-xs font-medium transition shadow-sm" title="Admin Login & JWT Token Generator">
        <i data-lucide="key" class="w-3.5 h-3.5 text-sky-600"></i>
        <span>Admin Login (JWT)</span>
      </button>

      <button onclick="promptExportCurrentPage()" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-indigo-200 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-medium transition shadow-sm" title="Export Current Page Data">
        <i data-lucide="download" class="w-3.5 h-3.5 text-indigo-400"></i>
        <span>Export Page</span>
      </button>

      <button onclick="refreshCurrentPage()" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium transition shadow-sm">
        <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-slate-500"></i>
        <span>Refresh</span>
      </button>

      <!-- Admin Badge -->
      <div id="topbar-admin-badge" class="hidden sm:flex items-center gap-2 px-2.5 py-1 rounded-lg bg-slate-100 border border-slate-200 text-xs text-slate-700">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span id="topbar-admin-name" class="font-semibold text-slate-900">Admin</span>
        <span id="topbar-admin-role" class="text-[10px] px-1.5 py-0.2 rounded bg-indigo-100 text-indigo-700 border border-indigo-200 font-mono">superadmin</span>
      </div>

      <!-- Admin Logout button -->
      <button onclick="adminLogout()" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-red-200 bg-red-50 hover:bg-red-100 text-red-700 text-xs font-semibold transition shadow-sm" title="Sign out of Admin Session">
        <i data-lucide="log-out" class="w-3.5 h-3.5 text-rose-600"></i>
        <span>Logout</span>
      </button>
    </div>
  </header>

  <!-- ── OVERVIEW PAGE ── -->
  <main id="page-overview" class="page-view p-8 flex-1">
    <!-- Stat Grid -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
      <div class="glass-panel rounded-xl p-5 border border-slate-200 flex items-center justify-between">
        <div>
          <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Total Customers</p>
          <h3 class="text-2xl font-extrabold text-slate-900 mt-1" id="ov-customers">—</h3>
        </div>
        <div class="w-12 h-12 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
          <i data-lucide="users" class="w-6 h-6"></i>
        </div>
      </div>

      <div class="glass-panel rounded-xl p-5 border border-slate-200 flex items-center justify-between">
        <div>
          <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Login Attempts</p>
          <h3 class="text-2xl font-extrabold text-slate-900 mt-1" id="ov-events">—</h3>
        </div>
        <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-600">
          <i data-lucide="activity" class="w-6 h-6"></i>
        </div>
      </div>

      <div class="glass-panel rounded-xl p-5 border border-slate-200 flex items-center justify-between">
        <div>
          <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Total Recharges</p>
          <h3 class="text-2xl font-extrabold text-slate-900 mt-1" id="ov-recharges">—</h3>
        </div>
        <div class="w-12 h-12 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400">
          <i data-lucide="zap" class="w-6 h-6"></i>
        </div>
      </div>

      <div class="glass-panel rounded-xl p-5 border border-slate-200 flex items-center justify-between">
        <div>
          <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Revenue Collected</p>
          <h3 class="text-2xl font-extrabold text-slate-900 mt-1" id="ov-revenue">—</h3>
        </div>
        <div class="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-600">
          <i data-lucide="wallet" class="w-6 h-6"></i>
        </div>
      </div>
    </div>

    <!-- Info Cards Grid -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div class="glass-panel rounded-xl p-6 border border-slate-200">
        <h4 class="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2 mb-4">
          <i data-lucide="database" class="w-4 h-4 text-indigo-400"></i>
          MongoDB Collections Status
        </h4>
        <div class="divide-y divide-slate-100 text-xs">
          <div class="flex justify-between py-2.5"><span class="text-slate-500">customers</span><span class="font-semibold text-slate-800" id="ov-c2">—</span></div>
          <div class="flex justify-between py-2.5"><span class="text-slate-500">login_events</span><span class="font-semibold text-slate-800" id="ov-e2">—</span></div>
          <div class="flex justify-between py-2.5"><span class="text-slate-500">recharges</span><span class="font-semibold text-slate-800" id="ov-r2">—</span></div>
          <div class="flex justify-between py-2.5"><span class="text-slate-500">admins</span><span class="font-semibold text-sky-600" id="ov-a2">—</span></div>
        </div>
      </div>

      <div class="glass-panel rounded-xl p-6 border border-slate-200">
        <h4 class="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2 mb-4">
          <i data-lucide="award" class="w-4 h-4 text-emerald-600"></i>
          Subscription & Plans
        </h4>
        <div class="divide-y divide-slate-100 text-xs">
          <div class="flex justify-between py-2.5"><span class="text-slate-500">Free Accounts</span><span class="font-semibold text-sky-600" id="ov-free">—</span></div>
          <div class="flex justify-between py-2.5"><span class="text-slate-500">Paid Accounts</span><span class="font-semibold text-emerald-600" id="ov-paid">—</span></div>
          <div class="flex justify-between py-2.5"><span class="text-slate-500">Default Trial Duration</span><span class="text-slate-700 font-medium">30 minutes</span></div>
        </div>
      </div>
    </div>
  </main>

  <!-- ── CUSTOMERS PAGE ── -->
  <main id="page-customers" class="page-view hidden p-8 flex-1">
    <!-- Toolbar with Search Bar, Filters & Export -->
    <div class="flex flex-wrap items-center justify-between gap-4 mb-6">
      <div class="flex items-center gap-3 flex-wrap">
        <div class="relative">
          <i data-lucide="search" class="w-4 h-4 text-slate-700 absolute left-3 top-1/2 -translate-y-1/2"></i>
          <input id="c-search" type="text" placeholder="Search phone or referral…" oninput="cSkip=0; loadCustomers()" class="w-64 bg-white border border-slate-200 rounded-lg pl-9 pr-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 transition"/>
        </div>
        
        <!-- Filter: Plan Type -->
        <select id="c-filter-plan" onchange="cSkip=0; loadCustomers()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-indigo-500 transition">
          <option value="">All Plans</option>
          <option value="free">Free</option>
          <option value="paid">Paid</option>
        </select>

        <!-- Filter: Source -->
        <select id="c-filter-source" onchange="cSkip=0; loadCustomers()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-indigo-500 transition">
          <option value="">All Sources</option>
          <option value="admin">admin</option>
          <option value="self">self</option>
        </select>

        <button onclick="loadCustomers()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-slate-700 text-xs font-medium transition">
          <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-slate-500"></i>
          <span>Reload</span>
        </button>

        <!-- Export button -->
        <button onclick="openExportModal('customers')" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-indigo-700/70 bg-indigo-900/30 hover:bg-indigo-900/50 text-indigo-300 text-xs font-medium transition" title="Export Customers to XLSX or JSON">
          <i data-lucide="download" class="w-3.5 h-3.5 text-indigo-400"></i>
          <span>Export</span>
        </button>
      </div>

      <div class="flex items-center gap-3">
        <!-- Add Customer Button -->
        <button onclick="openAddCustomerModal()" class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-sm transition">
          <i data-lucide="user-plus" class="w-4 h-4"></i>
          <span>Add Customer</span>
        </button>
      </div>
    </div>

    <!-- Table Wrap -->
    <div class="glass-panel rounded-xl border border-slate-200 overflow-hidden shadow-sm">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-white/90 border-b border-slate-200 text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              <th class="py-3.5 px-4">Phone Number</th>
              <th class="py-3.5 px-4">Active Session</th>
              <th class="py-3.5 px-4">OTP</th>
              <th class="py-3.5 px-4">Source</th>
              <th class="py-3.5 px-4">Plan</th>
              <th class="py-3.5 px-4">Referral</th>
              <th class="py-3.5 px-4">Time Left</th>
              <th class="py-3.5 px-4">Expiry</th>
              <th class="py-3.5 px-4">Last Login</th>
              <th class="py-3.5 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="c-tbody" class="divide-y divide-slate-100/60">
            <tr><td colspan="9" class="py-8 text-center text-slate-700 font-medium">Loading customers…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pagination -->
    <div class="flex items-center justify-between mt-4 text-xs text-slate-500">
      <span id="c-page-info">Showing records…</span>
      <div class="flex items-center gap-2">
        <button id="c-prev" onclick="customerPage(-1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Previous</button>
        <button id="c-next" onclick="customerPage(1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Next</button>
      </div>
    </div>
  </main>

  <!-- ── RECHARGES PAGE ── -->
  <main id="page-recharges" class="page-view hidden p-8 flex-1">
    <!-- Toolbar with Search Bar, Filters & Export -->
    <div class="flex flex-wrap items-center justify-between gap-4 mb-6">
      <div class="flex items-center gap-3 flex-wrap">
        <div class="relative">
          <i data-lucide="search" class="w-4 h-4 text-slate-700 absolute left-3 top-1/2 -translate-y-1/2"></i>
          <input id="r-search" type="text" placeholder="Search phone number…" oninput="rSkip=0; loadRecharges()" class="w-64 bg-white border border-slate-200 rounded-lg pl-9 pr-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 transition"/>
        </div>

        <!-- Filter: Status -->
        <select id="r-filter-status" onchange="rSkip=0; loadRecharges()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-indigo-500 transition">
          <option value="">All Statuses</option>
          <option value="completed">Completed</option>
          <option value="manual">Manual</option>
          <option value="pending">Pending</option>
          <option value="failed">Failed</option>
        </select>

        <!-- Filter: Source -->
        <select id="r-filter-source" onchange="rSkip=0; loadRecharges()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-indigo-500 transition">
          <option value="">All Sources</option>
          <option value="manual">manual</option>
          <option value="admin">admin</option>
          <option value="gateway">gateway</option>
        </select>

        <button onclick="loadRecharges()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-slate-700 text-xs font-medium transition">
          <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-slate-500"></i>
          <span>Reload</span>
        </button>

        <!-- Export button -->
        <button onclick="openExportModal('recharges')" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-amber-700/70 bg-amber-900/30 hover:bg-amber-900/50 text-amber-700 text-xs font-medium transition" title="Export Recharges to XLSX or JSON">
          <i data-lucide="download" class="w-3.5 h-3.5 text-amber-400"></i>
          <span>Export</span>
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="glass-panel rounded-xl border border-slate-200 overflow-hidden shadow-sm">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-white/90 border-b border-slate-200 text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              <th class="py-3.5 px-4">Phone Number</th>
              <th class="py-3.5 px-4">Amount</th>
              <th class="py-3.5 px-4">Time Adjustment</th>
              <th class="py-3.5 px-4">Plan Change</th>
              <th class="py-3.5 px-4">Source</th>
              <th class="py-3.5 px-4">Payment Status</th>
              <th class="py-3.5 px-4">Date</th>
              <th class="py-3.5 px-4">Notes</th>
              <th class="py-3.5 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="r-tbody" class="divide-y divide-slate-100/60">
            <tr><td colspan="9" class="py-8 text-center text-slate-700 font-medium">Loading recharges…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pagination -->
    <div class="flex items-center justify-between mt-4 text-xs text-slate-500">
      <span id="r-page-info">Showing records…</span>
      <div class="flex items-center gap-2">
        <button id="r-prev" onclick="rechargePage(-1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Previous</button>
        <button id="r-next" onclick="rechargePage(1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Next</button>
      </div>
    </div>
  </main>

  <!-- ── EVENTS PAGE ── -->
  <main id="page-events" class="page-view hidden p-8 flex-1">
    <!-- Toolbar with Search Bar, Filters & Export -->
    <div class="flex flex-wrap items-center justify-between gap-4 mb-6">
      <div class="flex items-center gap-3 flex-wrap">
        <div class="relative">
          <i data-lucide="search" class="w-4 h-4 text-slate-700 absolute left-3 top-1/2 -translate-y-1/2"></i>
          <input id="e-search" type="text" placeholder="Search phone or IP address…" oninput="eSkip=0; loadEvents()" class="w-64 bg-white border border-slate-200 rounded-lg pl-9 pr-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 transition"/>
        </div>

        <!-- Filter: Device Type -->
        <select id="e-filter-device" onchange="eSkip=0; loadEvents()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-indigo-500 transition">
          <option value="">All Devices</option>
          <option value="pc">PC / Desktop</option>
          <option value="mobile">Mobile</option>
          <option value="tablet">Tablet</option>
          <option value="bot">Bot / Crawler</option>
        </select>

        <button onclick="loadEvents()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-slate-700 text-xs font-medium transition">
          <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-slate-500"></i>
          <span>Reload</span>
        </button>

        <!-- Export button -->
        <button onclick="openExportModal('events')" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-cyan-700/70 bg-cyan-900/30 hover:bg-cyan-900/50 text-cyan-300 text-xs font-medium transition" title="Export Login Events to XLSX or JSON">
          <i data-lucide="download" class="w-3.5 h-3.5 text-cyan-600"></i>
          <span>Export</span>
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="glass-panel rounded-xl border border-slate-200 overflow-hidden shadow-sm">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-white/90 border-b border-slate-200 text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              <th class="py-3.5 px-4">Phone Number</th>
              <th class="py-3.5 px-4">IP Address</th>
              <th class="py-3.5 px-4">Location</th>
              <th class="py-3.5 px-4">Browser / OS</th>
              <th class="py-3.5 px-4">Device</th>
              <th class="py-3.5 px-4">Type</th>
              <th class="py-3.5 px-4">Timestamp</th>
              <th class="py-3.5 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="e-tbody" class="divide-y divide-slate-100/60">
            <tr><td colspan="8" class="py-8 text-center text-slate-700 font-medium">Loading events…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pagination -->
    <div class="flex items-center justify-between mt-4 text-xs text-slate-500">
      <span id="e-page-info">Showing records…</span>
      <div class="flex items-center gap-2">
        <button id="e-prev" onclick="eventPage(-1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Previous</button>
        <button id="e-next" onclick="eventPage(1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Next</button>
      </div>
    </div>
  </main>

  <!-- ── SESSIONS PAGE (CUSTOMER LOGIN SESSIONS & LOGOUT) ── -->
  <main id="page-sessions" class="page-view hidden p-8 flex-1">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center justify-between gap-4 mb-6">
      <div class="flex items-center gap-3 flex-wrap">
        <div class="relative">
          <i data-lucide="search" class="w-4 h-4 text-slate-700 absolute left-3 top-1/2 -translate-y-1/2"></i>
          <input id="s-search" type="text" placeholder="Search phone or session ID…" oninput="sSkip=0; loadSessions()" class="w-64 bg-white border border-slate-200 rounded-lg pl-9 pr-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-indigo-500 transition"/>
        </div>

        <!-- Filter: Session Status -->
        <select id="s-filter-status" onchange="sSkip=0; loadSessions()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-indigo-500 transition">
          <option value="all">All Sessions</option>
          <option value="active">Active Sessions Only</option>
          <option value="terminated">Terminated / Logged Out</option>
        </select>

        <button onclick="loadSessions()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-slate-700 text-xs font-medium transition">
          <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-slate-500"></i>
          <span>Reload</span>
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="glass-panel rounded-xl border border-slate-200 overflow-hidden shadow-sm">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-white/90 border-b border-slate-200 text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              <th class="py-3.5 px-4">Customer Phone</th>
              <th class="py-3.5 px-4">Active Session ID</th>
              <th class="py-3.5 px-4">Plan</th>
              <th class="py-3.5 px-4">Device ID</th>
              <th class="py-3.5 px-4">Last Login</th>
              <th class="py-3.5 px-4">Status</th>
              <th class="py-3.5 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="s-tbody" class="divide-y divide-slate-100/60">
            <tr><td colspan="7" class="py-8 text-center text-slate-700 font-medium">Loading sessions…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pagination -->
    <div class="flex items-center justify-between mt-4 text-xs text-slate-500">
      <span id="s-page-info">Showing records…</span>
      <div class="flex items-center gap-2">
        <button id="s-prev" onclick="sessionPage(-1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Previous</button>
        <button id="s-next" onclick="sessionPage(1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Next</button>
      </div>
    </div>
  </main>

  <!-- ── ADMINS PAGE ── -->
  <main id="page-admins" class="page-view hidden p-8 flex-1">
    <!-- Toolbar with Search Bar, Role Filter, Status Filter & Add Admin -->
    <div class="flex flex-wrap items-center justify-between gap-4 mb-6">
      <div class="flex items-center gap-3 flex-wrap">
        <div class="relative">
          <i data-lucide="search" class="w-4 h-4 text-slate-700 absolute left-3 top-1/2 -translate-y-1/2"></i>
          <input id="a-search" type="text" placeholder="Search name or email…" oninput="aSkip=0; loadAdmins()" class="w-64 bg-white border border-slate-200 rounded-lg pl-9 pr-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-sky-500 transition"/>
        </div>

        <!-- Filter: Role -->
        <select id="a-filter-role" onchange="aSkip=0; loadAdmins()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-sky-500 transition">
          <option value="">All Roles</option>
          <option value="superadmin">superadmin</option>
          <option value="admin">admin</option>
          <option value="moderator">moderator</option>
        </select>

        <!-- Filter: Status -->
        <select id="a-filter-status" onchange="aSkip=0; loadAdmins()" class="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-700 focus:outline-none focus:border-sky-500 transition">
          <option value="">All Statuses</option>
          <option value="true">Active</option>
          <option value="false">Inactive</option>
        </select>

        <button onclick="loadAdmins()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-slate-700 text-xs font-medium transition">
          <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-slate-500"></i>
          <span>Reload</span>
        </button>

        <!-- Export button -->
        <button onclick="openExportModal('admins')" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-sky-700/70 bg-sky-900/30 hover:bg-sky-900/50 text-sky-300 text-xs font-medium transition" title="Export Admins to XLSX or JSON">
          <i data-lucide="download" class="w-3.5 h-3.5 text-sky-600"></i>
          <span>Export</span>
        </button>
      </div>

      <div class="flex items-center gap-3">
        <!-- Admin Login JWT Tester Button -->
        <button onclick="openAdminLoginModal()" class="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border border-sky-800 bg-sky-950/60 hover:bg-sky-900/60 text-sky-300 text-xs font-semibold shadow-sm transition">
          <i data-lucide="key" class="w-4 h-4"></i>
          <span>Test Admin Login</span>
        </button>

        <!-- Add Admin Button -->
        <button onclick="openAddAdminModal()" class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold shadow-sm transition">
          <i data-lucide="user-plus" class="w-4 h-4"></i>
          <span>Add Admin</span>
        </button>
      </div>
    </div>

    <!-- Table Wrap -->
    <div class="glass-panel rounded-xl border border-slate-200 overflow-hidden shadow-sm">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-white/90 border-b border-slate-200 text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
              <th class="py-3.5 px-4">Admin</th>
              <th class="py-3.5 px-4">Role</th>
              <th class="py-3.5 px-4">Status</th>
              <th class="py-3.5 px-4">Password Hash</th>
              <th class="py-3.5 px-4">Created At</th>
              <th class="py-3.5 px-4">Last Login</th>
              <th class="py-3.5 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="a-tbody" class="divide-y divide-slate-100/60">
            <tr><td colspan="7" class="py-8 text-center text-slate-700 font-medium">Loading admin users…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pagination -->
    <div class="flex items-center justify-between mt-4 text-xs text-slate-500">
      <span id="a-page-info">Showing records…</span>
      <div class="flex items-center gap-2">
        <button id="a-prev" onclick="adminPage(-1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Previous</button>
        <button id="a-next" onclick="adminPage(1)" class="px-3 py-1.5 rounded border border-slate-200 bg-slate-100 hover:bg-slate-100 disabled:opacity-40 transition">Next</button>
      </div>
    </div>
  </main>

  <!-- ── STORE & CONFIG PAGE ── -->
  <main id="page-store" class="page-view hidden p-8 flex-1">
    <div class="max-w-5xl space-y-6">
      <!-- Store Header Card -->
      <div class="glass-panel rounded-xl p-6 border border-slate-200">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400 shadow-sm">
              <i data-lucide="shopping-bag" class="w-5 h-5"></i>
            </div>
            <div>
              <h3 class="text-base font-bold text-slate-900">Dynamic Store & System Configuration</h3>
              <p class="text-xs text-slate-500">Synchronized dynamically via backend API (<code>GET /admin/store</code>, <code>PUT /admin/store</code>)</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <span id="store-sync-status" class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium bg-emerald-50 border border-emerald-200 text-emerald-700">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              <span>API Connected</span>
            </span>
            <button onclick="loadStoreFromApi(true)" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-slate-700 text-xs font-medium transition">
              <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-slate-500"></i>
              <span>Fetch API</span>
            </button>
          </div>
        </div>

        <!-- Dynamic Form Fields -->
        <form onsubmit="event.preventDefault(); saveStoreSettings();" class="mt-6 space-y-4">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Backend URL</label>
              <input type="text" id="store-backend-url" required class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
              <p class="text-[10px] text-slate-700 mt-1">Host endpoint for Electron assistant synchronization</p>
            </div>

            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Default Plan For New Registrations</label>
              <select id="store-default-plan" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
                <option value="free">free (Standard Trial)</option>
                <option value="paid">paid (Instant Unlimited)</option>
              </select>
              <p class="text-[10px] text-slate-700 mt-1">Tier assigned when customers initiate their first login</p>
            </div>

            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Default Trial Balance (Seconds)</label>
              <div class="flex gap-2">
                <input type="number" id="store-trial-time" min="0" required class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
                <span id="store-trial-minutes-display" class="px-3 py-2 rounded-lg bg-dark-950 border border-slate-200 text-xs text-indigo-400 font-mono whitespace-nowrap">30m</span>
              </div>
              <p class="text-[10px] text-slate-700 mt-1">Initial voice & transcription allocation for free users</p>
            </div>

            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Rows Per Page (Universal Pagination)</label>
              <select id="store-per-page" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
                <option value="10">10 rows per page</option>
                <option value="20" selected>20 rows per page</option>
                <option value="50">50 rows per page</option>
                <option value="100">100 rows per page</option>
              </select>
              <p class="text-[10px] text-slate-700 mt-1">Applied dynamically across Customers, Recharges, & Events tables</p>
            </div>

            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Support Phone / Help Desk</label>
              <input type="text" id="store-support-phone" placeholder="+919876543210" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
              <p class="text-[10px] text-slate-700 mt-1">Contact number displayed for customer assistance</p>
            </div>

            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">System Currency</label>
              <input type="text" id="store-currency" value="INR" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 font-mono uppercase focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
              <p class="text-[10px] text-slate-700 mt-1">Currency symbol/code for recharge transactions</p>
            </div>

            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Session Expiry Timeout (Minutes)</label>
              <input type="number" id="store-session-timeout" min="15" value="1440" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
              <p class="text-[10px] text-slate-700 mt-1">Inactivity window before automatic session revocation</p>
            </div>

            <div>
              <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Auto-Generate OTP Policy</label>
              <select id="store-auto-otp" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
                <option value="true" selected>Enabled (Auto 6-digit numeric OTP)</option>
                <option value="false">Manual Only</option>
              </select>
              <p class="text-[10px] text-slate-700 mt-1">Automatic verification dispatch upon customer phone registration</p>
            </div>
          </div>

          <div class="mt-6 flex flex-wrap items-center justify-between gap-3 pt-4 border-t border-slate-200">
            <div class="text-[11px] text-slate-500">
              Last saved: <span id="store-last-saved" class="font-mono text-slate-700">—</span>
            </div>
            <div class="flex items-center gap-3">
              <button type="button" onclick="resetStoreDefaults()" class="px-4 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium transition">
                Reset System Defaults
              </button>
              <button type="submit" id="btn-save-store" class="inline-flex items-center gap-1.5 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-sm transition">
                <i data-lucide="check" class="w-4 h-4"></i>
                <span>Save to MongoDB API</span>
              </button>
            </div>
          </div>
        </form>
      </div>

      <!-- Live API Response Preview Card -->
      <div class="glass-panel rounded-xl p-6 border border-slate-200">
        <div class="flex items-center justify-between mb-3">
          <h4 class="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-2">
            <i data-lucide="database" class="w-4 h-4 text-rose-400"></i>
            Live API Response (<code>GET /admin/store</code>)
          </h4>
          <span class="text-[10px] font-mono text-slate-700">collection: pas_db.system_settings</span>
        </div>
        <pre id="store-json-preview" class="bg-dark-950 border border-slate-200 rounded-lg p-4 text-rose-300 text-[11px] font-mono overflow-x-auto max-h-64"></pre>
      </div>
    </div>
  </main>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- MODAL: EXPORT DATA (XLSX OR JSON)                              -->
<!-- ══════════════════════════════════════════════════════════════ -->

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- MODAL: CUSTOMER USAGE & MINUTES HISTORY TRACKER                -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="customer-usage-modal" onclick="closeModalOnOverlay(event, 'customer-usage-modal')">
  <div class="bg-white border border-slate-200 shadow-2xl w-full max-w-3xl rounded-2xl border border-slate-200 p-6 shadow-2xl relative overflow-y-auto max-h-[90vh]">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-cyan-500/10 flex items-center justify-center text-cyan-600">
          <i data-lucide="history" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 class="text-sm font-bold text-slate-900">Minutes Consumption & Usage History</h3>
          <p class="text-[11px] text-slate-500">Customer: <span id="usage-modal-phone" class="font-mono text-cyan-300 font-bold">—</span></p>
        </div>
      </div>
      <button onclick="closeModal('customer-usage-modal')" class="text-slate-500 hover:text-slate-900 transition p-1">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>
    </div>

    <!-- Summary Stat Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-4 gap-3 my-5">
      <div class="bg-dark-950/80 border border-slate-200 rounded-xl p-3 text-center">
        <div class="text-[10px] text-slate-500 font-semibold uppercase">Remaining Balance</div>
        <div id="usage-stat-balance" class="text-base font-extrabold text-emerald-600 mt-1">—</div>
      </div>
      <div class="bg-dark-950/80 border border-slate-200 rounded-xl p-3 text-center">
        <div class="text-[10px] text-slate-500 font-semibold uppercase">Total Minutes Used</div>
        <div id="usage-stat-consumed" class="text-base font-extrabold text-amber-400 mt-1">—</div>
      </div>
      <div class="bg-dark-950/80 border border-slate-200 rounded-xl p-3 text-center">
        <div class="text-[10px] text-slate-500 font-semibold uppercase">Total Sessions</div>
        <div id="usage-stat-sessions" class="text-base font-extrabold text-indigo-400 mt-1">—</div>
      </div>
      <div class="bg-dark-950/80 border border-slate-200 rounded-xl p-3 text-center">
        <div class="text-[10px] text-slate-500 font-semibold uppercase">Active Device</div>
        <div id="usage-stat-device" class="text-xs font-mono font-bold text-slate-800 mt-1.5 truncate">None</div>
      </div>
    </div>

    <!-- Usage History Log Table -->
    <div class="glass-panel rounded-xl border border-slate-200 overflow-hidden shadow-sm">
      <div class="overflow-x-auto max-h-72 overflow-y-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-dark-950/90 border-b border-slate-200 text-[11px] uppercase tracking-wider text-slate-500 font-semibold sticky top-0">
              <th class="py-2.5 px-3">Date & Time</th>
              <th class="py-2.5 px-3">Event / Action</th>
              <th class="py-2.5 px-3">Minutes Consumed</th>
              <th class="py-2.5 px-3">Balance (Before → After)</th>
              <th class="py-2.5 px-3">Device / Session</th>
            </tr>
          </thead>
          <tbody id="usage-history-tbody" class="divide-y divide-slate-100/60 font-mono text-[11px]">
            <tr><td colspan="5" class="py-6 text-center text-slate-500">Loading usage history…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="mt-6 flex justify-between items-center pt-4 border-t border-slate-200">
      <span class="text-[11px] text-slate-500">Each login consumes 5 mins (300s). Extra devices auto force-logged out.</span>
      <button onclick="closeModal('customer-usage-modal')" class="px-4 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium transition">
        Close
      </button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="export-modal" onclick="closeModalOnOverlay(event, 'export-modal')">
  <div class="bg-white border border-slate-200 rounded-2xl w-full max-w-md p-6 shadow-2xl">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
          <i data-lucide="download" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 class="text-sm font-bold text-slate-900">Export Dataset</h3>
          <p class="text-[11px] text-slate-500">Select export file format</p>
        </div>
      </div>
      <button onclick="closeModal('export-modal')" class="text-slate-500 hover:text-slate-900 transition">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>
    </div>

    <div class="mt-5 space-y-4">
      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Target Dataset</label>
        <input type="text" id="export-resource-display" readonly class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 font-mono uppercase"/>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-2">Choose Format</label>
        <div class="grid grid-cols-2 gap-3">
          <label class="flex items-center gap-3 p-3.5 rounded-xl border border-slate-200 bg-slate-50 hover:bg-white cursor-pointer transition">
            <input type="radio" name="export-format" value="xlsx" checked class="text-indigo-600 focus:ring-indigo-500"/>
            <div>
              <div class="text-xs font-bold text-slate-900 flex items-center gap-1.5">
                <i data-lucide="file-spreadsheet" class="w-3.5 h-3.5 text-emerald-600"></i>
                <span>Excel (.xlsx)</span>
              </div>
              <div class="text-[10px] text-slate-500">Formatted spreadsheet</div>
            </div>
          </label>

          <label class="flex items-center gap-3 p-3.5 rounded-xl border border-slate-200 bg-slate-50 hover:bg-white cursor-pointer transition">
            <input type="radio" name="export-format" value="json" class="text-indigo-600 focus:ring-indigo-500"/>
            <div>
              <div class="text-xs font-bold text-slate-900 flex items-center gap-1.5">
                <i data-lucide="file-code" class="w-3.5 h-3.5 text-amber-400"></i>
                <span>JSON (.json)</span>
              </div>
              <div class="text-[10px] text-slate-500">Raw structured data</div>
            </div>
          </label>
        </div>
      </div>
    </div>

    <div class="mt-6 flex justify-end gap-3 pt-4 border-t border-slate-200">
      <button onclick="closeModal('export-modal')" class="px-4 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium transition">
        Cancel
      </button>
      <button onclick="executeExport()" class="inline-flex items-center gap-1.5 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-sm transition">
        <i data-lucide="download" class="w-4 h-4"></i>
        <span>Download Export</span>
      </button>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- MODAL: ADD CUSTOMER                                            -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="add-customer-modal" onclick="closeModalOnOverlay(event, 'add-customer-modal')">
  <div class="bg-white border border-slate-200 rounded-2xl w-full max-w-lg p-6 shadow-2xl overflow-y-auto max-h-[90vh]">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
          <i data-lucide="user-plus" class="w-4 h-4"></i>
        </div>
        <h3 class="text-sm font-bold text-slate-900">Add New Customer</h3>
      </div>
      <button onclick="closeModal('add-customer-modal')" class="text-slate-500 hover:text-slate-900 transition">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>
    </div>

    <form onsubmit="event.preventDefault(); submitAddCustomer();" class="mt-4 space-y-4">
      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Phone Number *</label>
        <input type="text" id="add-phone" required placeholder="+919876543210" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Initial OTP</label>
          <div class="flex gap-2">
            <input type="text" id="add-otp" maxlength="6" placeholder="Auto" class="w-full bg-dark-950 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-amber-700 font-mono tracking-widest focus:outline-none focus:border-indigo-500 transition"/>
            <button type="button" onclick="generateRandomOtp('add-otp')" class="px-2.5 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs transition" title="Generate random OTP">↻</button>
          </div>
        </div>
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Plan Type</label>
          <select id="add-plan" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="free" selected>Free (Default)</option>
            <option value="paid">Paid</option>
          </select>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Source</label>
          <select id="add-source" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="admin" selected>admin</option>
            <option value="self">self</option>
          </select>
        </div>
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Initial Time (Minutes)</label>
          <input type="number" id="add-time-min" min="0" value="30" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
        </div>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Custom Referral Code (Optional)</label>
        <input type="text" id="add-referral" placeholder="Leave empty for auto-generated" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Device ID (Optional)</label>
        <input type="text" id="add-device" placeholder="e.g. dev_mac_a8f9" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
      </div>

      <div class="pt-3 border-t border-slate-200 flex justify-end gap-3">
        <button type="button" onclick="closeModal('add-customer-modal')" class="px-4 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium transition">Cancel</button>
        <button type="submit" id="btn-submit-add-customer" class="inline-flex items-center gap-1.5 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-sm transition">
          <i data-lucide="user-plus" class="w-4 h-4"></i>
          <span>Create Customer</span>
        </button>
      </div>
    </form>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- MODAL: EDIT CUSTOMER                                           -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="edit-customer-modal" onclick="closeModalOnOverlay(event, 'edit-customer-modal')">
  <div class="bg-white border border-slate-200 rounded-2xl w-full max-w-lg p-6 shadow-2xl overflow-y-auto max-h-[90vh]">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
          <i data-lucide="edit-3" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 class="text-sm font-bold text-slate-900">Edit Customer</h3>
          <p class="text-[11px] text-slate-500 font-mono" id="edit-phone-display"></p>
        </div>
      </div>
      <button onclick="closeModal('edit-customer-modal')" class="text-slate-500 hover:text-slate-900 transition">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>
    </div>

    <form onsubmit="event.preventDefault(); submitEditCustomer();" class="mt-4 space-y-4">
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Customer OTP</label>
          <div class="flex gap-2">
            <input type="text" id="edit-otp" maxlength="6" class="w-full bg-dark-950 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-amber-700 font-mono tracking-widest focus:outline-none focus:border-indigo-500 transition"/>
            <button type="button" onclick="generateRandomOtp('edit-otp')" class="px-2.5 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs transition" title="Generate random OTP">↻</button>
          </div>
        </div>
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Plan Tier</label>
          <select id="edit-plan" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="free">Free</option>
            <option value="paid">Paid</option>
          </select>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Source</label>
          <select id="edit-source" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="admin">admin</option>
            <option value="self">self</option>
          </select>
        </div>
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Remaining Seconds</label>
          <input type="number" id="edit-time-sec" min="0" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
        </div>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Referral Code Generated</label>
        <input type="text" id="edit-referral" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Device ID</label>
        <input type="text" id="edit-device" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
      </div>

      <div class="pt-3 border-t border-slate-200 flex justify-end gap-3">
        <button type="button" onclick="closeModal('edit-customer-modal')" class="px-4 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium transition">Cancel</button>
        <button type="submit" id="btn-submit-edit-customer" class="inline-flex items-center gap-1.5 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-sm transition">
          <i data-lucide="check" class="w-4 h-4"></i>
          <span>Save Changes</span>
        </button>
      </div>
    </form>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- MODAL: LOGIN ATTEMPTS                                          -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="attempts-overlay" onclick="closeModalOnOverlay(event, 'attempts-overlay')">
  <div class="bg-white border border-slate-200 rounded-2xl w-full max-w-4xl p-6 shadow-2xl overflow-y-auto max-h-[90vh]">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center text-amber-400">
          <i data-lucide="key-round" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 id="att-title" class="text-sm font-bold text-slate-900">Login Attempts</h3>
          <p id="att-subtitle" class="text-[11px] text-slate-500 font-mono"></p>
        </div>
      </div>
      <button onclick="closeModal('attempts-overlay')" class="text-slate-500 hover:text-slate-900 transition">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>
    </div>

    <div class="mt-4 overflow-x-auto">
      <table class="w-full text-left text-xs border-collapse">
        <thead>
          <tr class="bg-dark-950 border-b border-slate-200 text-[11px] uppercase tracking-wider text-slate-500 font-semibold">
            <th class="py-2.5 px-3.5">Timestamp</th>
            <th class="py-2.5 px-3.5">IP Address</th>
            <th class="py-2.5 px-3.5">Location</th>
            <th class="py-2.5 px-3.5">Browser / OS</th>
            <th class="py-2.5 px-3.5">Device</th>
            <th class="py-2.5 px-3.5">Type</th>
            <th class="py-2.5 px-3.5 text-right">View Detail</th>
          </tr>
        </thead>
        <tbody id="att-tbody" class="divide-y divide-slate-100/60">
          <tr><td colspan="7" class="py-8 text-center text-slate-700 font-medium">Loading attempts…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- MODAL: RECHARGE CUSTOMER (+ / - Time, Plan Upgrade, Amount)    -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="recharge-overlay" onclick="closeModalOnOverlay(event, 'recharge-overlay')">
  <div class="bg-white border border-slate-200 rounded-2xl w-full max-w-lg p-6 shadow-2xl overflow-y-auto max-h-[90vh]">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center text-emerald-600">
          <i data-lucide="zap" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 class="text-sm font-bold text-slate-900">Recharge Customer Plan & Time</h3>
          <p id="rec-phone-sub" class="text-[11px] text-slate-500 font-mono"></p>
        </div>
      </div>
      <button onclick="closeModal('recharge-overlay')" class="text-slate-500 hover:text-slate-900 transition">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>
    </div>

    <form onsubmit="event.preventDefault(); submitRecharge();" class="mt-4 space-y-4">
      <div class="p-3 rounded-xl bg-dark-950 border border-slate-200 text-xs flex justify-between items-center">
        <div>
          <span class="text-slate-500 block text-[10px] uppercase">Current Plan</span>
          <span id="rec-curr-plan" class="font-bold text-indigo-700 bg-indigo-50 border border-indigo-200 px-2 py-0.5 rounded uppercase text-xs">—</span>
        </div>
        <div class="text-right">
          <span class="text-slate-500 block text-[10px] uppercase">Current Time Balance</span>
          <span id="rec-curr-time" class="font-bold text-emerald-600">—</span>
        </div>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Adjustment Mode (+ / -)</label>
        <div class="grid grid-cols-2 gap-2 p-1 rounded-lg bg-dark-950 border border-slate-200">
          <button type="button" id="btn-mode-add" onclick="setRecMode('add')" class="py-1.5 rounded-md text-xs font-semibold bg-indigo-600 text-white transition">
            + Increase Time (Add)
          </button>
          <button type="button" id="btn-mode-sub" onclick="setRecMode('sub')" class="py-1.5 rounded-md text-xs font-semibold text-slate-500 hover:text-slate-900 transition">
            - Decrease Time (Minus)
          </button>
        </div>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Quick Presets</label>
        <div class="flex flex-wrap gap-2">
          <button type="button" onclick="applyQuickTime(15, 'min')" class="px-2.5 py-1 rounded bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-200 transition">+15m</button>
          <button type="button" onclick="applyQuickTime(30, 'min')" class="px-2.5 py-1 rounded bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-200 transition">+30m</button>
          <button type="button" onclick="applyQuickTime(1, 'hour')" class="px-2.5 py-1 rounded bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-200 transition">+1h</button>
          <button type="button" onclick="applyQuickTime(2, 'hour')" class="px-2.5 py-1 rounded bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-200 transition">+2h</button>
          <button type="button" onclick="applyQuickTime(-15, 'min')" class="px-2.5 py-1 rounded bg-rose-50 hover:bg-rose-100 text-rose-700 text-xs font-semibold border border-rose-200 transition">-15m</button>
          <button type="button" onclick="applyQuickTime(-30, 'min')" class="px-2.5 py-1 rounded bg-rose-50 hover:bg-rose-100 text-rose-700 text-xs font-semibold border border-rose-200 transition">-30m</button>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Time Value</label>
          <input type="number" id="rec-time-val" min="1" value="30" oninput="updateRecPreview()" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
        </div>
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Unit</label>
          <select id="rec-time-unit" onchange="updateRecPreview()" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="min" selected>Minutes</option>
            <option value="hour">Hours</option>
            <option value="sec">Seconds</option>
          </select>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Upgrade / Set Plan</label>
          <select id="rec-plan" onchange="updateRecPreview()" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="paid" selected>Paid (Upgraded)</option>
            <option value="free">Free</option>
          </select>
        </div>
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Payment Amount (INR)</label>
          <input type="number" step="0.01" min="0" id="rec-amount" value="0.00" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Recharge Source</label>
          <select id="rec-source" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="manual" selected>manual</option>
            <option value="admin">admin</option>
            <option value="gateway">gateway</option>
          </select>
        </div>
        <div>
          <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Payment Status</label>
          <select id="rec-status" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition">
            <option value="completed" selected>completed</option>
            <option value="manual">manual</option>
            <option value="pending">pending</option>
            <option value="failed">failed</option>
          </select>
        </div>
      </div>

      <div>
        <label class="block text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1.5">Transaction Notes (Optional)</label>
        <input type="text" id="rec-notes" placeholder="e.g. Manual bank transfer verified, cash received" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs text-slate-900 focus:outline-none focus:border-indigo-500 focus:bg-white transition"/>
      </div>

      <div class="p-3 rounded-xl bg-indigo-950/40 border border-indigo-800/60 text-xs text-indigo-300" id="rec-preview">
        Calculating preview…
      </div>

      <div class="pt-3 border-t border-slate-200 flex justify-end gap-3">
        <button type="button" onclick="closeModal('recharge-overlay')" class="px-4 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium transition">Cancel</button>
        <button type="submit" id="rec-submit-btn" class="inline-flex items-center gap-1.5 px-5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm transition">
          <i data-lucide="zap" class="w-4 h-4"></i>
          <span>Apply Recharge</span>
        </button>
      </div>
    </form>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- MODAL: VIEW DETAILS                                            -->
<!-- ══════════════════════════════════════════════════════════════ -->

<!-- ── ADD ADMIN MODAL ── -->
<div class="modal-overlay" id="add-admin-modal" onclick="closeModalOnOverlay(event, 'add-admin-modal')">
  <div class="bg-white border border-slate-200 shadow-2xl w-full max-w-md rounded-2xl border border-slate-200 p-6 shadow-2xl relative">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-sky-500/10 flex items-center justify-center text-sky-600">
          <i data-lucide="shield" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 class="text-sm font-bold text-slate-900">Create Admin Account</h3>
          <p class="text-[11px] text-slate-500">Stores crypto-hashed bcrypt password</p>
        </div>
      </div>
      <button onclick="closeModal('add-admin-modal')" class="text-slate-500 hover:text-slate-900 p-1 rounded-lg">
        <i data-lucide="x" class="w-4 h-4"></i>
      </button>
    </div>

    <form onsubmit="submitAddAdmin(event)" class="mt-4 space-y-4">
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Full Name</label>
        <input type="text" id="add-a-name" required placeholder="Admin Name" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500"/>
      </div>
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Email Address</label>
        <input type="email" id="add-a-email" required placeholder="admin@example.com" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500"/>
      </div>
      <div>
        <div class="flex items-center justify-between mb-1">
          <label class="block text-xs font-semibold text-slate-700">Password</label>
          <button type="button" onclick="generateRandomAdminPassword('add-a-password')" class="text-[10px] text-sky-600 hover:text-sky-300 flex items-center gap-1">
            <i data-lucide="wand-2" class="w-3 h-3"></i> Generate Strong
          </button>
        </div>
        <input type="password" id="add-a-password" required minlength="6" placeholder="Minimum 6 characters" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500"/>
        <p class="text-[10px] text-slate-500 mt-1">Automatically encrypted with bcrypt salt before MongoDB persistence.</p>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs font-semibold text-slate-700 mb-1">Role</label>
          <select id="add-a-role" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500">
            <option value="admin">Admin</option>
            <option value="superadmin">Superadmin</option>
            <option value="moderator">Moderator</option>
          </select>
        </div>
        <div>
          <label class="block text-xs font-semibold text-slate-700 mb-1">Status</label>
          <select id="add-a-active" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500">
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </div>
      </div>
      <div class="pt-2 flex justify-end gap-2">
        <button type="button" onclick="closeModal('add-admin-modal')" class="px-4 py-2 rounded-lg border border-slate-200 hover:bg-slate-100 text-slate-700 text-xs font-medium">Cancel</button>
        <button type="submit" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold shadow-md">Create Admin</button>
      </div>
    </form>
  </div>
</div>

<!-- ── EDIT ADMIN MODAL ── -->
<div class="modal-overlay" id="edit-admin-modal" onclick="closeModalOnOverlay(event, 'edit-admin-modal')">
  <div class="bg-white border border-slate-200 shadow-2xl w-full max-w-md rounded-2xl border border-slate-200 p-6 shadow-2xl relative">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
          <i data-lucide="edit-3" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 class="text-sm font-bold text-slate-900">Edit Admin Account</h3>
          <p class="text-[11px] text-slate-500">Update account role, status or password</p>
        </div>
      </div>
      <button onclick="closeModal('edit-admin-modal')" class="text-slate-500 hover:text-slate-900 p-1 rounded-lg">
        <i data-lucide="x" class="w-4 h-4"></i>
      </button>
    </div>

    <form onsubmit="submitEditAdmin(event)" class="mt-4 space-y-4">
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Email Address</label>
        <input type="email" id="edit-a-email" readonly class="w-full bg-white/60 border border-slate-200 rounded-lg px-3 py-2 text-xs text-slate-500 cursor-not-allowed"/>
      </div>
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Full Name</label>
        <input type="text" id="edit-a-name" required placeholder="Admin Name" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"/>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs font-semibold text-slate-700 mb-1">Role</label>
          <select id="edit-a-role" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500">
            <option value="superadmin">Superadmin</option>
            <option value="admin">Admin</option>
            <option value="moderator">Moderator</option>
          </select>
        </div>
        <div>
          <label class="block text-xs font-semibold text-slate-700 mb-1">Status</label>
          <select id="edit-a-active" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500">
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </div>
      </div>
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">New Password (Optional)</label>
        <input type="password" id="edit-a-password" minlength="6" placeholder="Leave blank to keep unchanged" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"/>
        <p class="text-[10px] text-slate-500 mt-1">If provided, this will replace the current password hash with a new bcrypt hash.</p>
      </div>
      <div class="pt-2 flex justify-end gap-2">
        <button type="button" onclick="closeModal('edit-admin-modal')" class="px-4 py-2 rounded-lg border border-slate-200 hover:bg-slate-100 text-slate-700 text-xs font-medium">Cancel</button>
        <button type="submit" class="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-md">Save Changes</button>
      </div>
    </form>
  </div>
</div>

<!-- ── ADMIN LOGIN & JWT TOKEN TESTER MODAL ── -->
<div class="modal-overlay" id="admin-login-modal" onclick="closeModalOnOverlay(event, 'admin-login-modal')">
  <div class="bg-white border border-slate-200 shadow-2xl w-full max-w-lg rounded-2xl border border-slate-200 p-6 shadow-2xl relative">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-sky-500/10 flex items-center justify-center text-sky-600">
          <i data-lucide="key" class="w-4 h-4"></i>
        </div>
        <div>
          <h3 class="text-sm font-bold text-slate-900">Admin Authentication & JWT</h3>
          <p class="text-[11px] text-slate-500">Calls POST /admin/login (crypto bcrypt verified)</p>
        </div>
      </div>
      <button onclick="closeModal('admin-login-modal')" class="text-slate-500 hover:text-slate-900 p-1 rounded-lg">
        <i data-lucide="x" class="w-4 h-4"></i>
      </button>
    </div>

    <form onsubmit="submitAdminLogin(event)" class="mt-4 space-y-4">
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Email Address</label>
        <input type="email" id="login-a-email" value="admin@pas.com" required placeholder="admin@pas.com" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500"/>
      </div>
      <div>
        <label class="block text-xs font-semibold text-slate-700 mb-1">Password</label>
        <input type="password" id="login-a-password" value="admin123" required placeholder="Password" class="w-full bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500"/>
      </div>
      <div class="flex items-center justify-between pt-1">
        <span class="text-[11px] text-slate-500">Default: <code class="text-sky-300 font-mono">admin@pas.com</code> / <code class="text-sky-300 font-mono">admin123</code></span>
        <button type="submit" id="btn-admin-login-submit" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold shadow-md flex items-center gap-2">
          <i data-lucide="log-in" class="w-3.5 h-3.5"></i>
          <span>Authenticate & Get JWT</span>
        </button>
      </div>
    </form>

    <!-- Token Output Box -->
    <div id="admin-login-result" class="hidden mt-4 pt-4 border-t border-slate-200 space-y-3">
      <div class="flex items-center justify-between">
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium bg-emerald-950/60 border border-emerald-800 text-emerald-300">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
          <span>HTTP 200 — JWT Issued</span>
        </span>
        <button onclick="copyAdminToken()" class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-slate-200 hover:bg-slate-100 text-xs text-slate-700">
          <i data-lucide="copy" class="w-3.5 h-3.5"></i>
          <span id="btn-copy-token-text">Copy JWT</span>
        </button>
      </div>

      <div>
        <label class="block text-[11px] font-semibold text-slate-500 mb-1">Access Token (Bearer)</label>
        <textarea id="admin-jwt-output" readonly rows="3" class="w-full font-mono text-[11px] bg-white border border-slate-200 rounded-lg p-2.5 text-sky-300 focus:outline-none"></textarea>
      </div>

      <div class="bg-white/80 border border-slate-200 rounded-lg p-3 text-[11px] font-mono text-slate-700 space-y-1">
        <div class="text-slate-500 font-semibold mb-1">Admin Profile Payload:</div>
        <div id="admin-jwt-meta" class="text-emerald-600 whitespace-pre-wrap"></div>
      </div>
    </div>
  </div>
</div>

<div class="modal-overlay" id="detail-overlay" onclick="closeModalOnOverlay(event, 'detail-overlay')">
  <div class="bg-white border border-slate-200 rounded-2xl w-full max-w-2xl p-6 shadow-2xl overflow-y-auto max-h-[90vh]">
    <div class="flex items-center justify-between pb-4 border-b border-slate-200">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
          <i data-lucide="eye" class="w-4 h-4"></i>
        </div>
        <h3 id="modal-title" class="text-sm font-bold text-slate-900">Document Details</h3>
      </div>
      <button onclick="closeModal('detail-overlay')" class="text-slate-500 hover:text-slate-900 transition">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>
    </div>
    <div class="mt-4">
      <pre id="modal-body" class="bg-dark-950 border border-slate-200 rounded-xl p-4 text-[11px] text-indigo-600 font-mono overflow-auto max-h-[500px]"></pre>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- JAVASCRIPT LOGIC                                               -->
<!-- ══════════════════════════════════════════════════════════════ -->
<script>
// ── Store & Settings Cache & Default Config ──
let storeCache = {
  backend_url: 'http://127.0.0.1:8000',
  default_plan: 'free',
  trial_time_seconds: 1800,
  per_page: 20,
  support_phone: '+919876543210',
  currency: 'INR',
  session_timeout_minutes: 1440,
  enable_auto_otp: true
};

const titles = {
  overview: ['Overview', 'PAS Authentication & Subscriptions Dashboard', 'layout-dashboard'],
  customers: ['Customers', 'Manage customer accounts, plans & time balances', 'users'],
  recharges: ['Recharges', 'Manual payments & time adjustment transaction logs', 'zap'],
  events: ['Login Events', 'Authentication history and device telemetry', 'activity'],
  sessions: ['Login Sessions', 'Active customer tokens and session revocation', 'shield-check'],
  admins: ['Admins', 'System administrator accounts, crypto authentication & roles', 'shield'],
  store: ['Store & Settings', 'Client preferences and persistent application store', 'shopping-bag']
};

// Load pagination setting from store
function getPerPage() {
  return Number(storeCache?.per_page) || 20;
}

let PER = getPerPage();
let cSkip = 0, eSkip = 0, rSkip = 0, sSkip = 0, aSkip = 0;
let cData = [], eData = [], rData = [], sData = [], aData = [];
let cTotal = 0, eTotal = 0, rTotal = 0, sTotal = 0, aTotal = 0;
let currentAdminForEdit = null;
let currentCustomerForRec = null;
let currentCustomerForEdit = null;
let currentRecMode = 'add';
let currentExportResource = 'customers';

async function loadStoreFromApi(showToast = false) {
  const syncStatus = document.getElementById('store-sync-status');
  if (syncStatus) {
    syncStatus.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span><span>Connecting…</span>';
    syncStatus.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium bg-amber-50 border border-amber-200 text-amber-700';
  }

  try {
    const res = await fetch('/admin/store');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.data) {
      storeCache = json.data;
      PER = Number(storeCache.per_page) || 20;
      renderStoreFields(storeCache);
      if (syncStatus) {
        syncStatus.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span><span>API Live</span>';
        syncStatus.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium bg-emerald-50 border border-emerald-200 text-emerald-700';
      }
      if (showToast) alert('Store settings reloaded live from backend API!');
    }
  } catch (err) {
    console.warn('Could not load store from API, using cached values:', err);
    renderStoreFields(storeCache);
    if (syncStatus) {
      syncStatus.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-red-400"></span><span>API Offline</span>';
      syncStatus.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium bg-rose-50 border border-rose-200 text-rose-700';
    }
  }
}

function renderStoreFields(data) {
  const be = document.getElementById('store-backend-url');
  if (be) be.value = data.backend_url || 'http://127.0.0.1:8000';
  const dp = document.getElementById('store-default-plan');
  if (dp) dp.value = data.default_plan || 'free';
  const tt = document.getElementById('store-trial-time');
  if (tt) {
    tt.value = data.trial_time_seconds || 1800;
    updateTrialMinDisplay(data.trial_time_seconds || 1800);
  }
  const pp = document.getElementById('store-per-page');
  if (pp) pp.value = String(data.per_page || 20);
  const sp = document.getElementById('store-support-phone');
  if (sp) sp.value = data.support_phone || '+919876543210';
  const cur = document.getElementById('store-currency');
  if (cur) cur.value = data.currency || 'INR';
  const st = document.getElementById('store-session-timeout');
  if (st) st.value = data.session_timeout_minutes || 1440;
  const ao = document.getElementById('store-auto-otp');
  if (ao) ao.value = data.enable_auto_otp === false ? 'false' : 'true';

  const ls = document.getElementById('store-last-saved');
  if (ls) ls.textContent = data.updated_at ? data.updated_at.replace('T', ' ').split('.')[0] : 'Just now';

  const pr = document.getElementById('store-json-preview');
  if (pr) pr.textContent = JSON.stringify(data, null, 2);
}

function updateTrialMinDisplay(sec) {
  const el = document.getElementById('store-trial-minutes-display');
  if (el) {
    const s = Number(sec) || 0;
    el.textContent = `${Math.round(s / 60)}m (${s}s)`;
  }
}

async function saveStoreSettings() {
  const btn = document.getElementById('btn-save-store');
  const orig = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span>Saving to API…</span>';
  }

  const payload = {
    backend_url: document.getElementById('store-backend-url').value.trim() || 'http://127.0.0.1:8000',
    default_plan: document.getElementById('store-default-plan').value,
    trial_time_seconds: Number(document.getElementById('store-trial-time').value) || 1800,
    per_page: Number(document.getElementById('store-per-page').value) || 20,
    support_phone: document.getElementById('store-support-phone').value.trim() || '+919876543210',
    currency: document.getElementById('store-currency').value.trim().toUpperCase() || 'INR',
    session_timeout_minutes: Number(document.getElementById('store-session-timeout').value) || 1440,
    enable_auto_otp: document.getElementById('store-auto-otp').value === 'true'
  };

  try {
    const res = await fetch('/admin/store', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Save failed');
    storeCache = json.data;
    PER = Number(storeCache.per_page) || 20;
    renderStoreFields(storeCache);
    alert('Settings saved dynamically to MongoDB via PUT /admin/store!');
  } catch (err) {
    alert(`Error saving store settings: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = orig;
      lucide.createIcons();
    }
  }
}

async function resetStoreDefaults() {
  if (!confirm('Are you sure you want to reset system settings to defaults via the API?')) return;
  try {
    const res = await fetch('/admin/store/reset', { method: 'POST' });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Reset failed');
    storeCache = json.data;
    PER = Number(storeCache.per_page) || 20;
    renderStoreFields(storeCache);
    alert('System settings reset to defaults via POST /admin/store/reset!');
  } catch (err) {
    alert(`Error resetting store: ${err.message}`);
  }
}

// ── Admin Authentication & Session Management ──
function getAdminToken() {
  return localStorage.getItem('pas_admin_token');
}

function checkAdminAuth() {
  const token = getAdminToken();
  const gate = document.getElementById('admin-auth-gate');
  if (!token) {
    if (gate) {
      gate.classList.remove('hidden');
      lucide.createIcons();
    }
    return false;
  }
  if (gate) gate.classList.add('hidden');
  
  // Render topbar admin profile
  const profileStr = localStorage.getItem('pas_admin_profile');
  if (profileStr) {
    try {
      const p = JSON.parse(profileStr);
      const nameEl = document.getElementById('topbar-admin-name');
      const roleEl = document.getElementById('topbar-admin-role');
      if (nameEl) nameEl.textContent = p.name || p.email;
      if (roleEl) roleEl.textContent = p.role || 'admin';
    } catch (_) {}
  }
  return true;
}

async function performAdminGateLogin(e) {
  e.preventDefault();
  const email = document.getElementById('gate-admin-email').value.trim();
  const password = document.getElementById('gate-admin-password').value;
  const btn = document.getElementById('btn-gate-login');
  const errBox = document.getElementById('gate-login-error');

  errBox.classList.add('hidden');
  btn.disabled = true;
  btn.innerHTML = '<span class="animate-pulse">Authenticating…</span>';

  try {
    const res = await fetch('/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Authentication failed');

    localStorage.setItem('pas_admin_token', json.access_token);
    localStorage.setItem('pas_admin_profile', JSON.stringify(json.admin));
    
    document.getElementById('admin-auth-gate').classList.add('hidden');
    checkAdminAuth();
    loadOverview();
  loadSystemHealth();
    refreshCurrentPage();
  } catch (err) {
    errBox.textContent = err.message;
    errBox.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="log-in" class="w-4 h-4"></i><span>Sign In to Dashboard</span>';
    lucide.createIcons();
  }
}

async function adminLogout() {
  if (!confirm('Are you sure you want to sign out of the PAS Admin session?')) return;
  try {
    await fetch('/admin/logout', { method: 'POST' });
  } catch (_) {}
  localStorage.removeItem('pas_admin_token');
  localStorage.removeItem('pas_admin_profile');
  sessionStorage.clear();
  
  const gate = document.getElementById('admin-auth-gate');
  if (gate) {
    gate.classList.remove('hidden');
    lucide.createIcons();
  }
}

// ── Export Modal & Execution ──
function openExportModal(resource) {
  currentExportResource = resource;
  document.getElementById('export-resource-display').value = resource.toUpperCase();
  openModal('export-modal');
}

function promptExportCurrentPage() {
  const activeNav = document.querySelector('.nav-link.active');
  let res = 'customers';
  if (activeNav) {
    if (activeNav.textContent.includes('Admins')) res = 'admins';
    else if (activeNav.textContent.includes('Recharges')) res = 'recharges';
    else if (activeNav.textContent.includes('Login Events')) res = 'events';
    else if (activeNav.textContent.includes('Sessions')) res = 'customers';
  }
  openExportModal(res);
}

function executeExport() {
  const formatEl = document.querySelector('input[name="export-format"]:checked');
  const fmt = formatEl ? formatEl.value : 'xlsx';
  let url = `/admin/export?resource=${encodeURIComponent(currentExportResource)}&format=${encodeURIComponent(fmt)}`;

  if (currentExportResource === 'customers') {
    const s = document.getElementById('c-search')?.value;
    const p = document.getElementById('c-filter-plan')?.value;
    const src = document.getElementById('c-filter-source')?.value;
    if (s) url += `&search=${encodeURIComponent(s)}`;
    if (p) url += `&payment_type=${encodeURIComponent(p)}`;
    if (src) url += `&source=${encodeURIComponent(src)}`;
  } else if (currentExportResource === 'recharges') {
    const s = document.getElementById('r-search')?.value;
    const st = document.getElementById('r-filter-status')?.value;
    const src = document.getElementById('r-filter-source')?.value;
    if (s) url += `&search=${encodeURIComponent(s)}`;
    if (st) url += `&payment_status=${encodeURIComponent(st)}`;
    if (src) url += `&source=${encodeURIComponent(src)}`;
  } else if (currentExportResource === 'events') {
    const s = document.getElementById('e-search')?.value;
    const d = document.getElementById('e-filter-device')?.value;
    if (s) url += `&search=${encodeURIComponent(s)}`;
    if (d) url += `&device_type=${encodeURIComponent(d)}`;
  } else if (currentExportResource === 'admins') {
    const s = document.getElementById('a-search')?.value;
    const r = document.getElementById('a-filter-role')?.value;
    const st = document.getElementById('a-filter-status')?.value;
    if (s) url += `&search=${encodeURIComponent(s)}`;
    if (r) url += `&role=${encodeURIComponent(r)}`;
    if (st) url += `&is_active=${encodeURIComponent(st)}`;
  }

  closeModal('export-modal');
  window.open(url, '_blank');
}

function formatSeconds(s) {
  if (s === null || s === undefined) return '0s';
  const sec = Number(s);
  if (isNaN(sec) || sec <= 0) return '0s';
  const m = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const remMin = m % 60;
    return `${sec}s (${h}h ${remMin}m)`;
  }
  return `${sec}s (${m}m ${remSec}s)`;
}

function showPage(id, btn) {
  document.querySelectorAll('.page-view').forEach(p => p.classList.add('hidden'));
  document.querySelectorAll('.nav-link').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + id).classList.remove('hidden');
  if (btn) btn.classList.add('active');

  const conf = titles[id] || ['PAS Admin', 'Dashboard', 'layout-dashboard'];
  document.getElementById('page-title').textContent = conf[0];
  document.getElementById('page-sub').textContent = conf[1];
  
  const iconEl = document.getElementById('page-icon');
  if (iconEl) {
    iconEl.setAttribute('data-lucide', conf[2]);
  }

  if (id === 'customers' && !cData.length) loadCustomers();
  if (id === 'recharges' && !rData.length) loadRecharges();
  if (id === 'events' && !eData.length) loadEvents();
  if (id === 'sessions' && !sData.length) loadSessions();
  if (id === 'admins' && !aData.length) loadAdmins();
  if (id === 'store') loadStoreFromApi();

  lucide.createIcons();
}

function refreshCurrentPage() {
  loadOverview();
  loadSystemHealth();
  const activeNav = document.querySelector('.nav-link.active');
  if (activeNav) {
    if (activeNav.textContent.includes('Customers')) loadCustomers();
    else if (activeNav.textContent.includes('Recharges')) loadRecharges();
    else if (activeNav.textContent.includes('Login Events')) loadEvents();
    else if (activeNav.textContent.includes('Sessions')) loadSessions();
    else if (activeNav.textContent.includes('Admins')) loadAdmins();
    else if (activeNav.textContent.includes('Store')) loadStoreFromApi();
  }
}

// ── Modals Helper ──
function openModal(id) {
  document.getElementById(id).classList.add('open');
  lucide.createIcons();
}
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}
function closeModalOnOverlay(e, id) {
  if (e.target.id === id) closeModal(id);
}

function generateRandomOtp(inputId) {
  const code = Math.floor(100000 + Math.random() * 900000).toString();
  document.getElementById(inputId).value = code;
}

// ── Overview ──
async function loadOverview() {
  const [cr, er, rr, ar] = await Promise.all([
    fetch('/admin/customers?limit=1').then(r => r.json()).catch(() => ({ total: 0 })),
    fetch('/admin/login-events?limit=1').then(r => r.json()).catch(() => ({ total: 0 })),
    fetch('/admin/recharges?limit=200').then(r => r.json()).catch(() => ({ total: 0, data: [] })),
    fetch('/admin/admins?limit=1').then(r => r.json()).catch(() => ({ total: 0 }))
  ]);
  const elA = document.getElementById('ov-a2');
  if (elA) elA.textContent = (ar.total ?? 0) + ' records';
  document.getElementById('ov-customers').textContent = cr.total ?? 0;
  document.getElementById('ov-events').textContent = er.total ?? 0;
  document.getElementById('ov-recharges').textContent = rr.total ?? 0;
  
  const totalAmount = (rr.data || []).reduce((sum, item) => sum + (Number(item.amount) || 0), 0);
  document.getElementById('ov-revenue').textContent = '₹' + totalAmount.toFixed(2);

  document.getElementById('ov-c2').textContent = (cr.total ?? 0) + ' records';
  document.getElementById('ov-e2').textContent = (er.total ?? 0) + ' records';
  document.getElementById('ov-r2').textContent = (rr.total ?? 0) + ' records';

  const all = await fetch('/admin/customers?limit=200').then(r => r.json()).catch(() => ({ data: [] }));
  const free = (all.data || []).filter(c => c.payment_type === 'free').length;
  const paid = (all.data || []).filter(c => c.payment_type === 'paid').length;
  document.getElementById('ov-free').textContent = free;
  document.getElementById('ov-paid').textContent = paid;
  
  lucide.createIcons();
}

// ── Customers ──
async function loadCustomers() {
  document.getElementById('c-tbody').innerHTML = '<tr><td colspan="9" class="py-8 text-center text-slate-700 font-medium">Loading customers…</td></tr>';
  const search = document.getElementById('c-search')?.value.trim() || '';
  const plan = document.getElementById('c-filter-plan')?.value || '';
  const source = document.getElementById('c-filter-source')?.value || '';

  let query = `/admin/customers?skip=${cSkip}&limit=${PER}`;
  if (search) query += `&search=${encodeURIComponent(search)}`;
  if (plan) query += `&payment_type=${encodeURIComponent(plan)}`;
  if (source) query += `&source=${encodeURIComponent(source)}`;

  const d = await fetch(query).then(r => r.json()).catch(() => ({ data: [], total: 0 }));
  cData = d.data || [];
  cTotal = d.total || 0;
  renderCustomers(cData);
  document.getElementById('c-page-info').textContent = `Showing ${cData.length ? cSkip + 1 : 0}–${Math.min(cSkip + PER, cTotal)} of ${cTotal} customers`;
  document.getElementById('c-prev').disabled = cSkip === 0;
  document.getElementById('c-next').disabled = cSkip + PER >= cTotal;
}

function renderCustomers(rows) {
  const tb = document.getElementById('c-tbody');
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="10" class="py-8 text-center text-slate-700 font-medium">No customer records found</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(c => {
    const isPaid = c.payment_type === 'paid';
    const planClass = isPaid ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-sky-50 text-sky-700 border-sky-200';
    const hasActiveSession = Boolean(c.login_session_id);
    const sessionBadge = hasActiveSession
      ? '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950/70 border border-emerald-800 text-emerald-300"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>1 Active Dev</span>'
      : '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-100 border border-slate-200 text-slate-600">0 Active</span>';

    return `<tr class="hover:bg-slate-50 transition">
      <td class="py-3 px-4 font-mono font-bold text-slate-900">${c.phone_number}</td>
      <td class="py-3 px-4">${sessionBadge}</td>
      <td class="py-3 px-4 font-mono font-medium text-amber-700">
        <span class="mr-1">${c.otp || '—'}</span>
        <button onclick="refreshOtp('${c.phone_number}', this)" title="Refresh OTP" class="text-indigo-400 hover:text-indigo-300 font-bold ml-1">↻</button>
      </td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-700 border border-slate-200">${c.source}</span></td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] font-semibold border ${planClass}">${c.payment_type}</span></td>
      <td class="py-3 px-4 font-mono text-slate-700">${c.referral_code_generated || '—'}</td>
      <td class="py-3 px-4 text-slate-700 font-medium">${formatSeconds(c.time_remaining_seconds)}</td>
      <td class="py-3 px-4 text-slate-500 text-[11px]">${c.time_expiry ? c.time_expiry.replace('T', ' ').split('.')[0] : '—'}</td>
      <td class="py-3 px-4 text-slate-500 text-[11px]">${c.last_login ? c.last_login.replace('T', ' ').split('.')[0] : '—'}</td>
      <td class="py-3 px-4 text-right">
        <div class="flex items-center justify-end gap-1.5 flex-wrap">
          <!-- Minutes History Tracker Button -->
          <button onclick="openUsageHistoryModal('${c.phone_number}')" title="Track minutes consumed & session history" class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-cyan-50 hover:bg-cyan-100 border border-cyan-200 text-cyan-700 text-xs font-semibold transition">
            <i data-lucide="history" class="w-3.5 h-3.5 text-cyan-600"></i>
            <span>Minutes History</span>
          </button>

          <!-- Force Logout Button -->
          <button onclick="forceLogoutCustomer('${c.phone_number}', this)" title="Force logout all active sessions on all devices" class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-rose-50 hover:bg-rose-100 border border-rose-200 text-rose-700 text-xs font-semibold transition">
            <i data-lucide="log-out" class="w-3.5 h-3.5 text-rose-600"></i>
            <span>Force Logout</span>
          </button>

          <button onclick="openAttemptsModal('${c.phone_number}')" title="View Login Attempts" class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-amber-50 hover:bg-amber-100 border border-amber-200 text-amber-700 text-xs font-semibold transition">
            <i data-lucide="key-round" class="w-3.5 h-3.5 text-amber-400"></i>
            <span>Attempts</span>
          </button>
          <button onclick='openRechargeModal(${JSON.stringify(c)})' title="Recharge Customer Time & Plan" class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 text-emerald-700 text-xs font-semibold transition">
            <i data-lucide="zap" class="w-3.5 h-3.5 text-emerald-600"></i>
            <span>Recharge</span>
          </button>
          <button onclick='openEditCustomerModal(${JSON.stringify(c)})' title="Edit Customer Details" class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 text-indigo-700 text-xs font-semibold transition">
            <i data-lucide="edit-3" class="w-3.5 h-3.5 text-indigo-400"></i>
            <span>Edit</span>
          </button>
          <button onclick='showDetail(${JSON.stringify(c)})' title="View JSON" class="inline-flex items-center gap-1 px-2 py-1 rounded bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 text-xs font-medium transition">
            <i data-lucide="eye" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </td>
    </tr>`;
  }).join('');
  lucide.createIcons();
}

function customerPage(d) {
  cSkip = Math.max(0, cSkip + d * PER);
  loadCustomers();
}

// ── Add Customer ──
function openAddCustomerModal() {
  const store = storeCache;
  document.getElementById('add-phone').value = '';
  document.getElementById('add-otp').value = '';
  document.getElementById('add-plan').value = store.defaultPlan || 'free';
  document.getElementById('add-source').value = 'admin';
  document.getElementById('add-time-min').value = Math.round((store.trialTimeSeconds || 1800) / 60);
  document.getElementById('add-referral').value = '';
  document.getElementById('add-device').value = '';
  openModal('add-customer-modal');
}

async function submitAddCustomer() {
  const btn = document.getElementById('btn-submit-add-customer');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span>Creating…</span>';

  const phone = document.getElementById('add-phone').value.trim();
  const otp = document.getElementById('add-otp').value.trim() || null;
  const plan = document.getElementById('add-plan').value;
  const source = document.getElementById('add-source').value;
  const timeMin = Number(document.getElementById('add-time-min').value) || 30;
  const referral = document.getElementById('add-referral').value.trim() || null;
  const device = document.getElementById('add-device').value.trim() || null;

  const payload = {
    phone_number: phone,
    otp: otp,
    payment_type: plan,
    source: source,
    time_remaining_seconds: Math.round(timeMin * 60),
    referral_code_generated: referral,
    device_id: device
  };

  try {
    const res = await fetch('/admin/customers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Error: ${data.detail || 'Could not create customer'}`);
      return;
    }
    closeModal('add-customer-modal');
    await loadCustomers();
    await loadOverview();
  loadSystemHealth();
  } catch (err) {
    alert(`Request error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
    lucide.createIcons();
  }
}

// ── Edit Customer ──
function openEditCustomerModal(customer) {
  currentCustomerForEdit = customer;
  document.getElementById('edit-phone-display').textContent = customer.phone_number;
  document.getElementById('edit-otp').value = customer.otp || '';
  document.getElementById('edit-plan').value = customer.payment_type || 'free';
  document.getElementById('edit-source').value = customer.source || 'admin';
  document.getElementById('edit-time-sec').value = customer.time_remaining_seconds || 0;
  document.getElementById('edit-referral').value = customer.referral_code_generated || '';
  document.getElementById('edit-device').value = customer.device_id || '';
  openModal('edit-customer-modal');
}

async function submitEditCustomer() {
  if (!currentCustomerForEdit) return;
  const btn = document.getElementById('btn-submit-edit-customer');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span>Saving…</span>';

  const payload = {
    otp: document.getElementById('edit-otp').value.trim() || null,
    payment_type: document.getElementById('edit-plan').value,
    source: document.getElementById('edit-source').value,
    time_remaining_seconds: Number(document.getElementById('edit-time-sec').value) || 0,
    referral_code_generated: document.getElementById('edit-referral').value.trim() || null,
    device_id: document.getElementById('edit-device').value.trim() || null
  };

  try {
    const res = await fetch(`/admin/customers/${encodeURIComponent(currentCustomerForEdit.phone_number)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Error: ${data.detail || 'Failed to update customer'}`);
      return;
    }
    closeModal('edit-customer-modal');
    await loadCustomers();
    await loadOverview();
  loadSystemHealth();
  } catch (err) {
    alert(`Request error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
    lucide.createIcons();
  }
}

// ── Attempts Modal ──
async function openAttemptsModal(phone) {
  document.getElementById('att-title').textContent = `Login Attempts`;
  document.getElementById('att-subtitle').textContent = `Phone: ${phone}`;
  document.getElementById('att-tbody').innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-700 font-medium">Loading attempts…</td></tr>';
  openModal('attempts-overlay');

  try {
    const res = await fetch(`/admin/customers/${encodeURIComponent(phone)}/login-events`).then(r => r.json());
    const events = res.data || [];
    renderAttempts(events);
  } catch (err) {
    document.getElementById('att-tbody').innerHTML = `<tr><td colspan="7" class="py-8 text-center text-rose-600 font-medium">Failed: ${err.message}</td></tr>`;
  }
}

function renderAttempts(events) {
  const tb = document.getElementById('att-tbody');
  if (!events.length) {
    tb.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-700 font-medium">No login attempts recorded yet for this customer.</td></tr>';
    return;
  }
  tb.innerHTML = events.map(e => {
    const geo = e.geolocation || {};
    const loc = [geo.city, geo.country].filter(Boolean).join(', ') || '—';
    const typeBadge = e.is_bot ? '<span class="px-2 py-0.5 rounded text-[10px] bg-rose-50 text-rose-700 border border-rose-200">bot</span>' : e.is_mobile ? '<span class="px-2 py-0.5 rounded text-[10px] bg-amber-50 text-amber-700 border border-amber-200">mobile</span>' : '<span class="px-2 py-0.5 rounded text-[10px] bg-blue-50 text-blue-700 border border-blue-200">pc</span>';
    return `<tr class="hover:bg-slate-50 transition">
      <td class="py-3 px-3.5 text-slate-700 font-mono text-[11px]">${e.timestamp ? e.timestamp.replace('T', ' ').split('.')[0] : '—'}</td>
      <td class="py-3 px-3.5 font-mono text-indigo-600">${e.ip_address || '—'}</td>
      <td class="py-3 px-3.5 text-slate-700">${loc}</td>
      <td class="py-3 px-3.5 text-slate-700">${e.browser?.family || '—'} on ${e.os?.family || '—'}</td>
      <td class="py-3 px-3.5 text-slate-500">${(e.device?.brand || '') + ' ' + (e.device?.model || '') || '—'}</td>
      <td class="py-3 px-3.5">${typeBadge}</td>
      <td class="py-3 px-3.5 text-right">
        <button onclick='showDetail(${JSON.stringify(e)})' class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 text-xs transition">
          <i data-lucide="eye" class="w-3.5 h-3.5"></i>
          <span>View</span>
        </button>
      </td>
    </tr>`;
  }).join('');
  lucide.createIcons();
}

// ── Recharge Modal ──
function openRechargeModal(customer) {
  currentCustomerForRec = customer;
  document.getElementById('rec-phone-sub').textContent = customer.phone_number;
  document.getElementById('rec-curr-plan').textContent = customer.payment_type;
  document.getElementById('rec-curr-time').textContent = formatSeconds(customer.time_remaining_seconds);
  document.getElementById('rec-plan').value = 'paid';

  currentRecMode = 'add';
  document.getElementById('btn-mode-add').className = 'py-1.5 rounded-md text-xs font-semibold bg-indigo-600 text-white transition';
  document.getElementById('btn-mode-sub').className = 'py-1.5 rounded-md text-xs font-semibold text-slate-500 hover:text-slate-900 transition';

  document.getElementById('rec-time-val').value = 30;
  document.getElementById('rec-time-unit').value = 'min';
  document.getElementById('rec-amount').value = '0.00';
  document.getElementById('rec-source').value = 'manual';
  document.getElementById('rec-status').value = 'completed';
  document.getElementById('rec-notes').value = '';

  updateRecPreview();
  openModal('recharge-overlay');
}

function setRecMode(mode) {
  currentRecMode = mode;
  if (mode === 'add') {
    document.getElementById('btn-mode-add').className = 'py-1.5 rounded-md text-xs font-semibold bg-indigo-600 text-white transition';
    document.getElementById('btn-mode-sub').className = 'py-1.5 rounded-md text-xs font-semibold text-slate-500 hover:text-slate-900 transition';
  } else {
    document.getElementById('btn-mode-sub').className = 'py-1.5 rounded-md text-xs font-semibold bg-red-600 text-white transition';
    document.getElementById('btn-mode-add').className = 'py-1.5 rounded-md text-xs font-semibold text-slate-500 hover:text-slate-900 transition';
  }
  updateRecPreview();
}

function applyQuickTime(val, unit) {
  if (val < 0) {
    setRecMode('sub');
    document.getElementById('rec-time-val').value = Math.abs(val);
  } else {
    setRecMode('add');
    document.getElementById('rec-time-val').value = val;
  }
  document.getElementById('rec-time-unit').value = unit;
  updateRecPreview();
}

function calculateDeltaSeconds() {
  const val = Number(document.getElementById('rec-time-val').value) || 0;
  const unit = document.getElementById('rec-time-unit').value;
  let multiplier = 60;
  if (unit === 'sec') multiplier = 1;
  else if (unit === 'hour') multiplier = 3600;
  const rawSeconds = Math.round(val * multiplier);
  return currentRecMode === 'sub' ? -rawSeconds : rawSeconds;
}

function updateRecPreview() {
  if (!currentCustomerForRec) return;
  const delta = calculateDeltaSeconds();
  const current = Number(currentCustomerForRec.time_remaining_seconds) || 0;
  const newBalance = Math.max(0, current + delta);
  const sign = delta >= 0 ? '+' : '';
  const deltaColor = delta >= 0 ? 'text-emerald-600' : 'text-rose-600';
  const targetPlan = document.getElementById('rec-plan').value;

  document.getElementById('rec-preview').innerHTML = `Adjustment: <strong class="${deltaColor}">${sign}${delta}s (${sign}${Math.round(delta / 60)}m)</strong> &nbsp;➔&nbsp; ` +
    `New Balance: <strong class="text-slate-900">${formatSeconds(newBalance)}</strong> &nbsp;|&nbsp; Target Plan: <strong class="text-indigo-600">${targetPlan}</strong>`;
}

async function submitRecharge() {
  if (!currentCustomerForRec) return;
  const btn = document.getElementById('rec-submit-btn');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span>Applying…</span>';

  const delta = calculateDeltaSeconds();
  const payload = {
    time_delta_seconds: delta,
    amount: parseFloat(document.getElementById('rec-amount').value) || 0.0,
    payment_type: document.getElementById('rec-plan').value,
    source: document.getElementById('rec-source').value || 'manual',
    payment_status: document.getElementById('rec-status').value || 'completed',
    notes: document.getElementById('rec-notes').value || '',
    created_by: 'admin'
  };

  try {
    const res = await fetch(`/admin/customers/${encodeURIComponent(currentCustomerForRec.phone_number)}/recharge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Error: ${data.detail || 'Recharge failed'}`);
      return;
    }
    closeModal('recharge-overlay');
    await loadCustomers();
    await loadOverview();
  loadSystemHealth();
    if (rData.length) loadRecharges();
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
    lucide.createIcons();
  }
}

// ── Recharges Page ──
async function loadRecharges() {
  document.getElementById('r-tbody').innerHTML = '<tr><td colspan="9" class="py-8 text-center text-slate-700 font-medium">Loading recharges…</td></tr>';
  const phone = document.getElementById('r-search')?.value.trim() || '';
  const status = document.getElementById('r-filter-status')?.value || '';
  const source = document.getElementById('r-filter-source')?.value || '';

  let query = `/admin/recharges?skip=${rSkip}&limit=${PER}`;
  if (phone) query += `&search=${encodeURIComponent(phone)}`;
  if (status) query += `&payment_status=${encodeURIComponent(status)}`;
  if (source) query += `&source=${encodeURIComponent(source)}`;

  const d = await fetch(query).then(r => r.json()).catch(() => ({ data: [], total: 0 }));
  rData = d.data || [];
  rTotal = d.total || 0;
  renderRecharges(rData);

  document.getElementById('r-page-info').textContent = `Showing ${rData.length ? rSkip + 1 : 0}–${Math.min(rSkip + PER, rTotal)} of ${rTotal} recharges`;
  document.getElementById('r-prev').disabled = rSkip === 0;
  document.getElementById('r-next').disabled = rSkip + PER >= rTotal;
}

function renderRecharges(rows) {
  const tb = document.getElementById('r-tbody');
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="9" class="py-8 text-center text-slate-700 font-medium">No recharge transactions found</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(r => {
    const delta = Number(r.time_delta_seconds) || 0;
    const deltaSign = delta >= 0 ? '+' : '';
    const deltaColor = delta >= 0 ? 'text-emerald-600' : 'text-rose-600';
    return `<tr class="hover:bg-slate-50 transition">
      <td class="py-3 px-4 font-mono font-bold text-slate-900">${r.phone_number}</td>
      <td class="py-3 px-4 font-semibold text-slate-800">₹${(Number(r.amount) || 0).toFixed(2)}</td>
      <td class="py-3 px-4 font-semibold ${deltaColor}">${deltaSign}${delta}s (${deltaSign}${Math.round(delta / 60)}m)</td>
      <td class="py-3 px-4">
        <span class="px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200">${r.previous_payment_type}</span>
        <span class="text-slate-500 mx-1">➔</span>
        <span class="px-2 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-600 border border-emerald-500/20 font-semibold">${r.new_payment_type}</span>
      </td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] bg-indigo-100 text-indigo-700 border border-indigo-200">${r.source || 'manual'}</span></td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-300 border border-emerald-800">${r.payment_status || 'completed'}</span></td>
      <td class="py-3 px-4 text-slate-500 text-[11px]">${r.created_at ? r.created_at.replace('T', ' ').split('.')[0] : '—'}</td>
      <td class="py-3 px-4 text-slate-500 text-[11px] truncate max-w-xs">${r.notes || '—'}</td>
      <td class="py-3 px-4 text-right">
        <button onclick='showDetail(${JSON.stringify(r)})' class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 text-xs transition">
          <i data-lucide="eye" class="w-3.5 h-3.5"></i>
          <span>View</span>
        </button>
      </td>
    </tr>`;
  }).join('');
  lucide.createIcons();
}

function rechargePage(d) {
  rSkip = Math.max(0, rSkip + d * PER);
  loadRecharges();
}

// ── Events ──
async function loadEvents() {
  document.getElementById('e-tbody').innerHTML = '<tr><td colspan="8" class="py-8 text-center text-slate-700 font-medium">Loading events…</td></tr>';
  const phone = document.getElementById('e-search')?.value.trim() || '';
  const deviceType = document.getElementById('e-filter-device')?.value || '';

  let query = `/admin/login-events?skip=${eSkip}&limit=${PER}`;
  if (phone) query += `&search=${encodeURIComponent(phone)}`;
  if (deviceType) query += `&device_type=${encodeURIComponent(deviceType)}`;

  const d = await fetch(query).then(r => r.json()).catch(() => ({ data: [], total: 0 }));
  eData = d.data || [];
  eTotal = d.total || 0;
  renderEvents(eData);

  document.getElementById('e-page-info').textContent = `Showing ${eData.length ? eSkip + 1 : 0}–${Math.min(eSkip + PER, eTotal)} of ${eTotal} events`;
  document.getElementById('e-prev').disabled = eSkip === 0;
  document.getElementById('e-next').disabled = eSkip + PER >= eTotal;
}

function renderEvents(rows) {
  const tb = document.getElementById('e-tbody');
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="8" class="py-8 text-center text-slate-700 font-medium">No login events recorded</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(e => {
    const geo = e.geolocation || {};
    const loc = [geo.city, geo.country].filter(Boolean).join(', ') || '—';
    const typeBadge = e.is_bot ? '<span class="px-2 py-0.5 rounded text-[10px] bg-rose-50 text-rose-700 border border-rose-200">bot</span>' : e.is_mobile ? '<span class="px-2 py-0.5 rounded text-[10px] bg-amber-50 text-amber-700 border border-amber-200">mobile</span>' : '<span class="px-2 py-0.5 rounded text-[10px] bg-blue-50 text-blue-700 border border-blue-200">pc</span>';
    return `<tr class="hover:bg-slate-50 transition">
      <td class="py-3 px-4 font-mono font-bold text-slate-900">${e.phone_number}</td>
      <td class="py-3 px-4 font-mono text-indigo-600">${e.ip_address || '—'}</td>
      <td class="py-3 px-4 text-slate-700">${loc}</td>
      <td class="py-3 px-4 text-slate-700">${e.browser?.family || '—'} on ${e.os?.family || '—'}</td>
      <td class="py-3 px-4 text-slate-500">${(e.device?.brand || '') + ' ' + (e.device?.model || '') || '—'}</td>
      <td class="py-3 px-4">${typeBadge}</td>
      <td class="py-3 px-4 text-slate-500 text-[11px] font-mono">${e.timestamp ? e.timestamp.replace('T', ' ').split('.')[0] : '—'}</td>
      <td class="py-3 px-4 text-right">
        <button onclick='showDetail(${JSON.stringify(e)})' class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 text-xs transition">
          <i data-lucide="eye" class="w-3.5 h-3.5"></i>
          <span>View</span>
        </button>
      </td>
    </tr>`;
  }).join('');
  lucide.createIcons();
}

function eventPage(d) {
  eSkip = Math.max(0, eSkip + d * PER);
  loadEvents();
}

// ── Sessions Page (Customer Sessions & Invalidation) ──
async function loadSessions() {
  document.getElementById('s-tbody').innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-700 font-medium">Loading sessions…</td></tr>';
  const q = document.getElementById('s-search')?.value.trim() || '';
  const status = document.getElementById('s-filter-status')?.value || 'all';
  let query = `/admin/sessions?skip=${sSkip}&limit=${PER}&status=${encodeURIComponent(status)}`;
  if (q) query += `&search=${encodeURIComponent(q)}`;

  const d = await fetch(query).then(r => r.json()).catch(() => ({ data: [], total: 0 }));
  sData = d.data || [];
  sTotal = d.total || 0;
  renderSessions(sData);

  document.getElementById('s-page-info').textContent = `Showing ${sData.length ? sSkip + 1 : 0}–${Math.min(sSkip + PER, sTotal)} of ${sTotal} customer sessions`;
  document.getElementById('s-prev').disabled = sSkip === 0;
  document.getElementById('s-next').disabled = sSkip + PER >= sTotal;
}

function renderSessions(rows) {
  const tb = document.getElementById('s-tbody');
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-700 font-medium">No customer login sessions found</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(s => {
    const isActive = Boolean(s.login_session_id);
    const statusBadge = isActive
      ? '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>Active</span>'
      : '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200 font-medium">Terminated</span>';

    const sessionCell = isActive
      ? `<span class="font-mono text-purple-700 text-[11px] truncate max-w-xs block">${s.login_session_id}</span>`
      : `<span class="font-mono text-slate-500 text-[11px] italic">Logged out / None</span>`;

    const actionBtn = isActive
      ? `<button onclick="terminateSession('${s.phone_number}', this)" class="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 text-amber-700 text-xs font-semibold transition" title="Log out customer from active session">
           <i data-lucide="log-out" class="w-3.5 h-3.5"></i>
           <span>Logout Session</span>
         </button>`
      : `<button onclick="activateSession('${s.phone_number}', this)" class="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-indigo-500/10 hover:bg-indigo-500/20 border border-indigo-500/30 text-indigo-300 text-xs font-semibold transition" title="Generate fresh active session">
           <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
           <span>Activate Session</span>
         </button>`;

    return `<tr class="hover:bg-slate-50 transition">
      <td class="py-3 px-4 font-mono font-bold text-slate-900">${s.phone_number}</td>
      <td class="py-3 px-4">${sessionCell}</td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-indigo-100 text-indigo-700 border border-indigo-200">${s.payment_type || 'free'}</span></td>
      <td class="py-3 px-4 text-slate-700 text-xs">${s.device_id || '—'}</td>
      <td class="py-3 px-4 text-slate-500 text-[11px] font-mono">${s.last_login ? s.last_login.replace('T', ' ').split('.')[0] : '—'}</td>
      <td class="py-3 px-4">${statusBadge}</td>
      <td class="py-3 px-4 text-right">${actionBtn}</td>
    </tr>`;
  }).join('');
  lucide.createIcons();
}

async function terminateSession(phone, btn) {
  if (!confirm(`Are you sure you want to log out customer ${phone}? Their active session will be terminated.`)) return;
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span>Logging out…</span>';
  try {
    const res = await fetch(`/admin/sessions/${encodeURIComponent(phone)}/terminate`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(`Error: ${data.detail || 'Failed to terminate session'}`);
      return;
    }
    await loadSessions();
  } catch (err) {
    alert(`Request error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
    lucide.createIcons();
  }
}

async function activateSession(phone, btn) {
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span>Activating…</span>';
  try {
    const res = await fetch(`/admin/sessions/${encodeURIComponent(phone)}/activate`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(`Error: ${data.detail || 'Failed to activate session'}`);
      return;
    }
    await loadSessions();
  } catch (err) {
    alert(`Request error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
    lucide.createIcons();
  }
}

function sessionPage(d) {
  sSkip = Math.max(0, sSkip + d * PER);
  loadSessions();
}

// ── View Details ──
function showDetail(obj) {
  document.getElementById('modal-title').textContent = obj.phone_number || obj._id || 'Document Details';
  document.getElementById('modal-body').textContent = JSON.stringify(obj, null, 2);
  openModal('detail-overlay');
}

async function refreshOtp(phone, btn) {
  const orig = btn.textContent;
  btn.textContent = '…';
  btn.disabled = true;
  try {
    const r = await fetch(`/admin/customers/${encodeURIComponent(phone)}/refresh-otp`, { method: 'POST' });
    const d = await r.json();
    if (d.otp) {
      const cell = btn.parentElement;
      cell.childNodes[0].textContent = d.otp;
    }
  } catch (_) {}
  btn.disabled = false;
  btn.textContent = orig;
}


// ── Admin Management & Token Authentication JS ──

// ── Customer Minutes Consumption & Force Logout Functions ──
async function openUsageHistoryModal(phone) {
  document.getElementById('usage-modal-phone').textContent = phone;
  document.getElementById('usage-stat-balance').textContent = 'Loading…';
  document.getElementById('usage-stat-consumed').textContent = 'Loading…';
  document.getElementById('usage-stat-sessions').textContent = 'Loading…';
  document.getElementById('usage-stat-device').textContent = 'Loading…';
  
  const tbody = document.getElementById('usage-history-tbody');
  tbody.innerHTML = '<tr><td colspan="5" class="py-6 text-center text-slate-500">Fetching usage events…</td></tr>';
  
  openModal('customer-usage-modal');

  try {
    const res = await fetch(`/admin/customers/${encodeURIComponent(phone)}/usage-history`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to load usage history');

    document.getElementById('usage-stat-balance').textContent = `${data.current_time_remaining_minutes}m (${data.current_time_remaining_seconds}s)`;
    document.getElementById('usage-stat-consumed').textContent = `${data.total_consumed_minutes} mins`;
    document.getElementById('usage-stat-sessions').textContent = `${data.total_sessions_count} logged`;
    document.getElementById('usage-stat-device').textContent = data.has_active_session ? (data.device_id || 'Active (1 device)') : 'Logged Out';

    const list = data.usage_history || [];
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="py-6 text-center text-slate-500 font-medium">No minute consumption events recorded yet.</td></tr>';
      return;
    }

    const actionColors = {
      login_consumption: 'bg-amber-950/70 text-amber-700 border-amber-800',
      initial_login_consumption: 'bg-sky-950/70 text-sky-300 border-sky-800',
      device_switch_force_logout: 'bg-purple-950/70 text-purple-700 border-purple-800',
      admin_force_logout: 'bg-red-950/70 text-red-300 border-red-800'
    };

    tbody.innerHTML = list.map(h => {
      const badge = actionColors[h.action] || 'bg-slate-100 text-slate-700 border-slate-200';
      const consumedDisplay = h.minutes_consumed > 0
        ? `<span class="text-rose-400 font-bold">-${h.minutes_consumed}m (${h.seconds_consumed}s)</span>`
        : `<span class="text-slate-500">0m</span>`;

      const prevMin = roundMinutes(h.previous_time_remaining_seconds);
      const newMin = roundMinutes(h.new_time_remaining_seconds);
      const balanceDisplay = `<span class="text-slate-700">${prevMin}m</span> → <span class="text-emerald-600 font-bold">${newMin}m</span>`;

      return `
        <tr class="hover:bg-slate-50 transition">
          <td class="py-2.5 px-3 text-slate-700">${h.created_at ? new Date(h.created_at).toLocaleString() : '—'}</td>
          <td class="py-2.5 px-3">
            <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold border ${badge}">
              ${escapeHtml(h.action)}
            </span>
            <div class="text-[10px] text-slate-500 mt-0.5">${escapeHtml(h.description || '')}</div>
          </td>
          <td class="py-2.5 px-3 font-semibold">${consumedDisplay}</td>
          <td class="py-2.5 px-3">${balanceDisplay}</td>
          <td class="py-2.5 px-3 text-slate-500">
            <div>${escapeHtml(h.device_id || '—')}</div>
            <div class="text-[10px] text-slate-700 truncate max-w-xs">${escapeHtml(h.session_id ? h.session_id.slice(0, 12) + '…' : '—')}</div>
          </td>
        </tr>
      `;
    }).join('');

    lucide.createIcons();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-rose-600">Error: ${err.message}</td></tr>`;
  }
}

function roundMinutes(sec) {
  if (sec === null || sec === undefined) return '0';
  return (sec / 60).toFixed(1);
}

async function forceLogoutCustomer(phone, btn) {
  if (!confirm(`Are you sure you want to FORCE LOGOUT customer ${phone}? All active sessions on all devices will be immediately terminated.`)) return;
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span>Logging out…</span>';

  try {
    const res = await fetch(`/admin/customers/${encodeURIComponent(phone)}/force-logout`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to force logout customer');
    await loadCustomers();
    await loadSessions();
    alert(`Customer ${phone} has been force logged out. Active sessions invalidated.`);
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
    lucide.createIcons();
  }
}


async function loadAdmins() {
  const tbody = document.getElementById('a-tbody');
  tbody.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-500 font-medium"><span class="animate-pulse">Loading admins…</span></td></tr>';

  const s = document.getElementById('a-search')?.value || '';
  const r = document.getElementById('a-filter-role')?.value || '';
  const st = document.getElementById('a-filter-status')?.value || '';

  let url = `/admin/admins?skip=${aSkip}&limit=${PER}`;
  if (s) url += `&search=${encodeURIComponent(s)}`;
  if (r) url += `&role=${encodeURIComponent(r)}`;
  if (st) url += `&is_active=${encodeURIComponent(st)}`;

  try {
    const res = await fetch(url);
    const json = await res.json();
    aData = json.data || [];
    aTotal = json.total || 0;
    renderAdmins(aData);
  } catch (err) {
    console.error('Failed to load admins:', err);
    tbody.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-rose-600">Failed to load admin accounts.</td></tr>';
  }
}

function renderAdmins(list) {
  const tbody = document.getElementById('a-tbody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="py-8 text-center text-slate-500">No admin accounts found.</td></tr>';
    document.getElementById('a-page-info').textContent = 'Showing 0 of 0 admins';
    document.getElementById('a-prev').disabled = true;
    document.getElementById('a-next').disabled = true;
    return;
  }

  const roleColors = {
    superadmin: 'bg-purple-950/70 text-purple-700 border-purple-800',
    admin: 'bg-indigo-950/70 text-indigo-300 border-indigo-800',
    moderator: 'bg-cyan-950/70 text-cyan-300 border-cyan-800'
  };

  tbody.innerHTML = list.map(a => {
    const roleBadge = roleColors[a.role] || 'bg-slate-100 text-slate-700 border-slate-200';
    const statusBadge = a.is_active
      ? '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950/60 border border-emerald-800 text-emerald-300"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>Active</span>'
      : '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-100 border border-slate-200 text-slate-600">Inactive</span>';

    const initials = (a.name || 'A').slice(0, 2).toUpperCase();
    const createdStr = a.created_at ? new Date(a.created_at).toLocaleString() : '—';
    const loginStr = a.last_login ? new Date(a.last_login).toLocaleString() : '<span class="text-slate-500 italic">Never</span>';

    return `
      <tr class="hover:bg-slate-50 transition">
        <td class="py-3 px-4">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 rounded-lg bg-sky-500/20 border border-sky-500/30 text-sky-300 flex items-center justify-center font-bold text-xs">
              ${initials}
            </div>
            <div>
              <div class="font-bold text-slate-900 text-xs">${escapeHtml(a.name || 'Admin')}</div>
              <div class="text-[11px] text-slate-500 font-mono">${escapeHtml(a.email)}</div>
            </div>
          </div>
        </td>
        <td class="py-3 px-4">
          <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold border ${roleBadge}">
            ${escapeHtml(a.role || 'admin')}
          </span>
        </td>
        <td class="py-3 px-4">
          ${statusBadge}
        </td>
        <td class="py-3 px-4">
          <span class="inline-flex items-center gap-1 text-[11px] font-mono text-emerald-600 bg-emerald-950/30 border border-emerald-900/50 px-2 py-0.5 rounded">
            <i data-lucide="lock" class="w-3 h-3 text-emerald-600"></i>
            <span>bcrypt-hashed</span>
          </span>
        </td>
        <td class="py-3 px-4 text-slate-700 text-[11px]">
          ${createdStr}
        </td>
        <td class="py-3 px-4 text-slate-700 text-[11px]">
          ${loginStr}
        </td>
        <td class="py-3 px-4 text-right">
          <div class="flex items-center justify-end gap-1.5">
            <button onclick="openEditAdminModal('${escapeHtml(a.email)}')" class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-200 transition" title="Edit Admin">
              <i data-lucide="edit-3" class="w-3 h-3 text-indigo-400"></i>
              <span>Edit</span>
            </button>
            <button onclick="openAdminLoginModal('${escapeHtml(a.email)}')" class="inline-flex items-center gap-1 px-2 py-1 rounded bg-sky-950/50 hover:bg-sky-900/60 text-sky-300 text-xs font-medium border border-sky-800 transition" title="Test Token Login">
              <i data-lucide="key" class="w-3 h-3 text-sky-600"></i>
              <span>Token</span>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  lucide.createIcons();

  const start = aSkip + 1;
  const end = Math.min(aSkip + PER, aTotal);
  document.getElementById('a-page-info').textContent = `Showing ${start}–${end} of ${aTotal} admins`;
  document.getElementById('a-prev').disabled = aSkip === 0;
  document.getElementById('a-next').disabled = aSkip + PER >= aTotal;
}

function adminPage(dir) {
  if (dir < 0 && aSkip > 0) aSkip = Math.max(0, aSkip - PER);
  else if (dir > 0 && aSkip + PER < aTotal) aSkip += PER;
  loadAdmins();
}

function openAddAdminModal() {
  document.getElementById('add-a-name').value = '';
  document.getElementById('add-a-email').value = '';
  document.getElementById('add-a-password').value = '';
  document.getElementById('add-a-role').value = 'admin';
  document.getElementById('add-a-active').value = 'true';
  openModal('add-admin-modal');
}

function generateRandomAdminPassword(targetId) {
  const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*';
  let pwd = '';
  for (let i = 0; i < 14; i++) {
    pwd += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  const el = document.getElementById(targetId);
  if (el) {
    el.value = pwd;
    el.type = 'text';
    setTimeout(() => { el.type = 'password'; }, 3000);
  }
}

async function submitAddAdmin(e) {
  e.preventDefault();
  const name = document.getElementById('add-a-name').value.trim();
  const email = document.getElementById('add-a-email').value.trim();
  const password = document.getElementById('add-a-password').value;
  const role = document.getElementById('add-a-role').value;
  const isActive = document.getElementById('add-a-active').value === 'true';

  try {
    const res = await fetch('/admin/admins', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, email, password, role, is_active: isActive })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Failed to create admin');
    closeModal('add-admin-modal');
    loadAdmins();
    alert('Admin account created successfully with bcrypt crypto hashing!');
  } catch (err) {
    alert(err.message);
  }
}

function openEditAdminModal(email) {
  const admin = aData.find(x => x.email === email);
  if (!admin) return;
  currentAdminForEdit = admin;
  document.getElementById('edit-a-email').value = admin.email;
  document.getElementById('edit-a-name').value = admin.name || '';
  document.getElementById('edit-a-role').value = admin.role || 'admin';
  document.getElementById('edit-a-active').value = admin.is_active ? 'true' : 'false';
  document.getElementById('edit-a-password').value = '';
  openModal('edit-admin-modal');
}

async function submitEditAdmin(e) {
  e.preventDefault();
  if (!currentAdminForEdit) return;
  const email = currentAdminForEdit.email;
  const name = document.getElementById('edit-a-name').value.trim();
  const role = document.getElementById('edit-a-role').value;
  const isActive = document.getElementById('edit-a-active').value === 'true';
  const password = document.getElementById('edit-a-password').value;

  const payload = { name, role, is_active: isActive };
  if (password && password.trim()) {
    payload.password = password.trim();
  }

  try {
    const res = await fetch(`/admin/admins/${encodeURIComponent(email)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Failed to update admin');
    closeModal('edit-admin-modal');
    loadAdmins();
    alert('Admin account updated successfully!');
  } catch (err) {
    alert(err.message);
  }
}

function openAdminLoginModal(prefillEmail) {
  if (prefillEmail) {
    document.getElementById('login-a-email').value = prefillEmail;
  }
  document.getElementById('admin-login-result').classList.add('hidden');
  openModal('admin-login-modal');
}

async function submitAdminLogin(e) {
  e.preventDefault();
  const email = document.getElementById('login-a-email').value.trim();
  const password = document.getElementById('login-a-password').value;

  const btn = document.getElementById('btn-admin-login-submit');
  btn.disabled = true;
  btn.innerHTML = '<span class="animate-pulse">Authenticating…</span>';

  try {
    const res = await fetch('/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Authentication failed');

    const resultBox = document.getElementById('admin-login-result');
    resultBox.classList.remove('hidden');
    document.getElementById('admin-jwt-output').value = json.access_token;
    document.getElementById('admin-jwt-meta').textContent = JSON.stringify({
      token_type: json.token_type,
      expires_in_minutes: json.expires_in_minutes,
      admin: json.admin
    }, null, 2);

    lucide.createIcons();
  } catch (err) {
    alert('Login Failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="log-in" class="w-3.5 h-3.5"></i><span>Authenticate & Get JWT</span>';
    lucide.createIcons();
  }
}

function copyAdminToken() {
  const token = document.getElementById('admin-jwt-output').value;
  if (!token) return;
  navigator.clipboard.writeText(token).then(() => {
    const btnText = document.getElementById('btn-copy-token-text');
    btnText.textContent = 'Copied!';
    setTimeout(() => { btnText.textContent = 'Copy JWT'; }, 2000);
  });
}


// ── Live System & Database Health Monitoring ──
async function loadSystemHealth(manual = false) {
  const refreshIcon = document.getElementById('health-refresh-icon');
  if (refreshIcon && manual) refreshIcon.classList.add('animate-spin');

  const t0 = performance.now();
  try {
    const res = await fetch('/admin/health-status');
    const clientLatency = Math.round(performance.now() - t0);
    const data = await res.json();

    const apiDot = document.getElementById('health-api-dot');
    const apiBadge = document.getElementById('health-api-badge');
    const apiLatency = document.getElementById('health-api-latency');
    const apiVer = document.getElementById('health-api-ver');

    const dbDot = document.getElementById('health-db-dot');
    const dbBadge = document.getElementById('health-db-badge');
    const dbPing = document.getElementById('health-db-ping');
    const dbName = document.getElementById('health-db-name');

    if (apiBadge) {
      apiBadge.textContent = 'Online';
      apiBadge.className = 'font-bold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded text-[10px] border border-emerald-200';
    }
    if (apiDot) apiDot.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
    if (apiLatency) apiLatency.textContent = `${clientLatency} ms`;
    if (apiVer) apiVer.textContent = `${data.api?.framework || 'FastAPI'} (Py ${data.api?.python || '3.12'})`;

    const isDbOk = data.database?.status === 'connected';
    if (dbBadge) {
      dbBadge.textContent = isDbOk ? 'Connected' : 'Degraded';
      dbBadge.className = isDbOk
        ? 'font-bold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded text-[10px] border border-emerald-200'
        : 'font-bold text-red-700 bg-red-50 px-1.5 py-0.5 rounded text-[10px] border border-red-200';
    }
    if (dbDot) dbDot.className = isDbOk ? 'w-2 h-2 rounded-full bg-emerald-500' : 'w-2 h-2 rounded-full bg-red-500 animate-ping';
    if (dbPing) dbPing.textContent = `${data.database?.ping_ms || 0} ms`;
    if (dbName) dbName.textContent = data.database?.name || 'pas_db';

    if (manual) lucide.createIcons();
  } catch (err) {
    const apiBadge = document.getElementById('health-api-badge');
    const apiDot = document.getElementById('health-api-dot');
    const dbBadge = document.getElementById('health-db-badge');
    const dbDot = document.getElementById('health-db-dot');

    if (apiBadge) {
      apiBadge.textContent = 'Offline';
      apiBadge.className = 'font-bold text-red-700 bg-red-50 px-1.5 py-0.5 rounded text-[10px] border border-red-200';
    }
    if (apiDot) apiDot.className = 'w-2 h-2 rounded-full bg-red-500';
    if (dbBadge) {
      dbBadge.textContent = 'Disconnected';
      dbBadge.className = 'font-bold text-red-700 bg-red-50 px-1.5 py-0.5 rounded text-[10px] border border-red-200';
    }
    if (dbDot) dbDot.className = 'w-2 h-2 rounded-full bg-red-500';
  } finally {
    if (refreshIcon && manual) {
      setTimeout(() => refreshIcon.classList.remove('animate-spin'), 400);
    }
  }
}

// Auto-poll health every 15 seconds
setInterval(loadSystemHealth, 15000);

// Initialize on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  if (!getAdminToken()) {
    // Show default credentials in login gate
    checkAdminAuth();
  } else {
    checkAdminAuth();
    loadStoreFromApi();
    loadOverview();
  loadSystemHealth();
  }
  lucide.createIcons();
});
loadStoreFromApi();
loadOverview();
  loadSystemHealth();
setTimeout(() => lucide.createIcons(), 100);
</script>
</body>
</html>"""
