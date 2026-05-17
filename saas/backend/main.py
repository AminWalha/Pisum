"""
PISUM SaaS Backend — FastAPI
Endpoints:
  POST /activate-free            → activates free plan for new user (requires JWT)
  POST /create-checkout-session  → creates Lemon Squeezy checkout session (requires JWT)
  POST /cancel-subscription      → cancels subscription at period end (requires JWT)
  POST /billing-portal           → returns Lemon Squeezy customer portal URL (requires JWT)
  POST /webhook                  → handles Lemon Squeezy webhook events
  GET  /check-access             → returns {"access": true/false, "plan": ...} (requires JWT)
  GET  /check-feature            → returns feature limits for current plan (requires JWT)
  POST /use-ai-enhancer          → increments monthly AI Enhancer counter (requires JWT)
  GET  /health                   → keep-alive ping endpoint (no auth)
"""

import os
import hmac
import hashlib
import json
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx
from supabase import create_client, Client

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────

SUPABASE_URL           = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY   = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # bypasses RLS
LS_API_KEY             = os.environ["LEMONSQUEEZY_API_KEY"]
LS_WEBHOOK_SECRET      = os.environ["LEMONSQUEEZY_WEBHOOK_SECRET"]
LS_STORE_ID            = os.environ["LEMONSQUEEZY_STORE_ID"]
FRONTEND_URL           = os.environ.get("FRONTEND_URL", "https://pisum.app")          # used for CORS (origin only)
FRONTEND_PAGES_URL     = os.environ.get("FRONTEND_PAGES_URL", "https://pisum.app/saas/frontend")  # used for redirect URLs

LS_VARIANT_IDS = {
    "starter": {
        "monthly": os.environ["LS_VARIANT_ID_STARTER"],
        "annual":  os.environ["LS_VARIANT_ID_STARTER_ANNUAL"],
    },
    "pro": {
        "monthly": os.environ["LS_VARIANT_ID_PRO"],
        "annual":  os.environ["LS_VARIANT_ID_PRO_ANNUAL"],
    },
    "expert": {
        "monthly": os.environ["LS_VARIANT_ID_EXPERT"],
        "annual":  os.environ["LS_VARIANT_ID_EXPERT_ANNUAL"],
    },
    "clinic": {
        "monthly": os.environ["LS_VARIANT_ID_CLINIC"],
        "annual":  os.environ["LS_VARIANT_ID_CLINIC_ANNUAL"],
    },
}

# Extra-seat add-on for Clinic (optional — leave blank to disable)
LS_VARIANT_ID_CLINIC_EXTRA_SEAT = os.environ.get("LS_VARIANT_ID_CLINIC_EXTRA_SEAT", "")

LS_API_BASE = "https://api.lemonsqueezy.com/v1"
LS_HEADERS = {
    "Authorization": f"Bearer {LS_API_KEY}",
    "Accept": "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
}

# ── Plan feature limits ───────────────────────────────────────────────────────
# ai_enhancer_monthly_limit: 0 = disabled, -1 = unlimited, N = N/month
# worklist: False | "basic" | "full" | "advanced" | "multisite"

PLAN_FEATURES: dict[str, dict] = {
    "free": {
        "templates": 10,
        "languages": 2,
        "export_word": False,
        "ai_dictation": True,
        "ai_dictation_minutes": 20,        # teaser
        "ai_enhancer_monthly_limit": 5,    # teaser
        "worklist": False,
        "users": 1,
        "stats": False,
        "cr_monthly_limit": 20,
    },
    "starter": {
        "templates": 20,
        "languages": 5,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": 200,
        "ai_enhancer_monthly_limit": 50,
        "worklist": "basic",
        "users": 1,
        "stats": False,
        "cr_monthly_limit": None,          # unlimited
    },
    "pro": {
        "templates": 112,
        "languages": 23,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": 2000,
        "ai_enhancer_monthly_limit": 200,
        "worklist": "full",
        "users": 1,
        "stats": "basic",
        "cr_monthly_limit": None,
    },
    "expert": {
        "templates": 112,
        "languages": 23,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": -1,        # unlimited
        "ai_enhancer_monthly_limit": -1,   # unlimited
        "worklist": "advanced",
        "users": 1,
        "stats": "advanced",
        "cr_monthly_limit": None,
    },
    "clinic": {
        "templates": -1,                   # custom / unlimited
        "languages": 23,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": -1,        # unlimited
        "ai_enhancer_monthly_limit": -1,   # unlimited
        "worklist": "multisite",
        "users": 5,
        "stats": "advanced",
        "cr_monthly_limit": None,
    },
}

