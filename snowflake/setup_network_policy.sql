-- Phase 4, PR 7: SVC_BANK_PIPELINE may log in only from the prod VM's reserved IP and, until the PC
-- rollback window ends (20 Nov 2026), Martins' home IP. Run by Martins as ACCOUNTADMIN AFTER the
-- VM has its reserved IP (docs/runbooks/network_policy.md) and Claude has confirmed the VM's
-- outgoing address is that IP.
--
-- Replace the two placeholders before running (the repo is public, so the real addresses aren't
-- written here):
--   <VM_RESERVED_IP>  the VM's new reserved public IP
--   <HOME_IP>         Martins' home IP
--
-- Only this one user gets a policy (SECURITY.md explains why not the others). Before it was
-- written, SVC_BANK_PIPELINE's 6-day login history held exactly two addresses: the VM's and the
-- home IP. Martins' own login, Power BI (SVC_POWERBI), CI (SVC_BANK_CI) and dev (SVC_BANK_DEV)
-- are not affected.

USE ROLE ACCOUNTADMIN;

-- 1. The allowed addresses, as a network rule (kept with the other control objects)
CREATE NETWORK RULE IF NOT EXISTS BANK_HEALTH.CONTROL.PIPELINE_ALLOWED_IPS
  MODE = INGRESS
  TYPE = IPV4
  VALUE_LIST = ('<VM_RESERVED_IP>', '<HOME_IP>')
  COMMENT = 'SVC_BANK_PIPELINE: the prod VM reserved IP; the home IP until 20 Nov 2026 (PC rollback)';

-- 2. The policy, attached to the pipeline user only
CREATE NETWORK POLICY IF NOT EXISTS SVC_BANK_PIPELINE_POLICY
  ALLOWED_NETWORK_RULE_LIST = ('BANK_HEALTH.CONTROL.PIPELINE_ALLOWED_IPS')
  COMMENT = 'Phase 4 PR 7: the pipeline user logs in from the prod VM only (and home until 20 Nov)';
ALTER USER SVC_BANK_PIPELINE SET NETWORK_POLICY = SVC_BANK_PIPELINE_POLICY;

-- Checks
DESC NETWORK RULE BANK_HEALTH.CONTROL.PIPELINE_ALLOWED_IPS;     -- value_list: the two addresses
DESC NETWORK POLICY SVC_BANK_PIPELINE_POLICY;                   -- allowed_network_rule_list: the rule
SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN USER SVC_BANK_PIPELINE;  -- value: SVC_BANK_PIPELINE_POLICY

-- ROLLBACK (instant, any time): the pipeline user can log in from anywhere again.
--   ALTER USER SVC_BANK_PIPELINE UNSET NETWORK_POLICY;
--
-- ON 20 NOV 2026 (reminder in ops/credentials.yml): remove the home IP, keep the VM's.
--   ALTER NETWORK RULE BANK_HEALTH.CONTROL.PIPELINE_ALLOWED_IPS SET VALUE_LIST = ('<VM_RESERVED_IP>');
