"""
PISUM SaaS Backend — FastAPI
Endpoints:
  POST /activate-free            → activates free plan for new user (requires JWT)
  POST /create-checkout-session  → creates Stripe checkout session (requires JWT)
  POST /cancel-subscription      → cancels subscription at period end (requires JWT)
  POST /billing-portal           → returns Stripe customer portal URL (requires JWT)
  POST /webhook                  → handles Stripe webhook events
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
import stripe
from supabase import create_client, Client

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # bypasses RLS
STRIPE_SECRET_KEY = os.environ["STRIPE_SECRET_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://pisum.app")  # used for CORS (origin only)
FRONTEND_PAGES_URL = os.environ.get("FRONTEND_PAGES_URL", "https://pisum.app/saas/frontend")  # used for redirect URLs
ALLOWED_ORIGINS = [FRONTEND_URL, "https://pisum.app"]

stripe.api_key = STRIPE_SECRET_KEY

# Stripe Price IDs from your product prices in Stripe
STRIPE_PRICE_IDS = {
    "starter": {
        "monthly": os.environ["STRIPE_PRICE_ID_STARTER_MONTHLY"],
        "annual": os.environ["STRIPE_PRICE_ID_STARTER_ANNUAL"],
    },
    "pro": {
        "monthly": os.environ["STRIPE_PRICE_ID_PRO_MONTHLY"],
        "annual": os.environ["STRIPE_PRICE_ID_PRO_ANNUAL"],
    },
    "expert": {
        "monthly": os.environ["STRIPE_PRICE_ID_EXPERT_MONTHLY"],
        "annual": os.environ["STRIPE_PRICE_ID_EXPERT_ANNUAL"],
    },
    "clinic": {
        "monthly": os.environ["STRIPE_PRICE_ID_CLINIC_MONTHLY"],
        "annual": os.environ["STRIPE_PRICE_ID_CLINIC_ANNUAL"],
    },
}

# Extra-seat add-on for Clinic (optional — leave blank to disable)
STRIPE_PRICE_ID_CLINIC_EXTRA_SEAT = os.environ.get("STRIPE_PRICE_ID_CLINIC_EXTRA_SEAT", "")


# ── Plan feature limits ───────────────────────────────────────────────────────
# ai_enhancer_monthly_limit: 0 = disabled, -1 = unlimited, N = N/month
# worklist: "limited" | "basic" | "full" | "advanced" | "multisite"
# network_sync: None | "single_site" | "multisite"
# network_max_workstations: 0 = disabled, 3 = Expert, -1 = unlimited

PLAN_FEATURES: dict[str, dict] = {
    "free": {
        "templates": 10,
        "languages": 2,
        "export_word": False,
        "ai_dictation": True,
        "ai_dictation_minutes": 30,
        "ai_enhancer_monthly_limit": 10,
        "worklist": "limited",
        "users": 1,
        "stats": False,
        "cr_monthly_limit": 50,
        "pacs_ris": False,
        "custom_templates": False,
        "history_days": 7,
        "advanced_editing": False,
        "structured_reports": False,
        "multilang": False,
        "network_sync": None,
        "network_max_workstations": 0,
    },
    "starter": {
        "templates": 20,
        "languages": 23,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": 500,
        "ai_enhancer_monthly_limit": 50,
        "worklist": "basic",
        "users": 1,
        "stats": False,
        "cr_monthly_limit": None,          # unlimited
        "pacs_ris": False,
        "custom_templates": False,
        "history_days": 90,
        "advanced_editing": False,
        "structured_reports": True,
        "multilang": False,
        "network_sync": None,
        "network_max_workstations": 0,
    },
    "pro": {
        "templates": -1,                   # unlimited
        "languages": 23,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": 2000,
        "ai_enhancer_monthly_limit": 200,
        "worklist": "full",
        "users": 1,
        "stats": "basic",
        "cr_monthly_limit": None,
        "pacs_ris": True,
        "custom_templates": True,
        "history_days": -1,                # unlimited
        "advanced_editing": True,
        "structured_reports": True,
        "multilang": True,
        "network_sync": None,
        "network_max_workstations": 0,
    },
    "expert": {
        "templates": -1,                   # unlimited
        "languages": 23,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": -1,        # unlimited
        "ai_enhancer_monthly_limit": -1,   # unlimited
        "worklist": "advanced",
        "users": 1,
        "stats": "advanced",
        "cr_monthly_limit": None,
        "pacs_ris": True,
        "custom_templates": True,
        "history_days": -1,                # unlimited
        "advanced_editing": True,
        "structured_reports": True,
        "multilang": True,
        "network_sync": "single_site",
        "network_max_workstations": 3,
    },
    "clinic": {
        "templates": -1,                   # unlimited
        "languages": 23,
        "export_word": True,
        "ai_dictation": True,
        "ai_dictation_minutes": -1,        # unlimited
        "ai_enhancer_monthly_limit": -1,   # unlimited
        "worklist": "multisite",
        "users": 5,
        "stats": "advanced",
        "cr_monthly_limit": None,
        "pacs_ris": True,
        "custom_templates": True,
        "history_days": -1,                # unlimited
        "advanced_editing": True,
        "structured_reports": True,
        "multilang": True,
        "network_sync": "multisite",
        "network_max_workstations": -1,    # unlimited
    },
}

# Use the service-role client (backend only — never expose this key to frontend)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

app = FastAPI(title="PISUM SaaS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
    if origin in ALLOWED_ORIGINS:
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
    quantity: int = 1           # Default quantity
    with_trial: bool = False


@app.post("/create-checkout-session")
async def create_checkout_session(
    body: CheckoutRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Creates a Stripe checkout session for the authenticated user.
    """
    plan = body.plan
    if plan not in STRIPE_PRICE_IDS:
        raise HTTPException(status_code=400, detail=f"Invalid plan '{plan}'. Choose: starter, pro, expert, clinic.")

    interval = body.interval if body.interval in ("monthly", "annual") else "monthly"
    price_id = STRIPE_PRICE_IDS[plan][interval]

    quantity = 1

    subscription = _get_subscription(user_id)
    stripe_customer_id = subscription.get("stripe_customer_id") if subscription else None

    subscription_details = {
        "metadata": {
            "user_id": user_id,
            "plan": plan,
            "interval": interval,
        }
    }
    if body.with_trial:
        subscription_details["trial_period_days"] = 14

    try:
        checkout_session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            line_items=[
                {"price": price_id, "quantity": quantity},
            ],
            mode="subscription",
            success_url=f"{FRONTEND_PAGES_URL}/dashboard.html?checkout=success",
            cancel_url=f"{FRONTEND_PAGES_URL}/dashboard.html?checkout=cancel",
            subscription_data=subscription_details,
            metadata={
                "user_id": user_id,
                "plan": plan,
                "interval": interval,
            }
        )
        return {"checkout_url": checkout_session.url}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")


