# ADR 0001: Production on an Oracle Cloud Arm VM, deployed by a self-hosted GitHub runner

- **Status:** accepted (Phase 2, October 2026)
- **Findings:** O-01 (the scheduler runs on a personal PC)

## Context

Airflow ran in Docker Desktop on the owner's PC. The Monday 06:00 run happened only if the PC was
on, awake and running Docker. On 26 Oct 2026 the PC was in standby until 08:30, and the run
started 2.5 hours late. A pipeline that depends on a laptop is not production.

Constraints: a portfolio budget (free or nearly), one person operating it, the existing Docker
Compose stack, and an Airflow image that already builds for linux/arm64.

## Decision

1. **Run production on an OCI Always Free `VM.Standard.A1.Flex`** (Arm, 4 OCPU, 24 GB, Ubuntu
   24.04). The same Compose file runs prod on any machine; per-machine paths live in a git-ignored
   `airflow/.env`.
2. **Upgrade the OCI account to Pay As You Go**, with a $1 budget alert. Oracle may reclaim
   *idle* Always Free instances, and this VM is busy for about 30 minutes a week. Pay As You Go
   accounts aren't reclaimed, and the Always Free resources stay free.
3. **Access by SSH only**: port 22 from one IP. The Airflow UI listens on `127.0.0.1` and is
   reached through an SSH tunnel.
4. **Deploy with a GitHub Actions self-hosted runner on the VM**, only after CI passes on `main`
   and only with the owner's approval in the `production` environment
   ([deploy.md](../runbooks/deploy.md)).
5. **Secrets as files** (`chmod 600`, owned by the deploy user, outside the checkout), copied by
   the owner with `scp`. OCI Vault comes in Phase 4.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep the PC, disable sleep | Still one laptop: reboots, travel, power. Doesn't answer "is this production?" |
| Managed Airflow (Astronomer, Amazon MWAA, Cloud Composer) | Real cost every month; outside a portfolio budget |
| Deploy from GitHub-hosted runners over SSH | GitHub's runner addresses change and are many: port 22 would have to be open to large ranges, plus an SSH private key in GitHub secrets |
| The VM pulls `main` itself (cron) | No approval step, and no deployment record in GitHub |
| A private repository (GitHub's advice for self-hosted runners) | The repository is the portfolio: it must stay public |

## Consequences

- **The public-repository risk** of a self-hosted runner is reduced, not removed. Fork workflow
  runs need approval ("never approve a workflow run from a fork"). The deploy workflow has no
  `pull_request` trigger. The runner user has no Docker access, can't read secrets, and may run
  only `deploy.sh`, which only fast-forwards to the current `main`. Whoever controls the GitHub
  account controls the VM, so two-factor authentication matters. Fast removal is in the runbook.
- **One scheduler at a time** is enforced by `OPS.PROD_SCHEDULER`, not only by procedure
  ([cutover_and_rollback.md](../runbooks/cutover_and_rollback.md)).
- **A down VM can't report itself**, so a GitHub Actions watchdog checks the run log from outside
  every Monday ([ADR 0002](0002-alert-channels-slack-and-discord.md)).
- **The home region is permanent**, and A1 capacity can be scarce; the runbook covers retrying.
- **Proven when** three Monday runs in a row succeed on the VM with the PC switched off.
