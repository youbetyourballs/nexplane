# sccm_dc_setup.ps1 — Domain Controller setup for SCCM smoke test AMI pair
# Run via AWS SSM AWS-RunPowerShellScript on Windows Server 2022 (t3.xlarge)
# Estimated runtime: 20-35 minutes (AD DS install + reboot triggered automatically)
# After reboot the instance will rejoin SSM automatically; sccm_site_setup.ps1
# must be run on the SITE SERVER instance AFTER this reboot completes.
#
# Domain: smoke.nexplane.local
# NetBIOS: SMOKE
# Safe-mode admin password: NexplaneSmoke2024!

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$ts] $Msg"
}

# ── Step 1: Set hostname ───────────────────────────────────────────────────────
Write-Log "STEP 1: Setting hostname to nexplane-smoke-dc"
if ((hostname) -ne "nexplane-smoke-dc") {
    Rename-Computer -NewName "nexplane-smoke-dc" -Force -ErrorAction SilentlyContinue
    Write-Log "Hostname set — will take effect after reboot"
} else {
    Write-Log "Hostname already nexplane-smoke-dc"
}

# ── Step 2: Set static-ish DNS to loopback so AD DS install succeeds ──────────
Write-Log "STEP 2: Configuring DNS"
$adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
foreach ($a in $adapters) {
    Set-DnsClientServerAddress -InterfaceIndex $a.InterfaceIndex -ServerAddresses "127.0.0.1","8.8.8.8" -ErrorAction SilentlyContinue
}

# ── Step 3: Install AD DS role ────────────────────────────────────────────────
Write-Log "STEP 3: Installing AD-Domain-Services role (~5 min)"
Install-WindowsFeature AD-Domain-Services -IncludeManagementTools -IncludeAllSubFeature

# ── Step 4: Install DNS Server role (required by AD DS) ───────────────────────
Write-Log "STEP 4: Installing DNS Server role"
Install-WindowsFeature DNS -IncludeManagementTools

# ── Step 5: Promote to domain controller (triggers automatic reboot) ──────────
Write-Log "STEP 5: Promoting to domain controller — instance will reboot"
Import-Module ADDSDeployment

$safePwd = ConvertTo-SecureString "NexplaneSmoke2024!" -AsPlainText -Force

Install-ADDSForest `
    -DomainName "smoke.nexplane.local" `
    -DomainNetbiosName "SMOKE" `
    -SafeModeAdministratorPassword $safePwd `
    -DomainMode "WinThreshold" `
    -ForestMode "WinThreshold" `
    -InstallDns:$true `
    -CreateDnsDelegation:$false `
    -DatabasePath "C:\Windows\NTDS" `
    -LogPath "C:\Windows\NTDS" `
    -SysvolPath "C:\Windows\SYSVOL" `
    -Force:$true `
    -NoRebootOnCompletion:$false

# Install-ADDSForest triggers a reboot; lines below will not execute.
Write-Log "DC_SETUP_COMPLETE — reboot initiated"