# ── POST /create-extra-seat-checkout ─────────────────────────────────────────

class ExtraSeatRequest(BaseModel):
    quantity: int  # number of extra seats to add (min 1)


@app.post("/create-extra-seat-checkout")
async def create_extra_seat_checkout(
    body: ExtraSeatRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Creates a checkout for extra Clinic seats.
    """
    if not STRIPE_PRICE_ID_CLINIC_EXTRA_SEAT:
        raise HTTPException(status_code=503, detail="Extra seat add-on not configured.")

    subscription = _get_subscription(user_id)
    if not subscription or subscription.get("plan") != "clinic":
        raise HTTPException(status_code=400, detail="Extra seats require an active Clinic subscription.")
    if subscription.get("status") not in ("active", "trialing", "past_due"):
        raise HTTPException(status_code=400, detail="Your Clinic subscription is not active.")

    quantity = max(1, body.quantity)
    stripe_customer_id = subscription.get("stripe_customer_id")

    try:
        checkout_session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            line_items=[
                {"price": STRIPE_PRICE_ID_CLINIC_EXTRA_SEAT, "quantity": quantity},
            ],
            mode="subscription",
            success_url=f"{FRONTEND_PAGES_URL}/dashboard.html?checkout=seats_added",
            cancel_url=f"{FRONTEND_PAGES_URL}/dashboard.html",
            metadata={"user_id": user_id, "plan": "clinic_extra_seat"},
        )
        return {"checkout_url": checkout_session.url}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")


# ── POST /webhook ─────────────────────────────────────────────────────────────

@app.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Receives Stripe webhook events and updates the subscriptions table.
    """
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_session_completed(data)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(data)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data)

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


def _db_update_by_stripe_sub(stripe_sub_id: str, data: dict):
    httpx.patch(
        f"{SUPABASE_URL}/rest/v1/subscriptions",
        headers={**_db_headers(), "Prefer": "return=minimal"},
        params={"stripe_subscription_id": f"eq.{stripe_sub_id}"},
        json=data,
        timeout=10,
    ).raise_for_status()


