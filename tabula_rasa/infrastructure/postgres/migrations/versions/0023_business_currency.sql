-- Retain the trigger and supported-code CHECK; only this settings command may
-- change an existing book. Admission country and approval stay unchanged.
-- Country still gates initial setup; no separate enrollment unit is retained.
ALTER TABLE beta_admissions DROP COLUMN currency;
CREATE OR REPLACE FUNCTION prevent_book_currency_change() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE context jsonb; org text;
BEGIN
    IF NEW.currency_code IS NOT DISTINCT FROM OLD.currency_code THEN RETURN NEW; END IF;
    context := NULLIF(current_setting('tabula.audit_context',true),'')::jsonb;
    IF context IS NULL OR context->>'source' IS DISTINCT FROM 'business.currency'
        OR context->>'actor_type' IS DISTINCT FROM 'user'
        OR context->>'entity_id' IS DISTINCT FROM OLD.id
        OR btrim(COALESCE(context->>'operation_id','')) = '' THEN
        RAISE EXCEPTION 'Explicit currency settings command required' USING ERRCODE='23514';
    END IF;
    SELECT e.organization_id INTO org FROM organization_entities e
        JOIN organizations o ON o.id=e.organization_id AND o.state='active'
        JOIN memberships m ON m.organization_id=o.id AND m.user_id=context->>'actor_id'
            AND m.state='active' AND m.role IN ('owner','admin')
        JOIN identity_users u ON u.id=m.user_id AND u.state='active'
        WHERE e.entity_id=OLD.id AND e.state='active';
    IF org IS NULL THEN
        RAISE EXCEPTION 'Currency authority required' USING ERRCODE='23514';
    END IF;
    NEW.version := OLD.version + 1;
    NEW.updated_at := clock_timestamp();
    INSERT INTO audit_events(entity_id,organization_id,operation_id,actor_type,actor_id,
        correlation_id,source,action,target_type,target_key,before_summary,after_summary,after_digest)
    VALUES (OLD.id,org,context->>'operation_id','user',context->>'actor_id',
        COALESCE(context->>'correlation_id',''),'business.currency','update','business_entities',
        jsonb_build_object('id',OLD.id),jsonb_build_object('currency_code',OLD.currency_code,'version',OLD.version),
        jsonb_build_object('currency_code',NEW.currency_code,'version',NEW.version),
        audit_row_digest(to_jsonb(NEW),'business_entities'));
    RETURN NEW;
END;
$$;
