CREATE TABLE billing_accounts (
    id text PRIMARY KEY CHECK (id <> '' AND id = btrim(id)),
    payer_user_id text NOT NULL REFERENCES identity_users(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE billing_account_links (
    organization_id text PRIMARY KEY REFERENCES organization_entities(organization_id),
    billing_account_id text NOT NULL REFERENCES billing_accounts(id),
    linked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX billing_account_links_by_account
    ON billing_account_links (billing_account_id, linked_at, organization_id);

-- Keep both tables dormant even when runtime has default table grants.
ALTER TABLE billing_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_account_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON billing_accounts, billing_account_links FROM PUBLIC;

-- PostgreSQL stores the stable default for existing rows without firing an
-- UPDATE or advancing their authorization versions.
ALTER TABLE memberships ADD COLUMN joined_at timestamptz DEFAULT statement_timestamp();
ALTER TABLE memberships ALTER COLUMN joined_at DROP DEFAULT;
ALTER TABLE memberships ADD CONSTRAINT active_membership_has_join_time
    CHECK (state <> 'active' OR joined_at IS NOT NULL);

CREATE FUNCTION record_membership_join() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.joined_at := CASE WHEN NEW.state = 'active' THEN clock_timestamp() END;
    ELSIF NEW.state = 'active' AND OLD.state <> 'active' THEN
        NEW.joined_at := clock_timestamp();
    ELSE
        NEW.joined_at := OLD.joined_at;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER record_membership_join BEFORE INSERT OR UPDATE ON memberships
    FOR EACH ROW EXECUTE FUNCTION record_membership_join();
