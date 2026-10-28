# ADR 0002: Alerts to both Slack and Discord through incoming webhooks

- **Status:** accepted (Phase 2, October 2026)
- **Findings:** O-02 (no alerts when something fails or is late), O-04 (quota errors)

## Context

Before Phase 2 the DAG had retries but told no one when it broke. The question "how would you
know?" had no answer. Alerts must reach the owner wherever they are, must never break the
pipeline themselves, and must never leak a secret into a log.

## Decision

- **Every alert goes to both a Slack channel and a Discord channel**, through an incoming webhook
  for each: the same message, sent by one helper, `ingestion/alerts.py:notify()`.
- **Dev and prod use separate webhooks and separate channels** (`#bank-health-dev`,
  `#bank-health-prod`), each pair in that environment's `.env`.
- **Failure handling:** if one channel fails, the other is still sent, and a warning names the
  failed channel. `notify()` never raises, so an alert can't fail a task.
- **Secrets:** webhook URLs are masked in every log line and message.
- **What alerts:**
  - a task failing after its last retry;
  - the freshness and volume checks;
  - a run not successful by Monday 12:00 CT, checked twice: by a DAG on the VM, and by a GitHub
    Actions watchdog that still works when the VM is down;
  - one **success** message per run, so silence never means "it ran fine".

## Alternatives considered

| Option | Why not (alone) |
|---|---|
| Email (Gmail SMTP with an app password) | More moving parts (SMTP settings, app password), easy to filter into spam, slow to notice |
| Slack only, or Discord only | One provider's outage or a muted channel silences everything; two independent channels cost nothing |
| Bot tokens or Airflow's Slack/Discord providers | More permission than needed: a webhook can only post to its one channel, so a leaked webhook can't read anything. No extra provider in the image |

## Consequences

- Four webhooks to create and keep ([alerts.md](../runbooks/alerts.md)); the prod pair is also
  stored as GitHub secrets for the watchdog.
- **Keeping dev out of the prod channel** relies on separate `.env` files plus a visible
  environment tag on every message (`[DEV]`, `[PROD]`). There is no automatic check that a dev
  `.env` doesn't hold a prod URL: a webhook URL doesn't reveal its channel.
- **Recognising a quota stop** relies on matching phrases, because Databricks doesn't document its
  quota error. The phrases live in one tested place.
- **GitHub disables scheduled workflows** in public repositories after 60 days without activity:
  the watchdog must be re-enabled if that happens (runbook).
