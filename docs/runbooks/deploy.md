# Runbook: deploying to the prod VM

Production changes reach the Oracle VM in one way only: a pull request is merged into `main`,
CI passes on `main`, **you approve the deployment in GitHub**, and a GitHub Actions runner on the
VM runs `deploy/deploy.sh`. No one deploys by copying files or editing code on the VM.

```
merge PR -> CI on main (all 8 checks) -> Deploy workflow waits -> you approve ("production")
        -> runner on the VM -> sudo -u deploy deploy.sh <sha>
        -> fast-forward to that sha -> docker compose build -> up -d -> smoke check
```

| Piece | Where |
|---|---|
| Workflow | [.github/workflows/deploy.yml](../../.github/workflows/deploy.yml) |
| Deploy script | [deploy/deploy.sh](../../deploy/deploy.sh), run as `deploy` |
| Smoke check | [deploy/smoke_check.sh](../../deploy/smoke_check.sh): services healthy, `environment=prod`, DAG loaded with no import errors |
| sudo rule | [deploy/sudoers.bank-health-deploy](../../deploy/sudoers.bank-health-deploy) |

`deploy.sh` refuses unless:
- it runs as `deploy`;
- the commit is exactly the current `main`;
- the checkout has no local changes;
- it can fast-forward;
- **no `bank_health_weekly` run is in progress** (restarting the scheduler would kill running
  tasks). Re-run the deploy afterwards.

## One-time setup

### A. GitHub settings (you, in the browser)

1. **Never run fork workflows without your approval.** Settings → Actions → General →
   *Approval for running fork pull request workflows from contributors* →
   **Require approval for all external contributors** → Save.
2. **The `production` environment.** Settings → Environments → New environment → `production`:
   - **Required reviewers:** add `martinogie`. Leave *Prevent self-review* **off**, so you can
     approve your own deploys.
   - **Deployment branches and tags:** *Selected branches and tags* → add the rule `main`.
3. Leave the switch off until the runner exists: the repository variable `DEPLOY_ENABLED` is
   created in step C.4.

### B. The runner user on the VM (as `ubuntu`)

```bash
# Its own user: no password, NOT in the docker group (docker access = root on this machine)
sudo adduser --disabled-password --gecos "" gh-runner
id gh-runner   # groups: gh-runner only

# The one command it may run as deploy
sudo visudo -f /etc/sudoers.d/bank-health-deploy
#   paste the line from deploy/sudoers.bank-health-deploy, save
sudo visudo -c                                    # "parsed OK"
sudo -u gh-runner sudo -n -l                      # lists only the deploy.sh rule
sudo -u gh-runner sudo -n -H -u deploy /srv/bank-health/app/deploy/deploy.sh not-a-sha
#   DEPLOY REFUSED: usage: deploy.sh <40-character commit sha>   <- the rule works, the script checks
```

The secrets stay unreadable to the runner:

```bash
sudo -u gh-runner cat /srv/bank-health/secrets/.env   # Permission denied (expected)
```

### C. Register the runner (you; the token is shown once)

1. GitHub → Settings → Actions → Runners → **New self-hosted runner** → **Linux**, **ARM64**.
   The page shows download, checksum and configure commands, with a one-time registration
   token. The token is for that page only: don't paste it anywhere else.
2. On the VM, as the runner user (`sudo -iu gh-runner`), run the page's **Download** commands
   (including the checksum check) in `~/actions-runner`. Then configure:
   ```bash
   cd ~/actions-runner
   ./config.sh --url https://github.com/martinogie/bank-health-data-platform \
     --token <token from the page> --name oracle-vm --labels prod --unattended
   ```
   GitHub adds `self-hosted`, `Linux` and `ARM64` itself; `--labels prod` adds the fourth label.
3. Install it as a service that starts on boot (back as `ubuntu`):
   ```bash
   cd /home/gh-runner/actions-runner
   sudo ./svc.sh install gh-runner
   sudo ./svc.sh start
   sudo ./svc.sh status    # active (running)
   ```
   GitHub → Settings → Actions → Runners shows `oracle-vm` as **Idle**, with labels
   `self-hosted, Linux, ARM64, prod`.
4. **Switch deploys on:** Settings → Secrets and variables → Actions → **Variables** → New
   repository variable `DEPLOY_ENABLED` = `true`.

