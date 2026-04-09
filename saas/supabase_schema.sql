-- ============================================================
-- PISUM SaaS — Supabase Schema
-- Run this in the Supabase SQL Editor (one-time setup)
-- ============================================================

-- NOTE: Supabase already creates the "auth.users" table automatically.
-- We only need to create the "subscriptions" table.

CREATE TABLE IF NOT EXISTS public.subscriptions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    stripe_customer_id       TEXT,
    stripe_subscription_id   TEXT UNIQUE,
    status                   TEXT NOT NULL DEFAULT 'inactive',
    -- status values: 'active' | 'inactive' | 'canceled'
    plan                     TEXT NOT NULL DEFAULT 'free',
    -- plan values: 'free' | 'starter' | 'pro' | 'expert' | 'clinic'
    ai_enhancer_uses         INT NOT NULL DEFAULT 0,
    ai_enhancer_reset_at     TIMESTAMPTZ,
    current_period_end       TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT subscriptions_user_id_key UNIQUE (user_id)
);

-- ── Migration: add new columns if table already exists ───────────────────────
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free';
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS ai_enhancer_uses INT NOT NULL DEFAULT 0;
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS ai_enhancer_reset_at TIMESTAMPTZ;

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS set_updated_at ON public.subscriptions;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.subscriptions
    FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- ── Row Level Security ────────────────────────────────────────────────────────
-- The backend uses the service_role key and bypasses RLS.
-- The frontend (anon/authenticated key) can only read its own row.

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

-- Authenticated users can read only their own subscription
DROP POLICY IF EXISTS "Users can read own subscription" ON public.subscriptions;
CREATE POLICY "Users can read own subscription"
    ON public.subscriptions FOR SELECT
    USING (auth.uid() = user_id);

-- Only the service_role (backend) can insert/update/delete
-- (No INSERT/UPDATE/DELETE policies for authenticated role → backend handles all writes)

-- ── Optional: index for fast lookups ─────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_sub_id
    ON public.subscriptions (stripe_subscription_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_customer_id
    ON public.subscriptions (stripe_customer_id);