# Use the service-role client (backend only — never expose this key to frontend)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

app = FastAPI(title="PISUM SaaS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}


# Ensure CORS headers are present even on unhandled 500 errors
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin", "")
    headers = {}
    if origin == FRONTEND_URL:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {exc}"},
        headers=headers,
    )


# ── JWT Auth dependency ───────────────────────────────────────────────────────

def get_user_id(authorization: str = Header(...)) -> str:
    """
    Extract and verify user UUID from Supabase JWT.
    Calls the Supabase Auth REST API directly — avoids python-jose algorithm issues.
    The Authorization header must be: Bearer <token>
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization.removeprefix("Bearer ").strip()

    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_SERVICE_KEY},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = resp.json().get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return user_id
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ── Helper: get subscription row ─────────────────────────────────────────────

def _get_subscription(user_id: str) -> dict | None:
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/subscriptions",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        },
        params={"user_id": f"eq.{user_id}", "limit": "1"},
        timeout=10,
    )
    rows = resp.json()
    if not isinstance(rows, list) or len(rows) == 0:
        return None
    return rows[0]


# ── POST /create-checkout-session ────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan: str       # "starter" | "pro" | "expert" | "clinic"
    interval: str = "monthly"  # "monthly" | "annual"
    quantity: int = 5           # clinic only — number of seats (min 5)


@app.post("/create-checkout-session")
async def create_checkout_session(
    body: CheckoutRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Creates a Lemon Squeezy checkout session for the authenticated user.
    Accepts a 'plan' field: 'starter', 'pro', 'expert', or 'clinic'.
    Returns the Lemon Squeezy hosted checkout URL.
    """
    plan = body.plan
    if plan not in LS_VARIANT_IDS:
        raise HTTPException(status_code=400, detail=f"Invalid plan '{plan}'. Choose: starter, pro, expert, clinic.")

    interval = body.interval if body.interval in ("monthly", "annual") else "monthly"
    variant_id = LS_VARIANT_IDS[plan][interval]

    quantity = max(5, body.quantity) if plan == "clinic" else 1

    # Fetch user email from Supabase auth
    user_resp = supabase.auth.admin.get_user_by_id(user_id)
    if not user_resp or not user_resp.user:
        raise HTTPException(status_code=404, detail="User not found")

    email = user_resp.user.email

    try:
        resp = httpx.post(
            f"{LS_API_BASE}/checkouts",
            headers=LS_HEADERS,
            json={
                "data": {
                    "type": "checkouts",
                    "attributes": {
                        "checkout_data": {
                            "email": email,
                            "variant_quantities": [
                                {"variant_id": int(variant_id), "quantity": quantity},
                            ],
                            "custom": {
                                "user_id": user_id,
                                "plan": plan,
                                "interval": interval,
                            },
                        },
                        "product_options": {
                            "redirect_url": f"{FRONTEND_PAGES_URL}/dashboard.html?checkout=success",
                        },
                    },
                    "relationships": {
                        "store": {"data": {"type": "stores", "id": LS_STORE_ID}},
                        "variant": {"data": {"type": "variants", "id": variant_id}},
                    },
                }
            },
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")

    checkout_url = resp.json()["data"]["attributes"]["url"]
    return {"checkout_url": checkout_url}


# ── POST /create-extra-seat-checkout ─────────────────────────────────────────

class ExtraSeatRequest(BaseModel):
    quantity: int  # number of extra seats to add (min 1)


@app.post("/create-extra-seat-checkout")
async def create_extra_seat_checkout(
    body: ExtraSeatRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Creates a checkout for extra Clinic seats (+€69/seat/month).
    Requires the user to already have an active Clinic subscription.
    The quantity here is the number of EXTRA seats (on top of the included 5).
    """
    if not LS_VARIANT_ID_CLINIC_EXTRA_SEAT:
        raise HTTPException(status_code=503, detail="Extra seat add-on not configured.")

    subscription = _get_subscription(user_id)
    if not subscription or subscription.get("plan") != "clinic":
        raise HTTPException(status_code=400, detail="Extra seats require an active Clinic subscription.")
    if subscription.get("status") not in ("active", "canceling"):
        raise HTTPException(status_code=400, detail="Your Clinic subscription is not active.")

    quantity = max(1, body.quantity)

    user_resp = supabase.auth.admin.get_user_by_id(user_id)
    if not user_resp or not user_resp.user:
        raise HTTPException(status_code=404, detail="User not found")
    email = user_resp.user.email

    try:
        resp = httpx.post(
            f"{LS_API_BASE}/checkouts",
            headers=LS_HEADERS,
            json={
                "data": {
                    "type": "checkouts",
                    "attributes": {
                        "checkout_data": {
                            "email": email,
                            "variant_quantities": [
                                {"variant_id": int(LS_VARIANT_ID_CLINIC_EXTRA_SEAT), "quantity": quantity},
                            ],
                            "custom": {
                                "user_id": user_id,
                                "plan": "clinic_extra_seat",
                            },
                        },
                        "product_options": {
                            "redirect_url": f"{FRONTEND_PAGES_URL}/dashboard.html?checkout=seats_added",
                        },
                    },
                    "relationships": {
                        "store": {"data": {"type": "stores", "id": LS_STORE_ID}},
                        "variant": {"data": {"type": "variants", "id": str(LS_VARIANT_ID_CLINIC_EXTRA_SEAT)}},
                    },
                }
            },
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")

    checkout_url = resp.json()["data"]["attributes"]["url"]
    return {"checkout_url": checkout_url}


# ── POST /webhook ─────────────────────────────────────────────────────────────

@app.post("/webhook")
async def lemonsqueezy_webhook(request: Request, x_signature: str = Header(...)):
    """
    Receives Lemon Squeezy webhook events and updates the subscriptions table.
    Lemon Squeezy verifies authenticity via HMAC-SHA256 signature in X-Signature header.
    """
    payload = await request.body()

    expected_sig = hmac.new(
        LS_WEBHOOK_SECRET.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(x_signature, expected_sig):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event = json.loads(payload)
    event_name = event.get("meta", {}).get("event_name", "")

    if event_name == "subscription_created":
        _handle_subscription_created(event)

    elif event_name == "subscription_updated":
        _handle_subscription_updated(event)

    elif event_name == "subscription_payment_success":
        _handle_payment_succeeded(event)

    elif event_name == "subscription_payment_failed":
        _handle_payment_failed(event)

    elif event_name == "subscription_cancelled":
        _handle_subscription_cancelled(event)

    elif event_name == "subscription_expired":
        _handle_subscription_deleted(event)

    return {"status": "ok"}


def _db_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _db_update(user_id: str, data: dict):
    httpx.patch(
        f"{SUPABASE_URL}/rest/v1/subscriptions",
        headers={**_db_headers(), "Prefer": "return=minimal"},
        params={"user_id": f"eq.{user_id}"},
        json=data,
        timeout=10,
    ).raise_for_status()


def _db_update_by_ls_sub(ls_sub_id: str, data: dict):
    httpx.patch(
        f"{SUPABASE_URL}/rest/v1/subscriptions",
        headers={**_db_headers(), "Prefer": "return=minimal"},
        params={"ls_subscription_id": f"eq.{ls_sub_id}"},
        json=data,
        timeout=10,
    ).raise_for_status()


def _upsert_subscription(user_id: str, row: dict):
    """Insert or update the subscription row for a user."""
    existing = _get_subscription(user_id)
    if existing:
        # Row exists → PATCH
        httpx.patch(
            f"{SUPABASE_URL}/rest/v1/subscriptions",
            headers={**_db_headers(), "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            json=row,
            timeout=10,
        ).raise_for_status()
    else:
        # No row yet → INSERT
        httpx.post(
            f"{SUPABASE_URL}/rest/v1/subscriptions",
            headers={**_db_headers(), "Prefer": "return=minimal"},
            json={"user_id": user_id, **row},
            timeout=10,
        ).raise_for_status()


def _handle_subscription_created(event: dict):
    meta   = event.get("meta", {})
    data   = event.get("data", {})
    attrs  = data.get("attributes", {})
    custom = meta.get("custom_data", {})

    user_id = custom.get("user_id")
    if not user_id:
        return

    plan = custom.get("plan", "starter")

    # Extra-seat addon: just update the extra_seats count on the user's existing row
    if plan == "clinic_extra_seat":
        quantity = int(attrs.get("quantity", 0))
        sub_id   = str(data.get("id", ""))
        if quantity > 0:
            _db_update(user_id, {
                "extra_seats": quantity,
                "ls_extra_seat_sub_id": sub_id,
            })
        return

    subscription_id = str(data.get("id", ""))
    customer_id     = str(attrs.get("customer_id", ""))
    interval        = custom.get("interval", "monthly")
    period_end      = attrs.get("current_period_end")
    period_start    = attrs.get("created_at")
    quantity        = int(attrs.get("quantity", 1))
    extra_seats     = max(0, quantity - 5) if plan == "clinic" else 0

    # Map Lemon Squeezy status: "on_trial" → "trial", everything else → "active"
    ls_status     = attrs.get("status", "active")
    trial_ends_at = attrs.get("trial_ends_at")
    status        = "trial" if ls_status == "on_trial" and trial_ends_at else "active"

    _upsert_subscription(user_id, {
        "ls_customer_id": customer_id,
        "ls_subscription_id": subscription_id,
        "status": status,
        "plan": plan,
        "billing_interval": interval,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "trial_ends_at": trial_ends_at,
        "extra_seats": extra_seats,
    })


def _handle_subscription_updated(event: dict):
    """Handles quantity changes (seat upgrades/downgrades) from the customer portal."""
    data  = event.get("data", {})
    attrs = data.get("attributes", {})
    subscription_id = str(data.get("id", ""))
    if not subscription_id:
        return

    quantity = int(attrs.get("quantity", 1))

    # Look up the existing row to know the plan
    rows = httpx.get(
        f"{SUPABASE_URL}/rest/v1/subscriptions",
        headers=_db_headers(),
        params={"ls_subscription_id": f"eq.{subscription_id}", "limit": "1"},
        timeout=10,
    ).json()
    if not isinstance(rows, list) or len(rows) == 0:
        return

    plan = rows[0].get("plan", "")
    extra_seats = max(0, quantity - 5) if plan == "clinic" else 0

    _db_update_by_ls_sub(subscription_id, {"extra_seats": extra_seats})


def _handle_payment_succeeded(event: dict):
    """Renewal succeeded — re-activate and extend period."""
    data  = event.get("data", {})
    attrs = data.get("attributes", {})
    subscription_id = str(data.get("id", ""))
    if not subscription_id:
        return

    period_end = attrs.get("current_period_end")
    _db_update_by_ls_sub(subscription_id, {
        "status": "active",
        "current_period_end": period_end,
    })


def _handle_payment_failed(event: dict):
    data = event.get("data", {})
    subscription_id = str(data.get("id", ""))
    if not subscription_id:
        return
    _db_update_by_ls_sub(subscription_id, {"status": "inactive"})


def _handle_subscription_cancelled(event: dict):
    """User canceled from billing portal — set status to 'canceling' (access until period end)."""
    data = event.get("data", {})
    attrs = data.get("attributes", {})
    subscription_id = str(data.get("id", ""))
    if not subscription_id:
        return

    # Check if this is the extra-seat addon cancellation
    if _is_extra_seat_sub(subscription_id):
        _db_update_by_extra_seat_sub(subscription_id, {"extra_seats": 0, "ls_extra_seat_sub_id": None})
        return

    period_end = attrs.get("current_period_end")
    update = {"status": "canceling"}
    if period_end:
        update["current_period_end"] = period_end
    _db_update_by_ls_sub(subscription_id, update)


def _handle_subscription_deleted(event: dict):
    """Subscription has actually expired — revoke access."""
    data = event.get("data", {})
    subscription_id = str(data.get("id", ""))
    if not subscription_id:
        return

    # Check if this is the extra-seat addon expiry
    if _is_extra_seat_sub(subscription_id):
        _db_update_by_extra_seat_sub(subscription_id, {"extra_seats": 0, "ls_extra_seat_sub_id": None})
        return

    _db_update_by_ls_sub(subscription_id, {"status": "canceled"})


def _is_extra_seat_sub(subscription_id: str) -> bool:
    """Returns True if this subscription ID belongs to an extra-seat addon row."""
    try:
        rows = httpx.get(
            f"{SUPABASE_URL}/rest/v1/subscriptions",
            headers=_db_headers(),
            params={"ls_extra_seat_sub_id": f"eq.{subscription_id}", "limit": "1"},
            timeout=10,
        ).json()
        return isinstance(rows, list) and len(rows) > 0
    except Exception:
        return False


def _db_update_by_extra_seat_sub(extra_seat_sub_id: str, data: dict):
    httpx.patch(
        f"{SUPABASE_URL}/rest/v1/subscriptions",
        headers={**_db_headers(), "Prefer": "return=minimal"},
        params={"ls_extra_seat_sub_id": f"eq.{extra_seat_sub_id}"},
        json=data,
        timeout=10,
    ).raise_for_status()


# ── GET /check-access ─────────────────────────────────────────────────────────

@app.get("/check-access")
async def check_access(user_id: str = Depends(get_user_id)):
    """
    Returns {"access": true} only if the user has an active subscription.
    Used by both the frontend and the desktop app.
    """
    subscription = _get_subscription(user_id)

    if not subscription:
        return {"access": False, "reason": "no_subscription"}

    if subscription["status"] not in ("active", "canceling", "trial"):
        return {"access": False, "reason": subscription["status"]}

    plan = subscription.get("plan", "starter")

    # Trial users: check trial_ends_at
    if subscription["status"] == "trial":
        trial_end_str = subscription.get("trial_ends_at")
        if trial_end_str:
            trial_end = datetime.fromisoformat(trial_end_str.replace("Z", "+00:00"))
            if trial_end.tzinfo is None:
                trial_end = trial_end.replace(tzinfo=timezone.utc)
            if datetime.now(tz=timezone.utc) > trial_end:
                _db_update(user_id, {"status": "inactive"})
                return {"access": False, "reason": "trial_expired"}

    # Paid subscribers: check period_end (skip for free plan)
    if plan != "free" and subscription["status"] != "trial":
        period_end_str = subscription.get("current_period_end")
        if period_end_str:
            period_end = datetime.fromisoformat(period_end_str)
            if period_end.tzinfo is None:
                period_end = period_end.replace(tzinfo=timezone.utc)
            if datetime.now(tz=timezone.utc) > period_end:
                _db_update(user_id, {"status": "canceled"})
                return {"access": False, "reason": "expired"}

    base_features = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    if plan == "clinic" and subscription:
        effective = _get_effective_limits(plan, subscription)
        features_out = {**base_features, **effective}
    else:
        features_out = base_features
    return {"access": True, "plan": plan, "features": features_out}


# ── POST /activate-free ───────────────────────────────────────────────────────

@app.post("/activate-free")
async def activate_free(user_id: str = Depends(get_user_id)):
    """
    Activates the free plan for a new user with no active subscription.
    Idempotent: safe to call multiple times.
    """
    try:
        existing = _get_subscription(user_id)
        if existing and existing["status"] == "active":
            return {"message": "Already active", "plan": existing.get("plan", "free")}

        _upsert_subscription(user_id, {
            "status": "active",
            "plan": "free",
            "ai_enhancer_uses": 0,
            "ai_enhancer_reset_at": None,
        })
        return {"message": "Free plan activated", "plan": "free"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ── GET /check-feature ────────────────────────────────────────────────────────

@app.get("/check-feature")
async def check_feature(feature: str, user_id: str = Depends(get_user_id)):
    """
    Returns the feature limit/value for the authenticated user's current plan.
    Query param 'feature' can be any key from PLAN_FEATURES
    (e.g. ai_enhancer_monthly_limit, worklist, templates, ...).
    For 'ai_enhancer' specifically, also returns used/remaining this month.
    """
    subscription = _get_subscription(user_id)
    plan = "free"
    if subscription and subscription["status"] == "active":
        plan = subscription.get("plan", "free")

    features = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    effective = _get_effective_limits(plan, subscription) if subscription else {}

    if feature == "ai_enhancer":
        limit = effective.get("ai_enhancer_monthly_limit", features["ai_enhancer_monthly_limit"])
        if limit == 0:
            return {"allowed": False, "plan": plan, "limit": 0, "used": 0, "remaining": 0}
        if limit == -1:
            return {"allowed": True, "plan": plan, "limit": -1, "used": 0, "remaining": -1}

        # Check / auto-reset monthly counter
        uses, reset_at = _get_enhancer_state(subscription, user_id)
        remaining = limit - uses
        return {
            "allowed": remaining > 0,
            "plan": plan,
            "limit": limit,
            "used": uses,
            "remaining": max(0, remaining),
            "reset_at": reset_at,
        }

    if feature == "cr":
        limit = features.get("cr_monthly_limit")   # None = unlimited
        if limit is None:
            return {"allowed": True, "plan": plan, "limit": -1, "used": 0, "remaining": -1}
        uses, reset_at = _get_report_state(subscription, user_id)
        remaining = limit - uses
        return {
            "allowed": remaining > 0,
            "plan": plan,
            "limit": limit,
            "used": uses,
            "remaining": max(0, remaining),
            "reset_at": reset_at,
        }

    if feature == "ai_dictation_minutes":
        value = effective.get("ai_dictation_minutes", features.get("ai_dictation_minutes", 0))
        return {"allowed": bool(value), "value": value, "plan": plan}

    if feature == "users":
        value = effective.get("users", features.get("users", 1))
        return {"allowed": True, "value": value, "plan": plan}

    if feature not in features:
        raise HTTPException(status_code=400, detail=f"Unknown feature '{feature}'")

    value = features[feature]
    return {"allowed": bool(value), "value": value, "plan": plan}


def _get_effective_limits(plan: str, subscription: dict) -> dict:
    """
    Returns effective limits for a plan, scaling clinic limits by purchased seat quantity.
    Each extra seat (beyond base 5) adds +300 AI enhancer calls and +1,200 dictation min/month.
    """
    base = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    limits = {
        "ai_enhancer_monthly_limit": base["ai_enhancer_monthly_limit"],
        "ai_dictation_minutes":      base.get("ai_dictation_minutes", 0),
        "users":                     base.get("users", 1),
    }
    if plan == "clinic":
        extra_seats = max(0, int(subscription.get("extra_seats", 0) or 0))
        limits["users"] = 5 + extra_seats
    return limits


# ── POST /use-ai-enhancer ─────────────────────────────────────────────────────

@app.post("/use-ai-enhancer")
async def use_ai_enhancer(user_id: str = Depends(get_user_id)):
    """
    Records one AI Enhancer use for the authenticated user.
    Returns 403 if the plan doesn't include AI Enhancer or the monthly limit is reached.
    """
    subscription = _get_subscription(user_id)
    if not subscription or subscription["status"] != "active":
        raise HTTPException(status_code=403, detail="No active subscription")

    plan = subscription.get("plan", "free")
    effective = _get_effective_limits(plan, subscription)
    limit = effective["ai_enhancer_monthly_limit"]

    if limit == 0:
        raise HTTPException(status_code=403, detail="AI Enhancer not included in your plan")

    if limit == -1:
        return {"success": True, "remaining": -1}  # unlimited — no counter needed

    uses, reset_at = _get_enhancer_state(subscription, user_id)
    if uses >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Monthly AI Enhancer limit reached ({limit}/month). Resets on {reset_at}."
        )

    _db_update(user_id, {"ai_enhancer_uses": uses + 1})

    return {"success": True, "used": uses + 1, "limit": limit, "remaining": limit - uses - 1}


def _get_enhancer_state(subscription: dict | None, user_id: str) -> tuple[int, str | None]:
    """
    Returns (uses_this_month, reset_at_iso).
    Auto-resets the counter if the reset date has passed.
    """
    if not subscription:
        return 0, None

    uses = subscription.get("ai_enhancer_uses", 0) or 0
    reset_at_str = subscription.get("ai_enhancer_reset_at")

    now = datetime.now(tz=timezone.utc)

    if reset_at_str:
        reset_at = datetime.fromisoformat(reset_at_str)
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)
        if now >= reset_at:
            # New reset: first day of the month after next reset
            next_month = (reset_at.replace(day=1) + timedelta(days=32)).replace(day=1)
            reset_at_iso = next_month.isoformat()
            _db_update(user_id, {"ai_enhancer_uses": 0, "ai_enhancer_reset_at": reset_at_iso})
            return 0, reset_at_iso
        return uses, reset_at_str
    else:
        # First use — initialize reset date to first day of next month
        next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
        reset_at_iso = next_month.isoformat()
        _db_update(user_id, {"ai_enhancer_reset_at": reset_at_iso})
        return uses, reset_at_iso


# ── POST /use-report ──────────────────────────────────────────────────────────

@app.post("/use-report")
async def use_report(user_id: str = Depends(get_user_id)):
    """
    Records one report creation for the authenticated user.
    Only enforces the monthly limit for free-plan users (cr_monthly_limit = 20).
    Unlimited plans return success without touching the counter.
    """
    subscription = _get_subscription(user_id)
    if not subscription or subscription["status"] != "active":
        raise HTTPException(status_code=403, detail="No active subscription")

    plan = subscription.get("plan", "free")
    features = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    limit = features.get("cr_monthly_limit")   # None = unlimited, int = capped

    if limit is None:
        return {"success": True, "remaining": -1}  # unlimited — no counter needed

    uses, reset_at = _get_report_state(subscription, user_id)
    if uses >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Monthly report limit reached ({limit}/month). Resets on {reset_at}."
        )

    _db_update(user_id, {"cr_uses": uses + 1})
    return {"success": True, "used": uses + 1, "limit": limit, "remaining": limit - uses - 1}


def _get_report_state(subscription: dict | None, user_id: str) -> tuple[int, str | None]:
    """Returns (cr_uses_this_month, reset_at_iso). Auto-resets on month rollover."""
    if not subscription:
        return 0, None

    uses = subscription.get("cr_uses", 0) or 0
    reset_at_str = subscription.get("cr_reset_at")

    now = datetime.now(tz=timezone.utc)

    if reset_at_str:
        reset_at = datetime.fromisoformat(reset_at_str)
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)
        if now >= reset_at:
            next_month = (reset_at.replace(day=1) + timedelta(days=32)).replace(day=1)
            reset_at_iso = next_month.isoformat()
            _db_update(user_id, {"cr_uses": 0, "cr_reset_at": reset_at_iso})
            return 0, reset_at_iso
        return uses, reset_at_str
    else:
        next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
        reset_at_iso = next_month.isoformat()
        _db_update(user_id, {"cr_reset_at": reset_at_iso})
        return uses, reset_at_iso


# ── GET /subscription-info ────────────────────────────────────────────────────

@app.get("/subscription-info")
async def subscription_info(user_id: str = Depends(get_user_id)):
    """
    Returns full subscription details for the dashboard:
    plan, status, billing dates, interval, features, and usage counters.
    """
    subscription = _get_subscription(user_id)

    if not subscription:
        return {
            "plan": "none", "status": "none",
            "billing_interval": "monthly",
            "current_period_start": None,
            "current_period_end": None,
            "created_at": None,
            "features": {},
            "usage": {"ai_enhancer": {"used": 0, "limit": 0, "reset_at": None}},
        }

    plan    = subscription.get("plan", "free")
    status  = subscription.get("status", "inactive")
    features = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    effective = _get_effective_limits(plan, subscription)

    # AI enhancer usage (only if plan includes it) — use effective (scaled) limit
    enhancer_limit = effective.get("ai_enhancer_monthly_limit", features.get("ai_enhancer_monthly_limit", 0))
    if enhancer_limit != 0:
        uses, reset_at = _get_enhancer_state(subscription, user_id)
    else:
        uses, reset_at = 0, None

    extra_seats = max(0, int(subscription.get("extra_seats", 0) or 0))

    interval       = subscription.get("billing_interval", "monthly")
    period_start   = subscription.get("current_period_start") or subscription.get("created_at")
    period_end     = subscription.get("current_period_end")

    # Fallback: compute period_end from period_start when missing (e.g. manually-created subscriptions)
    if not period_end and period_start:
        try:
            start_dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
            delta = timedelta(days=365) if interval == "annual" else timedelta(days=30)
            period_end = (start_dt + delta).isoformat()
        except Exception:
            pass

    ls_sub_id = subscription.get("ls_subscription_id")

    return {
        "plan": plan,
        "status": status,
        "billing_interval": interval,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "created_at": subscription.get("created_at"),
        "has_billing_portal": bool(ls_sub_id),
        "extra_seats": extra_seats,
        "features": {
            **features,
            "ai_enhancer_monthly_limit": enhancer_limit,
            "ai_dictation_minutes": effective.get("ai_dictation_minutes", features.get("ai_dictation_minutes", 0)),
        },
        "usage": {
            "ai_enhancer": {
                "used": uses,
                "limit": enhancer_limit,
                "reset_at": reset_at,
            }
        },
    }


# ── POST /cancel-subscription ────────────────────────────────────────────────

@app.post("/cancel-subscription")
async def cancel_subscription(user_id: str = Depends(get_user_id)):
    """
    Cancels the authenticated user's Lemon Squeezy subscription at period end.
    The subscription stays active until current_period_end, then becomes canceled.
    """
    subscription = _get_subscription(user_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    ls_sub_id = subscription.get("ls_subscription_id")
    if not ls_sub_id:
        raise HTTPException(
            status_code=404,
            detail="No active paid subscription to cancel"
        )

    if subscription.get("status") not in ("active",):
        raise HTTPException(
            status_code=400,
            detail="Subscription is not active"
        )

    try:
        resp = httpx.patch(
            f"{LS_API_BASE}/subscriptions/{ls_sub_id}",
            headers=LS_HEADERS,
            json={
                "data": {
                    "type": "subscriptions",
                    "id": str(ls_sub_id),
                    "attributes": {"cancelled": True},
                }
            },
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Payment provider error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")

    _db_update(user_id, {"status": "canceling"})

    return {"message": "Subscription will be canceled at the end of the billing period"}


# ── POST /billing-portal ─────────────────────────────────────────────────────

@app.post("/billing-portal")
async def billing_portal(user_id: str = Depends(get_user_id)):
    """
    Returns the Lemon Squeezy customer portal URL for the authenticated user.
    Returns {"portal_url": "..."} — redirect the user there to manage billing.
    """
    subscription = _get_subscription(user_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    ls_sub_id = subscription.get("ls_subscription_id")
    if not ls_sub_id:
        raise HTTPException(
            status_code=404,
            detail="No billing account found. You may be on the free plan."
        )

    try:
        resp = httpx.get(
            f"{LS_API_BASE}/subscriptions/{ls_sub_id}",
            headers=LS_HEADERS,
            timeout=10,
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Billing account not found. Please contact support.")
        resp.raise_for_status()
        portal_url = resp.json()["data"]["attributes"]["urls"]["customer_portal"]
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Billing portal error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")

    return {"portal_url": portal_url}


# ── DELETE /delete-account ───────────────────────────────────────────────────

@app.delete("/delete-account")
async def delete_account(user_id: str = Depends(get_user_id)):
    """
    Permanently deletes the authenticated user's account:
    1. Cancels any active Lemon Squeezy subscription immediately.
    2. Removes the subscriptions and profiles rows from Supabase.
    3. Deletes the Supabase Auth user record.
    """
    subscription = _get_subscription(user_id)

    # Cancel the Lemon Squeezy subscription if one exists and is still active
    if subscription:
        ls_sub_id = subscription.get("ls_subscription_id")
        status    = subscription.get("status", "")
        if ls_sub_id and status in ("active", "canceling"):
            try:
                httpx.delete(
                    f"{LS_API_BASE}/subscriptions/{ls_sub_id}",
                    headers=LS_HEADERS,
                    timeout=10,
                ).raise_for_status()
            except Exception:
                pass  # Best-effort — don't block account deletion if LS call fails

        # Delete subscription row
        try:
            httpx.delete(
                f"{SUPABASE_URL}/rest/v1/subscriptions",
                headers={**_db_headers(), "Prefer": "return=minimal"},
                params={"user_id": f"eq.{user_id}"},
                timeout=10,
            ).raise_for_status()
        except Exception:
            pass

    # Delete profile row
    try:
        httpx.delete(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers={**_db_headers(), "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            timeout=10,
        ).raise_for_status()
    except Exception:
        pass

    # Delete the Supabase Auth user (this is irreversible)
    try:
        result = supabase.auth.admin.delete_user(user_id)
        if hasattr(result, "error") and result.error:
            raise HTTPException(status_code=500, detail=f"Auth deletion failed: {result.error.message}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth deletion failed: {e}")

    return {"message": "Account deleted successfully"}


# ── GET / (health check) ─────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "PISUM SaaS API is running"}