## Deploying

- **After a merge:** CI runs on `main`. When it is green, the *Deploy* run appears in the Actions
  tab, "Waiting for review". Open it → **Review deployments** → tick `production` → **Approve and
  deploy**. The log ends with `smoke check passed` and `DEPLOYED <sha>`.
- **By hand** (e.g. after the VM was rebuilt): Actions → *Deploy* → **Run workflow** on `main`,
  then approve as above.
- **Don't approve on a Monday morning** before the weekly run has finished: the deploy would be
  refused anyway while the run is in progress.
- **A deploy failed?** The log says why (`DEPLOY REFUSED: ...` or `SMOKE CHECK FAILED: ...`).
  The stack keeps running the previous image if the build failed. If the smoke check failed,
  fix forward with a pull request, or roll back (below).

## Rolling back a bad deploy

Preferred: revert the pull request on GitHub (*Revert* button), merge the revert, approve the
deploy. In an emergency, as `deploy` on the VM:

```bash
cd /srv/bank-health/app
git log --oneline -5                       # find the last good commit
git reset --hard <good sha>                # the next normal deploy fast-forwards from here
cd airflow && docker compose build && docker compose up -d --wait && ../deploy/smoke_check.sh
```

Then fix `main` by a pull request, as usual.

## Security: why the runner never runs on pull requests

The repository is public, and GitHub advises using self-hosted runners only with private
repositories. A pull request from a fork can contain *its own* workflow file that asks for
`runs-on: [self-hosted, prod]`. If that ran, a stranger's code would run on the prod VM. The
defences:

1. **Never approve a workflow run from a fork.** With setting A.1, fork pull requests from
   external contributors don't run any workflow until you approve them. If you ever see
   "Approve and run" on someone else's pull request, don't click it. Close the pull request if
   it touches `.github/`.
2. **Our deploy workflow has no `pull_request` trigger.** `workflow_run` always uses the workflow
   file on `main`, and the job checks that the trigger was a push to `main`.
3. **The `production` environment needs your approval** and allows only `main`.
4. **The runner has almost no power.** User `gh-runner` isn't in the docker group, can't read
   `/srv/bank-health/secrets`, and can run exactly one program as `deploy`: `deploy.sh`, which
   only fast-forwards to the current `main`. Everything it deploys has passed CI and review.

What remains: someone who controls `main` (i.e. your GitHub account) controls the VM. Protect the
account with two-factor authentication.

## If the runner or the VM may be compromised: remove it fast

Do the first two steps at once. They take about two minutes.

1. **GitHub (stop new jobs):**
   - Settings → Secrets and variables → Actions → Variables → set `DEPLOY_ENABLED` to `false`.
   - Settings → Actions → Runners → `oracle-vm` → **Remove runner** (use *force remove* if it
     says the runner is busy).
   - If you suspect more: Settings → Actions → General → **Disable actions** for the
     repository until you're done.
2. **VM (as `ubuntu`):**
   ```bash
   sudo systemctl stop 'actions.runner.*'
   cd /home/gh-runner/actions-runner && sudo ./svc.sh uninstall
   sudo pkill -u gh-runner || true
   sudo rm -f /etc/sudoers.d/bank-health-deploy
   sudo deluser --remove-home gh-runner
   ```
3. **Look around:**
   - `last -a | head`
   - `sudo journalctl -u ssh --since "-7 days" | grep Accepted`
   - `sudo crontab -l; sudo -u deploy crontab -l`
   - `cat /home/*/.ssh/authorized_keys`
   - `docker ps -a` (anything you don't recognise?)
4. **Rotate the secrets** if anything beyond the runner user might have been reached:
   - the Snowflake prod key pair (new pair; `ALTER USER SVC_BANK_PIPELINE SET RSA_PUBLIC_KEY_2`,
     switch, then `UNSET RSA_PUBLIC_KEY`);
   - the Databricks token;
   - the Slack and Discord webhooks;
   - the Airflow admin password.
5. **When in doubt, rebuild:** terminate the VM and set up a new one with
   [oracle_vm_setup.md](oracle_vm_setup.md). The PC rollback stack can run prod meanwhile (the cutover runbook, added with the cutover
   step, covers switching back).
