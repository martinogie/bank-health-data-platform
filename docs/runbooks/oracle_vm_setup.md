# Runbook: the Oracle Cloud VM for production

Production Airflow moves from the PC to an Oracle Cloud Infrastructure (OCI) Always Free Arm VM.
This runbook sets the machine up once. Deploys are in [deploy.md](deploy.md); the move itself
(one scheduler at a time) gets its own runbook in the cutover step.

You do every step here yourself: the OCI console, the SSH key, copying secrets. Nothing secret
is ever pasted into chat, a commit or a log.

**Until cutover the VM only runs smoke checks. The DAG stays paused on the VM**: the PC is still
production. Never unpause it on the VM before the cutover runbook says so.

| | |
|---|---|
| Shape | `VM.Standard.A1.Flex`, 4 OCPU / 24 GB (all of the Always Free Arm allowance) |
| Image | Canonical Ubuntu 24.04 (aarch64, picked automatically for an Arm shape) |
| Boot volume | 100 GB (the Always Free allowance is 200 GB of block storage in total) |
| Inbound | TCP 22 from your IP only. Nothing else, ever |
| Airflow UI | `127.0.0.1:8080` on the VM, reached through an SSH tunnel |
| Users | `ubuntu` (admin, sudo), `deploy` (runs the stack), `gh-runner` (GitHub runner, [deploy.md](deploy.md)) |
| Layout | `/srv/bank-health/app` (git checkout), `/srv/bank-health/secrets` (`.env`, `keys/`), `/srv/bank-health/var` (data, logs, watchlist) |

## 1. Account, region and billing

1. **Account.** Sign up at <https://www.oracle.com/cloud/free/> (or sign in).
2. **Home region: choose carefully, it can't be changed.** Always Free compute (including the
   A1 Arm shape) can only be created in your home region. Pick one near Texas, e.g.
   *US South (Dallas)* or *US West (Phoenix)*. A busy region may often be out of A1 capacity
   (step 3.4).
3. **Upgrade to Pay As You Go (your decision: yes).**
   - **Why:** Oracle may reclaim *idle* Always Free compute instances in free-tier accounts. Pay
     As You Go accounts aren't reclaimed, and they keep every Always Free resource at no cost.
     This VM is busy for about 30 minutes a week, so it would look idle.
   - **The rule today**, from Oracle's "Always Free Resources" page: an instance is idle if,
     over 7 days, its 95th-percentile CPU use is below 20%, network use is below 20%, and
     (A1 shapes only) memory use is below 20%. Check the current page, because the thresholds
     may change.
   - **Steps:** Console → **☰ → Billing & Cost Management → Upgrade and Manage Payment →
     Upgrade to Pay As You Go**. Add the card. Oracle may place a small temporary hold on it.
   - **What could cost money:** only resources above the Always Free limits, e.g. a second VM
     or more than 200 GB of disk. The budget alert below catches that.
4. **$1 budget alert.** Console → **☰ → Billing & Cost Management → Budgets → Create Budget**:
   - Name `bank-health-1usd`; target: compartment, your root compartment.
   - Schedule: monthly; budget amount `1` (USD).
   - Alert rule: type **Actual spend**, threshold **100%** of the budget, your email.
   - Add a second rule: **Forecast spend**, threshold **100%**, the same email.

## 2. SSH key (on your PC)

In PowerShell:

```powershell
ssh-keygen -t ed25519 -C "bank-health-vm" -f $HOME\.ssh\bank_health_vm
```

Choose a passphrase when asked (you type it; nobody else sees it). This creates
`bank_health_vm` (private, stays on your PC) and `bank_health_vm.pub` (public, pasted into
OCI in step 3.4). Open the `.pub` file in Notepad to copy it.

## 3. Network and VM (OCI console)

1. **Your public IP.** Open <https://ifconfig.me> in a browser. Note it, e.g. `203.0.113.7`.
2. **Network.** **☰ → Networking → Virtual Cloud Networks → Start VCN Wizard → Create VCN with
   Internet Connectivity**. Name `bank-health-vcn`, keep the defaults, **Create**.
3. **Firewall: port 22, your IP only.** Open `bank-health-vcn` → **Security Lists → Default
   Security List for bank-health-vcn → Security rules (Ingress)**. Edit the rule for
   **TCP, destination port 22, source 0.0.0.0/0**, and change the source CIDR to `<your IP>/32`.
   Leave the two ICMP rules (they open no ports; they let path-MTU discovery work). Add no other
   ingress rule: no 8080, no 443, nothing.
