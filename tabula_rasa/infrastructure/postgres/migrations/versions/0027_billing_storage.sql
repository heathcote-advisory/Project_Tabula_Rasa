CREATE TABLE billing_plan_versions (
    plan_key text NOT NULL CHECK (plan_key <> '' AND plan_key = btrim(plan_key)),
    version integer NOT NULL CHECK (version > 0),
    entitlements jsonb NOT NULL CHECK (jsonb_typeof(entitlements) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (plan_key, version)
);

CREATE TABLE billing_customers (
    organization_id text PRIMARY KEY REFERENCES organizations(id),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE billing_subscriptions (
    organization_id text NOT NULL REFERENCES billing_customers(organization_id),
    id text NOT NULL CHECK (id <> '' AND id = btrim(id)),
    plan_key text NOT NULL,
    plan_version integer NOT NULL,
    state text NOT NULL CHECK (state <> '' AND state = btrim(state)),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id, id),
    FOREIGN KEY (plan_key, plan_version) REFERENCES billing_plan_versions(plan_key, version)
);

-- Scope identifies a configured provider account and environment.
CREATE TABLE billing_provider_customers (
    organization_id text NOT NULL REFERENCES billing_customers(organization_id),
    provider text NOT NULL CHECK (provider <> '' AND provider = btrim(provider)),
    provider_scope text NOT NULL CHECK (provider_scope <> '' AND provider_scope = btrim(provider_scope)),
    provider_customer_id text NOT NULL CHECK (btrim(provider_customer_id) <> ''),
    PRIMARY KEY (organization_id, provider, provider_scope),
    UNIQUE (provider, provider_scope, provider_customer_id)
);

CREATE TABLE billing_provider_subscriptions (
    organization_id text NOT NULL,
    subscription_id text NOT NULL,
    provider text NOT NULL,
    provider_scope text NOT NULL,
    provider_subscription_id text NOT NULL CHECK (btrim(provider_subscription_id) <> ''),
    PRIMARY KEY (organization_id, subscription_id, provider, provider_scope),
    UNIQUE (provider, provider_scope, provider_subscription_id),
    FOREIGN KEY (organization_id, subscription_id) REFERENCES billing_subscriptions(organization_id, id),
    FOREIGN KEY (organization_id, provider, provider_scope)
        REFERENCES billing_provider_customers(organization_id, provider, provider_scope)
);

-- Receipts identify verified, resolved events without retaining provider payloads.
CREATE TABLE billing_event_receipts (
    organization_id text NOT NULL,
    id text NOT NULL CHECK (id <> '' AND id = btrim(id)),
    provider text NOT NULL,
    provider_scope text NOT NULL,
    provider_event_id text NOT NULL CHECK (btrim(provider_event_id) <> ''),
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, id),
    UNIQUE (provider, provider_scope, provider_event_id),
    FOREIGN KEY (organization_id, provider, provider_scope)
        REFERENCES billing_provider_customers(organization_id, provider, provider_scope)
);

CREATE TABLE billing_subscription_history (
    organization_id text NOT NULL,
    subscription_id text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    plan_key text NOT NULL,
    plan_version integer NOT NULL,
    state text NOT NULL CHECK (state <> '' AND state = btrim(state)),
    event_receipt_id text,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, subscription_id, version),
    UNIQUE (organization_id, subscription_id, event_receipt_id),
    FOREIGN KEY (organization_id, subscription_id) REFERENCES billing_subscriptions(organization_id, id),
    FOREIGN KEY (organization_id, event_receipt_id) REFERENCES billing_event_receipts(organization_id, id),
    FOREIGN KEY (plan_key, plan_version) REFERENCES billing_plan_versions(plan_key, version)
);

CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON billing_plan_versions
    FOR EACH STATEMENT EXECUTE FUNCTION audit_immutable();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON billing_event_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION audit_immutable();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON billing_subscription_history
    FOR EACH STATEMENT EXECUTE FUNCTION audit_immutable();

-- The application has no billing access policies in this release. RLS keeps
-- ordinary runtime roles out even when the migration owner has default grants.
ALTER TABLE billing_plan_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_provider_customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_provider_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_event_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_subscription_history ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON billing_plan_versions, billing_customers, billing_subscriptions,
    billing_provider_customers, billing_provider_subscriptions, billing_event_receipts,
    billing_subscription_history FROM PUBLIC;
