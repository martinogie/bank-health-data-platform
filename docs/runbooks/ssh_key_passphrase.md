# Runbook: a passphrase on the VM's SSH key

Phase 4, PR 10. The key `~/.ssh/bank_health_vm` (on Martins' PC) opens both SSH logins to the VM:
`bank-health-vm` (deploy) and `bank-health-vm-admin` (ubuntu, sudo). Today it has no passphrase, so
anyone who copied the file could log in. Adding one keeps the **same key**: nothing changes on the
VM, in GitHub or in OCI. **Martins does every step; nobody else handles the key.**

**Decision (6 Nov 2026):** Martins chose not to add a passphrase for now; the key stays protected by
his Windows account. This runbook is kept for when that changes.

## Steps (PowerShell on the PC; ~5 minutes)

1. **Start the Windows ssh-agent and keep it on** (PowerShell *as administrator*), so the passphrase
   is typed once, not at every login:
   ```powershell
   Set-Service ssh-agent -StartupType Automatic
   Start-Service ssh-agent
   ```
2. **Add the passphrase** (normal PowerShell). Choose a strong one and save it in your password
   manager first. It asks for the old passphrase (press Enter: there is none) and the new one twice:
   ```powershell
   & "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe" -p -f "$HOME\.ssh\bank_health_vm"
   ```
3. **Give the key to the agent** (asks for the passphrase once; the agent keeps it for your Windows
   user, also after a restart):
   ```powershell
   & "$env:WINDIR\System32\OpenSSH\ssh-add.exe" "$HOME\.ssh\bank_health_vm"
   ```
4. **Check** (no passphrase prompt; prints `ok`):
   ```powershell
   & "$env:WINDIR\System32\OpenSSH\ssh.exe" -o BatchMode=yes bank-health-vm "echo ok"
   ```

## What else changes

- **Git Bash's `ssh`** (`/usr/bin/ssh`) can't use the Windows agent: it will ask for the passphrase.
  Use the Windows one in scripts and in Claude's checks: `/c/Windows/System32/OpenSSH/ssh.exe`
  (and `scp.exe`). VS Code's remote features already use the Windows one.
- **Deploys don't change:** GitHub's runner runs on the VM itself and doesn't use this key.
- The OCI console's cloud shell stays the way in if the PC is lost (see SECURITY.md, "If something
  leaks").

## Undo

`ssh-keygen -p -f "$HOME\.ssh\bank_health_vm"` again, with the current passphrase and an empty new
one. The key stays the same, so the VM needs nothing.

## Trade-off

The agent keeps the key usable while you are logged in to Windows. The passphrase protects a copied
file (a backup, a stolen disk), not a session that is already open.