4. **The VM.** **☰ → Compute → Instances → Create instance**:
   - Name `bank-health-prod`, the root compartment.
   - **Image:** *Change image* → **Canonical Ubuntu 24.04** (not "Minimal").
   - **Shape:** *Change shape* → **Ampere** → `VM.Standard.A1.Flex` → **4 OCPUs, 24 GB**.
   - **Networking:** `bank-health-vcn`, its **public** subnet, *Assign a public IPv4 address*.
   - **SSH keys:** *Paste public keys* → the contents of `bank_health_vm.pub`.
   - **Boot volume:** *Specify a custom boot volume size* → **100 GB**.
   - **Create.** If it says **"Out of capacity"**, try another *availability domain* in the
     placement section, or try again later (A1 capacity frees up through the day).
5. Wait until the state is **Running**. Note the **Public IPv4 address**.
6. **If your home IP changes** (most home connections change now and then), SSH stops working
   from your PC. Update the source CIDR of the port-22 rule (step 3.3) to the new IP.

## 4. First login and SSH config (on your PC)

Add this to `C:\Users\marti\.ssh\config` (create the file if needed). `<VM IP>` is the address
from step 3.5:

```
Host bank-health-vm-admin
  HostName <VM IP>
  User ubuntu
  IdentityFile ~/.ssh/bank_health_vm

Host bank-health-vm
  HostName <VM IP>
  User deploy
  IdentityFile ~/.ssh/bank_health_vm
  # Airflow UI through the tunnel. Until cutover the PC's own Airflow uses 8080, so the VM's UI
  # is on http://localhost:8090; after cutover you may change 8090 to 8080.
  LocalForward 8090 localhost:8080
```

Log in as the admin user:

```powershell
ssh bank-health-vm-admin
```

## 5. Base system (as `ubuntu`, on the VM)

```bash
# time zone: logs and the 03:00 reboot window in Chicago time
sudo timedatectl set-timezone America/Chicago

# updates
sudo apt-get update && sudo apt-get -y full-upgrade

# host firewall: OCI's Ubuntu image already allows only SSH inbound (iptables rules in
# /etc/iptables/rules.v4). Check it; don't enable ufw on top (it conflicts with those rules).
sudo iptables -L INPUT -n --line-numbers   # expect: ACCEPT tcp dpt:22 ... then REJECT all

# SSH: keys only, no root login (OCI's defaults; check them)
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '
#   expect: passwordauthentication no, permitrootlogin no (or prohibit-password)
```

**Swap file (4 GB)**: a safety net for memory peaks during image builds:

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # Swap: 4.0Gi
```

**Automatic security updates.** Ubuntu installs and enables `unattended-upgrades`. Allow it
to reboot when an update needs it, at 03:00 Chicago time. A Monday 03:00 reboot finishes long
before the 06:00 run, and the stack starts again by itself:

```bash
sudo apt-get install -y unattended-upgrades
printf '%s\n' 'Unattended-Upgrade::Automatic-Reboot "true";' \
  'Unattended-Upgrade::Automatic-Reboot-Time "03:00";' | sudo tee /etc/apt/apt.conf.d/52bank-health-reboot
sudo unattended-upgrade --dry-run --debug 2>&1 | tail -3   # runs without errors
```

## 6. Docker Engine and the Compose plugin

From Docker's own apt repository:

```bash
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# start on boot
sudo systemctl enable --now docker containerd
docker --version && docker compose version
```

Note: ports that Docker publishes skip the host's INPUT firewall rules. That is why the
Compose file publishes the Airflow UI on `127.0.0.1` only: even a firewall mistake can't expose it.

## 7. The `deploy` user and the folders

```bash
# deploy runs the stack. Being in the docker group is equivalent to root on this machine,
# so only you (by SSH key) log in as deploy.
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
sudo install -m 600 -o deploy -g deploy /home/ubuntu/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys

# layout: app (git checkout), secrets (outside the checkout), var (data, logs, watchlist)
sudo install -d -m 755 -o deploy -g deploy /srv/bank-health /srv/bank-health/var \
  /srv/bank-health/var/data /srv/bank-health/var/logs /srv/bank-health/var/site
