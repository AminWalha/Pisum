-- ============================================================
-- PISUM SaaS — Supabase Schema
-- Run this in the Supabase SQL Editor (one-time setup)
-- ============================================================

-- NOTE: Supabase already creates the "auth.users" table automatically.
-- We only need to create the "subscriptions" table.

CREATE TABLE IF NOT EXISTS public.subscriptions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    ls_customer_id           TEXT,
    ls_subscription_id       TEXT UNIQUE,
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
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS billing_interval TEXT NOT NULL DEFAULT 'monthly';
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS current_period_start TIMESTAMPTZ;
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS extra_seats INT NOT NULL DEFAULT 0;
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS ls_extra_seat_sub_id TEXT;
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS cr_uses INT NOT NULL DEFAULT 0;
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS cr_reset_at TIMESTAMPTZ;

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
CREATE INDEX IF NOT EXISTS idx_subscriptions_ls_sub_id
    ON public.subscriptions (ls_subscription_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_ls_customer_id
    ON public.subscriptions (ls_customer_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_ls_extra_seat_sub_id
    ON public.subscriptions (ls_extra_seat_sub_id);


-- ============================================================
-- PISUM SaaS — User Profiles
-- Run this AFTER the subscriptions setup above
-- ============================================================

CREATE TABLE IF NOT EXISTS public.profiles (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name    TEXT,
    phone        TEXT,
    organization TEXT,
    specialty    TEXT,
    city         TEXT,
    country      TEXT,
    avatar_url   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT profiles_user_id_key UNIQUE (user_id)
);

-- Auto-update updated_at
DROP TRIGGER IF EXISTS set_profiles_updated_at ON public.profiles;
CREATE TRIGGER set_profiles_updated_at
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- ── Row Level Security ────────────────────────────────────────────────────────
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read own profile"   ON public.profiles;
DROP POLICY IF EXISTS "Users can insert own profile" ON public.profiles;
DROP POLICY IF EXISTS "Users can update own profile" ON public.profiles;

CREATE POLICY "Users can read own profile"
    ON public.profiles FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own profile"
    ON public.profiles FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own profile"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = user_id);


-- ============================================================
-- PISUM SaaS — Avatar Storage (Supabase Storage)
-- Run this in Supabase SQL Editor to configure the avatars bucket
-- ============================================================

-- Create public bucket for avatars (idempotent)
INSERT INTO storage.buckets (id, name, public)
VALUES ('avatars', 'avatars', true)
ON CONFLICT (id) DO NOTHING;

-- RLS on storage objects
DROP POLICY IF EXISTS "Users upload own avatar"  ON storage.objects;
DROP POLICY IF EXISTS "Users update own avatar"  ON storage.objects;
DROP POLICY IF EXISTS "Public read avatars"      ON storage.objects;

CREATE POLICY "Users upload own avatar"
    ON storage.objects FOR INSERT
    WITH CHECK (
        bucket_id = 'avatars'
        AND auth.uid()::text = (storage.foldername(name))[1]
    );

CREATE POLICY "Users update own avatar"
    ON storage.objects FOR UPDATE
    USING (
        bucket_id = 'avatars'
        AND auth.uid()::text = (storage.foldername(name))[1]
    );

CREATE POLICY "Public read avatars"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'avatars');
