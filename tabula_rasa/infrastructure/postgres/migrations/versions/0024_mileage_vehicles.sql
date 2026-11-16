LOCK TABLE mileage_records IN SHARE ROW EXCLUSIVE MODE;
-- Only this maintenance transaction may backfill metadata in closed books.
-- Re-enable the existing guard before commit; failure rolls back its state too.
ALTER TABLE mileage_records DISABLE TRIGGER customer_closure_guard;
ALTER TABLE mileage_records DISABLE TRIGGER bookkeeping_cutover_guard;
CREATE TABLE vehicles (
    entity_id text NOT NULL REFERENCES business_entities(id),
    id text NOT NULL CHECK (btrim(id) <> ''),
    incarnation_id text NOT NULL CHECK (btrim(incarnation_id) <> ''),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    name text NOT NULL CHECK (btrim(name) <> ''),
    name_key text NOT NULL CHECK (name_key=name),
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY(entity_id,id), UNIQUE(entity_id,name_key)
);
ALTER TABLE mileage_records ADD COLUMN vehicle_id text;
ALTER TABLE mileage_records ADD CONSTRAINT fk_mileage_vehicle
    FOREIGN KEY(entity_id,vehicle_id) REFERENCES vehicles(entity_id,id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX mileage_vehicle_reference ON mileage_records(entity_id,vehicle_id);

CREATE TRIGGER advance_version BEFORE UPDATE ON vehicles
    FOR EACH ROW EXECUTE FUNCTION advance_aggregate_version();
CREATE CONSTRAINT TRIGGER audit_capture AFTER INSERT OR UPDATE OR DELETE ON vehicles
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION capture_bookkeeping_audit('id');
SELECT set_config('tabula.audit_context',jsonb_build_object(
    'actor_type','system','actor_id','schema-migration','source','migration.vehicles',
    'operation_id',gen_random_uuid()::text,'correlation_id','','entity_id','')::text,true);

-- Exact comparison deliberately does not fold case, spacing, or punctuation.
-- Repeated labels share one stable identity; similar labels remain separate.
INSERT INTO vehicles(entity_id,id,incarnation_id,name,name_key)
    SELECT DISTINCT entity_id,'vehicle-' || md5(entity_id || ':' || vehicle),
        gen_random_uuid()::text,vehicle,vehicle FROM (
            SELECT DISTINCT entity_id,vehicle FROM mileage_records WHERE btrim(vehicle) <> ''
        ) historical ON CONFLICT (entity_id,name_key) DO NOTHING;
-- This explicit one-time backfill changes no numeric field or source label.
-- Existing audit capture records its trusted system attribution.
UPDATE mileage_records m SET vehicle_id=v.id FROM vehicles v
    WHERE v.entity_id=m.entity_id AND v.name_key=m.vehicle AND m.vehicle_id IS NULL;

-- Even an unassigned trip gains a persisted field. Invalidate old loaded
-- baselines and capture the new-schema digest without changing source text.
UPDATE mileage_records SET version=version+1 WHERE vehicle_id IS NULL;
-- Complete deferred integrity/audit events before changing trigger metadata.
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE mileage_records ENABLE TRIGGER customer_closure_guard;
ALTER TABLE mileage_records ENABLE TRIGGER bookkeeping_cutover_guard;
SET CONSTRAINTS ALL DEFERRED;
CREATE TRIGGER customer_closure_guard BEFORE INSERT OR UPDATE OR DELETE ON vehicles
    FOR EACH ROW EXECUTE FUNCTION guard_customer_closure();
CREATE TRIGGER bookkeeping_cutover_guard BEFORE INSERT OR UPDATE OR DELETE ON vehicles
    FOR EACH ROW EXECUTE FUNCTION guard_bookkeeping_cutover();