sudo install -d -m 700 -o deploy -g deploy /srv/bank-health/secrets /srv/bank-health/secrets/keys
```

Log out, then log in as `deploy` (from your PC: `ssh bank-health-vm`) and get the code. The
repository is public, so no credentials are needed:

```bash
git clone https://github.com/martinogie/bank-health-data-platform.git /srv/bank-health/app
```

Tell Compose where this machine keeps things. This file is git-ignored and holds paths only,
no secrets:

```bash
cat > /srv/bank-health/app/airflow/.env <<EOF
BANK_HEALTH_ENV_FILE=/srv/bank-health/secrets/.env
BANK_HEALTH_KEYS_DIR=/srv/bank-health/secrets/keys
BANK_HEALTH_DATA_DIR=/srv/bank-health/var/data
AIRFLOW_LOGS_DIR=/srv/bank-health/var/logs
WATCHLIST_DIR=/srv/bank-health/var/site
AIRFLOW_UID=$(id -u)
EOF
cat /srv/bank-health/app/airflow/.env
```

## 8. Secrets: you copy them (on your PC)

Copy the prod `.env` and the **encrypted** Snowflake prod key. The Power BI key stays on the PC.
In PowerShell on your PC:

```powershell
scp C:\Users\marti\projects\bank-health-data-platform\.env bank-health-vm:/srv/bank-health/secrets/.env
scp C:\Users\marti\projects\bank-health-data-platform\keys\snowflake_rsa_key.p8 bank-health-vm:/srv/bank-health/secrets/keys/
ssh bank-health-vm "chmod 600 /srv/bank-health/secrets/.env /srv/bank-health/secrets/keys/snowflake_rsa_key.p8 && ls -l /srv/bank-health/secrets /srv/bank-health/secrets/keys"
```

Expected: both files are `-rw------- deploy deploy`.

Give the VM its machine name (not a secret). The copied `.env` may not end with a newline, so
add one first if it's missing; otherwise the new line is glued to the last one:

```powershell
ssh bank-health-vm "f=/srv/bank-health/secrets/.env; grep -q '^PROD_HOST_ID=' \$f || { [ -z \"\$(tail -c1 \$f)\" ] || echo >> \$f; echo PROD_HOST_ID=oracle-vm >> \$f; }; grep -c '^PROD_HOST_ID=' \$f"
```

Expected output: `1`. The `.env` keeps `BANK_HEALTH_ENV=prod`
and `SNOWFLAKE_PRIVATE_KEY_PATH=/opt/airflow/keys/snowflake_rsa_key.p8`; the path is the same
inside the container on both machines. Don't edit secrets on the VM in chat or in logs. To
change one, edit it on the VM with `nano`, or copy a new file.

## 9. First start (smoke check only; the DAG stays paused)

As `deploy` on the VM:

```bash
cd /srv/bank-health/app/airflow
docker compose build                  # native arm64 build, about 5 minutes
docker compose up airflow-init        # creates the metadata database and the admin user
docker compose up -d --wait
# dbt packages aren't in git: install them once (deploy.sh repeats this on every deploy).
# Until they exist the DAG fails to import with "missing dbt_packages".
docker compose exec airflow-scheduler bash -c 'cd /opt/airflow/dbt && /home/airflow/dbt_venv/bin/dbt deps --profiles-dir .'
# the DAG processor re-reads files every 2 minutes; give it that long
docker compose ps                     # every service "healthy"
docker compose logs airflow-scheduler | grep 'Bank Health Airflow'
#   Bank Health Airflow: environment=prod snowflake_database=BANK_HEALTH databricks_catalog=bank_health ...
docker compose exec airflow-scheduler airflow dags list-import-errors   # "No data found"
docker compose exec airflow-scheduler airflow dags details bank_health_weekly | grep is_paused
#   is_paused  True   <- must stay True until cutover
# On this fresh database the scheduler's next run date is the last missed Monday: unpausing
# would start a prod run AT ONCE. Never unpause here; cutover brings the PC's database.
```

The UI: on your PC, `ssh bank-health-vm` (leave the window open), then browse to
<http://localhost:8090>. Sign in with the admin user and password from the `.env`.

## 10. Acceptance checks for this step

Run these from your PC (PowerShell) and on the VM:

| Check | Command | Expected |
|---|---|---|
| SSH works | `ssh bank-health-vm "hostname; whoami"` | the VM's hostname (OCI names it after the VNIC, e.g. `bank-health-prod-vnic`), `deploy` |
| Only 22 is reachable | `Test-NetConnection <VM IP> -Port 22`, then `-Port 8080` | 22: `TcpTestSucceeded : True`; 8080: `False` |
| SSH only from your IP | from your phone's hotspot (a different IP): `Test-NetConnection <VM IP> -Port 22` | `False` |
| UI only through the tunnel | with the tunnel open: <http://localhost:8090>; with it closed: fails | |
| Survives a reboot | `ssh bank-health-vm-admin "sudo reboot"`, wait 2 minutes, then `ssh bank-health-vm "cd /srv/bank-health/app/airflow && docker compose ps"` | every service healthy, nothing started by hand |
| Environment logged | `docker compose logs airflow-scheduler \| grep 'environment=prod'` | the line from step 9 |
| DAG still paused | step 9's last command | `True` |

## Notes

- **Stop vs delete.** `docker compose stop` keeps everything, and services stay stopped after a
  reboot (`restart: unless-stopped`). `docker compose down` removes the containers but keeps the
  metadata database volume. `docker compose down -v` **deletes the metadata database**
  (watermarks, run history): never run it on prod.
- **Disk.** `df -h /` and `docker system df` now and then; `docker builder prune` frees build cache.
- **The VM's public IP** stays the same while the instance exists. If you ever terminate and
  recreate it, update `~/.ssh/config`.
