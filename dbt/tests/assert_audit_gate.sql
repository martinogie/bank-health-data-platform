-- Fault injection for write, audit, publish (dev checks only). Normally returns nothing and passes.
-- With --vars '{"marts_schema": "MARTS_NEXT", "audit_fail_for_test": true}' it fails, like any
-- critical test, to show that a failing test stops the publish: MARTS keeps last week's marts.
-- The pipeline never sets this var (its dbt vars are fixed in the DAG), so it can't fire in prod.
select 'forced failure (audit_fail_for_test)' as problem
from {{ ref('mart_early_warning') }}
where {{ 'true' if var('audit_fail_for_test', false) else 'false' }}
limit 1
