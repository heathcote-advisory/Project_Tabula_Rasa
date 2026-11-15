-- Run as a database administrator on the exact bound production database.
-- Set worker_role and migration_role with psql -v; do not pass passwords.
\set ON_ERROR_STOP on
BEGIN;
CREATE SCHEMA release_control;
REVOKE ALL ON SCHEMA release_control FROM PUBLIC;

SELECT format($definition$
    CREATE FUNCTION release_control.set_worker_login(expected_role name, enabled boolean)
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog
    AS $body$
    BEGIN
        IF expected_role IS DISTINCT FROM %1$L::name OR enabled IS NULL THEN
            RAISE EXCEPTION 'Invalid worker login request';
        END IF;
        IF enabled THEN
            ALTER ROLE %1$I LOGIN;
        ELSE
            ALTER ROLE %1$I NOLOGIN;
        END IF;
    END;
    $body$
$definition$, :'worker_role')
\gexec

REVOKE ALL ON FUNCTION release_control.set_worker_login(name, boolean) FROM PUBLIC;
GRANT USAGE ON SCHEMA release_control TO :"migration_role";
GRANT EXECUTE ON FUNCTION release_control.set_worker_login(name, boolean) TO :"migration_role";
COMMIT;
