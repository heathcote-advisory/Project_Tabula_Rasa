CREATE TABLE customer_policy_acknowledgements (
    user_id text NOT NULL REFERENCES identity_users(id),
    bundle_sha256 text NOT NULL CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
    policy_version text NOT NULL,
    acknowledged_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id,bundle_sha256)
);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON customer_policy_acknowledgements
    FOR EACH STATEMENT EXECUTE FUNCTION audit_immutable();
REVOKE ALL ON customer_policy_acknowledgements FROM PUBLIC;