def _upsert_subscription(user_id: str, row: dict):
    """Insert or update the subscription row for a user."""
    existing = _get_subscription(user_id)
    if existing:
        httpx.patch(
            f"{SUPABASE_URL}/rest/v1/subscriptions",
            headers={**_db_headers(), "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            json=row,
            timeout=10,
        ).raise_for_status()
    else:
        httpx.post(
            f"{SUPABASE_URL}/rest/v1/subscriptions",
            headers={**_db_headers(), "Prefer": "return=minimal"},
            json={"user_id": user_id, **row},
            timeout=10,
        ).raise_for_status()


def _handle_checkout_session_completed(session: dict):
    user_id = session.get("metadata", {}).get("user_id")
    if not user_id:
        return

    stripe_customer_id = session.get("customer")
    stripe_subscription_id = session.get("subscription")

    if not stripe_customer_id or not stripe_subscription_id:
        return

    try:
        subscription = stripe.Subscription.retrieve(stripe_subscription_id)
    except Exception:
        return

    plan = subscription.get("metadata", {}).get("plan", "starter")

    if plan == "clinic_extra_seat":
        quantity = sum(item.quantity for item in subscription.get("items", {}).get("data", []))
        if quantity > 0:
            _db_update(user_id, {
                "extra_seats": quantity,
                "stripe_extra_seat_sub_id": stripe_subscription_id,
            })
        return

    interval = subscription.get("metadata", {}).get("interval", "monthly")
    period_start = datetime.fromtimestamp(subscription["current_period_start"], tz=timezone.utc).isoformat()
    period_end = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc).isoformat()
    status = subscription["status"]
    quantity = sum(item.quantity for item in subscription.get("items", {}).get("data", []))
    extra_seats = max(0, quantity - 5) if plan == "clinic" else 0

    _upsert_subscription(user_id, {
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "status": status,
        "plan": plan,
        "billing_interval": interval,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "extra_seats": extra_seats,
    })


def _handle_subscription_updated(subscription: dict):
    stripe_subscription_id = subscription["id"]
    status = subscription["status"]
    plan = subscription.get("metadata", {}).get("plan", "starter")
    period_end = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc).isoformat()
    cancel_at_period_end = subscription.get("cancel_at_period_end", False)

    update_data = {
        "status": status,
        "current_period_end": period_end,
        "plan": plan,
        "cancel_at_period_end": cancel_at_period_end,
    }

    if plan == "clinic":
        quantity = sum(item.quantity for item in subscription.get("items", {}).get("data", []))
        update_data["extra_seats"] = max(0, quantity - 5)

    _db_update_by_stripe_sub(stripe_subscription_id, update_data)


def _handle_subscription_deleted(subscription: dict):
    stripe_subscription_id = subscription["id"]
    _db_update_by_stripe_sub(stripe_subscription_id, {
        "plan": "free",
        "status": "active",
        "stripe_subscription_id": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
    })


# ── GET /check-access ─────────────────────────────────────────────────────────

