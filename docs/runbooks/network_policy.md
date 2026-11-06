# Runbook: reserved IP for the VM and the Snowflake network policy

Phase 4, PR 7. Goal: the pipeline's Snowflake user, `SVC_BANK_PIPELINE`, can log in only from the
prod VM (and, until 20 Nov 2026, from Martins' home IP for the PC rollback). That needs a VM address
that never changes: a **reserved** public IP instead of the ephemeral one it had.

Price: **$0**. Oracle's live price list (checked 6 Nov 2026) has no charge item for public IP
addresses; the $5 budget alert stays as the backstop.

## 1. Reserve an IP and move the VM onto it (Martins, OCI console; ~5 minutes)

Not on a Monday morning; no run in progress (Claude checks before you start). The VM keeps running;
only its public address changes. For about a minute between steps c and d the VM has no public
address: SSH drops, and the GitHub runner reconnects by itself afterwards.

a. **Networking → IP management → Reserved public IPs → Reserve public IP address.**
   Name `bank-health-prod-ip`, compartment `mogiekpo (root)`, *Create a new reserved IP address
   from an Oracle IP pool*. **Reserve.** Note the address.
b. **Compute → Instances → `bank-health-prod` → Attached VNICs → `bank-health-prod-vnic` →
   IP administration** (IPv4 addresses): the primary private IP `10.0.0.156`.
c. On that row: **⋮ → Edit → Public IP type: No public IP → Update.** (The old ephemeral address
   is released.)
d. Same row again: **⋮ → Edit → Public IP type: Reserved public IP → Select existing →
   `bank-health-prod-ip` → Update.**
e. **Your SSH config** (`C:\Users\marti\.ssh\config`): set `HostName` to the new address for
   **both** `bank-health-vm` and `bank-health-vm-admin`. Then `ssh bank-health-vm "echo ok"`: the
   first connection asks to trust the host key again (the key didn't change, the address did):
   answer `yes`.
f. Tell Claude the new address. Claude checks the VM's outgoing address is the reserved one and
   that the runner is back online.

**Rollback:** nothing to undo on Snowflake yet. If the VM can't be reached, the OCI console's
cloud shell or serial console still works; the reserved IP can be unassigned and an ephemeral one
assigned again the same way.

## 2. The network policy (Martins, Snowsight as ACCOUNTADMIN; ~2 minutes)

Run [`snowflake/setup_network_policy.sql`](../../snowflake/setup_network_policy.sql) with the two
placeholders replaced (`<VM_RESERVED_IP>`, `<HOME_IP>`). Claude then checks from the VM that the
pipeline logs in (no Databricks).

**Rollback (instant):** `ALTER USER SVC_BANK_PIPELINE UNSET NETWORK_POLICY;`

## 3. On 20 Nov 2026: remove the home IP

The PC rollback window ends (`ops/credentials.yml` reminds 14 days ahead). As ACCOUNTADMIN:

```sql
ALTER NETWORK RULE BANK_HEALTH.CONTROL.PIPELINE_ALLOWED_IPS SET VALUE_LIST = ('<VM_RESERVED_IP>');
```

## If a login is refused

The error says the IP is not allowed (`Incoming request with IP/Token ... is not allowed to access
Snowflake`). Check where it came from (`LOGIN_HISTORY`, `CLIENT_IP`). If the VM's address changed,
update the rule; to unblock at once, `UNSET NETWORK_POLICY` (above) and fix calmly.
