<#
Snowflake Terraform, run by Martins in his own terminal (his encrypted key pair on his own user).

  .\infra\terraform\snowflake\plan.ps1 -Generate   # first time only: write generated.tf from the live account
  .\infra\terraform\snowflake\plan.ps1             # plan -> tfplan, plan.txt, plan.json, guard result
  .\infra\terraform\snowflake\plan.ps1 -Apply      # apply the SAVED tfplan, only after Martins approved it

-Apply never makes a new plan: it re-runs the guard on the saved tfplan and applies exactly that
file (Terraform refuses it if anything changed since). Output in apply.txt.
The key's passphrase goes from a hidden prompt into SNOWFLAKE_PRIVATE_KEY_PASSPHRASE for this one
run and is removed at the end (also on errors); it is never written to disk or printed. The key
file's path is in the git-ignored terraform.tfvars.
#>
param([switch]$Generate, [switch]$Apply)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$repo = Resolve-Path (Join-Path $here '..\..\..')
$guard = Join-Path $repo 'scripts\tf_guard.py'
$tf = (Get-Command terraform -ErrorAction SilentlyContinue).Source
if (-not $tf) {
  $tf = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter terraform.exe -Recurse |
        Select-Object -First 1 -ExpandProperty FullName
}

Push-Location $here
try {
  if (-not (Test-Path '.terraform')) { & $tf init '-backend-config=backend.hcl' '-input=false' | Out-Null }

  # Through cmd: PowerShell 5.1 turns a native program's stderr into a stopping error and writes
  # UTF-16; cmd keeps Terraform's full output (errors included) as plain text.
  $ErrorActionPreference = 'Continue'

  if ($Apply) {
    if (-not (Test-Path 'tfplan')) { Write-Host 'No saved tfplan: run plan.ps1 first.'; return }
    cmd /c "`"$tf`" show -json tfplan > plan.json"
    python $guard plan.json
    if ($LASTEXITCODE -ne 0) { Write-Host 'Guard FAILED on the saved plan: NOT applying.'; return }
  }

  $secure = Read-Host 'Passphrase of your Terraform key (hidden)' -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  $env:SNOWFLAKE_PRIVATE_KEY_PASSPHRASE = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

  if ($Apply) {
    cmd /c "`"$tf`" apply -input=false -no-color tfplan > apply.txt 2>&1"
    Write-Host "apply: exit $LASTEXITCODE (full text in apply.txt)"
    Get-Content apply.txt | Select-String 'Apply complete|Error' | ForEach-Object { Write-Host $_ }
    Remove-Item tfplan, plan.json -ErrorAction SilentlyContinue
  } elseif ($Generate) {
    cmd /c "`"$tf`" plan -input=false -no-color -generate-config-out=generated.tf > plan.txt 2>&1"
    Write-Host "generate: exit $LASTEXITCODE (output in plan.txt; generated.tf written if 0)"
  } else {
    cmd /c "`"$tf`" plan -input=false -no-color -out=tfplan > plan.txt 2>&1"
    $code = $LASTEXITCODE
    Write-Host "plan: exit $code (full text in plan.txt)"
    if ($code -eq 0) {
      cmd /c "`"$tf`" show -json tfplan > plan.json"
      python $guard plan.json
    }
  }
}
finally {
  Remove-Item Env:SNOWFLAKE_PRIVATE_KEY_PASSPHRASE -ErrorAction SilentlyContinue
  Pop-Location
}