@app.get("/check-access")
async def check_access(user_id: str = Depends(get_user_id)):
    """
    Returns {"access": true} only if the user has an active subscription.
    """
    subscription = _get_subscription(user_id)

    if not subscription:
        return {"access": False, "reason": "no_subscription"}

    if subscription["status"] not in ("active", "trialing", "past_due"):
        return {"access": False, "reason": subscription["status"]}

    plan = subscription.get("plan", "starter")

    if plan != "free" and subscription["status"] != "trialing":
        period_end_str = subscription.get("current_period_end")
        if period_end_str:
            period_end = datetime.fromisoformat(period_end_str)
            if period_end.tzinfo is None:
                period_end = period_end.replace(tzinfo=timezone.utc)
            if datetime.now(tz=timezone.utc) > period_end:
                _db_update(user_id, {
                    "plan": "free",
                    "status": "active",
                    "stripe_subscription_id": None,
                    "current_period_end": None,
                    "cancel_at_period_end": False,
                })
                plan = "free"

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
    """
    try:
        existing = _get_subscription(user_id)
        if existing and existing["status"] in ("active", "trialing", "past_due"):
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
    """
    subscription = _get_subscription(user_id)
    plan = "free"
    if subscription and subscription["status"] in ("active", "trialing", "past_due"):
        plan = subscription.get("plan", "free")

    features = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    effective = _get_effective_limits(plan, subscription) if subscription else {}

    if feature == "ai_enhancer":
        limit = effective.get("ai_enhancer_monthly_limit", features["ai_enhancer_monthly_limit"])
        if limit == 0:
            return {"allowed": False, "plan": plan, "limit": 0, "used": 0, "remaining": 0}
        if limit == -1:
            return {"allowed": True, "plan": plan, "limit": -1, "used": 0, "remaining": -1}

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
        limit = features.get("cr_monthly_limit")
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
    """
    subscription = _get_subscription(user_id)
    if not subscription or subscription["status"] not in ("active", "trialing", "past_due"):
        raise HTTPException(status_code=403, detail="No active subscription")

    plan = subscription.get("plan", "free")
    effective = _get_effective_limits(plan, subscription)
    limit = effective["ai_enhancer_monthly_limit"]

    if limit == 0:
        raise HTTPException(status_code=403, detail="AI Enhancer not included in your plan")

    if limit == -1:
        return {"success": True, "remaining": -1}

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
            next_month = (reset_at.replace(day=1) + timedelta(days=32)).replace(day=1)
            reset_at_iso = next_month.isoformat()
            _db_update(user_id, {"ai_enhancer_uses": 0, "ai_enhancer_reset_at": reset_at_iso})
            return 0, reset_at_iso
        return uses, reset_at_str
    else:
        next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
        reset_at_iso = next_month.isoformat()
        _db_update(user_id, {"ai_enhancer_reset_at": reset_at_iso})
        return uses, reset_at_iso


# ── POST /use-report ──────────────────────────────────────────────────────────

@app.post("/use-report")
async def use_report(user_id: str = Depends(get_user_id)):
    """
    Records one report creation for the authenticated user.
    """
    subscription = _get_subscription(user_id)
    if not subscription or subscription["status"] not in ("active", "trialing", "past_due"):
        raise HTTPException(status_code=403, detail="No active subscription")

    plan = subscription.get("plan", "free")
    features = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    limit = features.get("cr_monthly_limit")

    if limit is None:
        return {"success": True, "remaining": -1}

    uses, reset_at = _get_report_state(subscription, user_id)
    if uses >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Monthly report limit reached ({limit}/month). Resets on {reset_at}."
        )

    _db_update(user_id, {"cr_uses": uses + 1})
    return {"success": True, "used": uses + 1, "limit": limit, "remaining": limit - uses - 1}


def _get_report_state(subscription: dict | None, user_id: str) -> tuple[int, str | None]:
    """Returns (cr_uses_this_month, reset_at_iso)."""
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
    Returns full subscription details for the dashboard.
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

    enhancer_limit = effective.get("ai_enhancer_monthly_limit", features.get("ai_enhancer_monthly_limit", 0))
    if enhancer_limit != 0:
        uses, reset_at = _get_enhancer_state(subscription, user_id)
    else:
        uses, reset_at = 0, None

    extra_seats = max(0, int(subscription.get("extra_seats", 0) or 0))
    interval = subscription.get("billing_interval", "monthly")
    period_start = subscription.get("current_period_start") or subscription.get("created_at")
    period_end = subscription.get("current_period_end")

    stripe_sub_id = subscription.get("stripe_subscription_id")

    return {
        "plan": plan,
        "status": status,
        "billing_interval": interval,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "created_at": subscription.get("created_at"),
        "has_billing_portal": bool(stripe_sub_id),
        "extra_seats": extra_seats,
        "cancel_at_period_end": bool(subscription.get("cancel_at_period_end", False)),
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
    Cancels the authenticated user's Stripe subscription at period end.
    """
    subscription = _get_subscription(user_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    if subscription.get("status") not in ("active", "trialing", "past_due"):
        raise HTTPException(status_code=400, detail="Subscription is not active")

    stripe_sub_id = subscription.get("stripe_subscription_id")
    if not stripe_sub_id:
        _db_update(user_id, {"status": "canceled"})
        return {"message": "Subscription canceled"}

    try:
        stripe.Subscription.update(stripe_sub_id, cancel_at_period_end=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")

    _db_update(user_id, {"cancel_at_period_end": True})
    return {"message": "Subscription will be canceled at the end of the billing period"}


# ── POST /billing-portal ─────────────────────────────────────────────────────

@app.post("/billing-portal")
async def billing_portal(user_id: str = Depends(get_user_id)):
    """
    Returns the Stripe customer portal URL for the authenticated user.
    """
    subscription = _get_subscription(user_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    stripe_customer_id = subscription.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(
            status_code=404,
            detail="No billing account found. You may be on the free plan."
        )

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{FRONTEND_PAGES_URL}/dashboard.html",
        )
        return {"portal_url": portal_session.url}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Payment provider error: {e}")


# ── DELETE /delete-account ───────────────────────────────────────────────────

@app.delete("/delete-account")
async def delete_account(user_id: str = Depends(get_user_id)):
    """
    Permanently deletes the authenticated user's account.
    """
    subscription = _get_subscription(user_id)

    if subscription:
        stripe_sub_id = subscription.get("stripe_subscription_id")
        if stripe_sub_id:
            try:
                stripe.Subscription.cancel(stripe_sub_id)
            except Exception:
                pass  # Best-effort

        try:
            httpx.delete(
                f"{SUPABASE_URL}/rest/v1/subscriptions",
                headers={**_db_headers(), "Prefer": "return=minimal"},
                params={"user_id": f"eq.{user_id}"},
                timeout=10,
            ).raise_for_status()
        except Exception:
            pass

    try:
        httpx.delete(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers={**_db_headers(), "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            timeout=10,
        ).raise_for_status()
    except Exception:
        pass

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
