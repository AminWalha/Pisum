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
    -- status values: 'active' | 'inactive' | 'canceled' | 'trialing' | 'past_due'
    plan                     TEXT NOT NULL DEFAULT 'free',
    -- plan values: 'free' | 'starter' | 'pro' | 'expert' | 'clinic'
    ai_enhancer_uses         INT NOT NULL DEFAULT 0,
    ai_enhancer_reset_at     TIMESTAMPTZ,
    current_period_end       TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    billing_interval         TEXT NOT NULL DEFAULT 'monthly',
    current_period_start     TIMESTAMPTZ,
    extra_seats              INT NOT NULL DEFAULT 0,
    trial_ends_at            TIMESTAMPTZ,
    stripe_extra_seat_sub_id TEXT,
    cr_uses                  INT NOT NULL DEFAULT 0,
    cr_reset_at              TIMESTAMPTZ,
    cancel_at_period_end     BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT subscriptions_user_id_key UNIQUE (user_id)
);

-- ── Migration from LemonSqueezy: rename columns if they exist ────────────────
DO $$
BEGIN
    IF EXISTS(SELECT * FROM information_schema.columns WHERE table_name='subscriptions' AND column_name='ls_customer_id') THEN
        ALTER TABLE public.subscriptions RENAME COLUMN ls_customer_id TO stripe_customer_id;
    END IF;
    IF EXISTS(SELECT * FROM information_schema.columns WHERE table_name='subscriptions' AND column_name='ls_subscription_id') THEN
        ALTER TABLE public.subscriptions RENAME COLUMN ls_subscription_id TO stripe_subscription_id;
    END IF;
    IF EXISTS(SELECT * FROM information_schema.columns WHERE table_name='subscriptions' AND column_name='ls_extra_seat_sub_id') THEN
        ALTER TABLE public.subscriptions RENAME COLUMN ls_extra_seat_sub_id TO stripe_extra_seat_sub_id;
    END IF;
END $$;


-- Migration: add cancel_at_period_end if missing (for existing tables)
DO $$
BEGIN
    IF NOT EXISTS(SELECT * FROM information_schema.columns WHERE table_name='subscriptions' AND column_name='cancel_at_period_end') THEN
        ALTER TABLE public.subscriptions ADD COLUMN cancel_at_period_end BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;

-- Auto-activate free plan for every new user (email, Google, LinkedIn, etc.)
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.subscriptions (user_id, plan, status)
  VALUES (NEW.id, 'free', 'active')
  ON CONFLICT (user_id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

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
DROP INDEX IF EXISTS idx_subscriptions_ls_sub_id;
DROP INDEX IF EXISTS idx_subscriptions_ls_customer_id;
DROP INDEX IF EXISTS idx_subscriptions_ls_extra_seat_sub_id;

CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_sub_id
    ON public.subscriptions (stripe_subscription_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_customer_id
    ON public.subscriptions (stripe_customer_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_extra_seat_sub_id
    ON public.subscriptions (stripe_extra_seat_sub_id);


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
