-- Every legacy subscription and provider record depends on billing_customers.
-- Stop if private/operator-written records require an explicit mapping.
LOCK TABLE billing_customers IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE billing_table text;
BEGIN
    IF EXISTS (SELECT 1 FROM billing_customers) THEN
        RAISE EXCEPTION 'Billing customer records require explicit payer mapping' USING ERRCODE = '23514';
    END IF;
    FOREACH billing_table IN ARRAY ARRAY[
        'billing_customers', 'billing_subscriptions', 'billing_provider_customers',
        'billing_provider_subscriptions', 'billing_event_receipts', 'billing_subscription_history'
    ] LOOP
        EXECUTE format('ALTER TABLE %I RENAME COLUMN organization_id TO billing_account_id', billing_table);
    END LOOP;
END $$;

ALTER TABLE billing_customers
    DROP CONSTRAINT billing_customers_organization_id_fkey,
    ADD FOREIGN KEY (billing_account_id) REFERENCES billing_accounts(id);

ALTER TABLE billing_subscriptions
    ADD UNIQUE (billing_account_id),
    ADD COLUMN version bigint NOT NULL CHECK (version > 0),
    ADD COLUMN term_ends_at timestamptz,
    ADD COLUMN first_failed_renewal_at timestamptz,
    ADD CONSTRAINT billing_subscription_terms CHECK (
        (state = 'free' AND term_ends_at IS NULL AND first_failed_renewal_at IS NULL)
        OR (state IN ('trial', 'paid', 'ending') AND term_ends_at IS NOT NULL AND first_failed_renewal_at IS NULL)
        OR (state = 'grace' AND term_ends_at IS NOT NULL AND first_failed_renewal_at IS NOT NULL)
    );

ALTER TABLE billing_subscription_history
    ADD COLUMN term_ends_at timestamptz,
    ADD COLUMN first_failed_renewal_at timestamptz;

CREATE TABLE billing_link_requests (
    id text PRIMARY KEY CHECK (id <> '' AND id = btrim(id)),
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    organization_id text NOT NULL REFERENCES organization_entities(organization_id),
    requested_by text NOT NULL REFERENCES identity_users(id),
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    approved_by text REFERENCES identity_users(id),
    approved_at timestamptz,
    CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
    UNIQUE (billing_account_id, organization_id)
);

ALTER TABLE billing_link_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON billing_link_requests FROM PUBLIC;
