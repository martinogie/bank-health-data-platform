Empty on purpose. In .env mode (dev, and prod without Vault) docker-compose.yaml mounts this
folder at /run/bank-health, so the mount always has a source. In Vault mode the host's RAM folder
/run/bank-health is mounted instead (docs/runbooks/vault.md). Never put secrets here.
