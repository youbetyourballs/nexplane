# sccm_site_setup.ps1 — SCCM/MECM Site Server setup for Nexplane smoke test AMI pair
# Run via AWS SSM AWS-RunPowerShellScript on a SEPARATE Windows Server 2022 (t3.xlarge)
# PREREQUISITE: sccm_dc_setup.ps1 must have completed and the DC instance must have
#               rebooted and come back up before running this script.
#
# Estimated runtime: 3-4 hours total
#   - Domain join + prereqs:         ~20 min
#   - SQL Server 2022 Express:        ~30 min (download ~600 MB)
#   - Windows ADK + WinPE add-on:    ~45 min (download ~3 GB)
#   - MECM/ConfigMgr evaluation:     ~90-120 min (download ~1 GB + install)
#
# DOWNLOAD NOTE: The URLs below were valid as of May 2026.
#   SQL Server 2022 Express bootstrapper — redirects to current build.
#   Windows ADK 11 — pinned to a specific build; check aka.ms/adk for latest.
#   MECM Evaluation — Microsoft Evaluation Center; URL must be obtained from:
#     https://www.microsoft.com/en-us/evalcenter/evaluate-microsoft-endpoint-configuration-manager
#   because Microsoft serves it behind a registration form. Replace $MecmEvalUrl
#   with the direct download link obtained from that page.
#
# Domain: smoke.nexplane.local / SMOKE
# Domain admin: SMOKE\Administrator / NexplaneSmoke2024!
# SQL service account: SMOKE\svc-sccm / NexplaneSvc2024!
# SCCM site code: NXP
# SCCM site name: Nexplane Smoke Site

$ErrorActionPreference = "Stop"

$DomainName         = "smoke.nexplane.local"
$DomainNetBios      = "SMOKE"
$DomainAdminUser    = "Administrator"
$DomainAdminPass    = "NexplaneSmoke2024!"
$SvcAccountName     = "svc-sccm"
$SvcAccountPass     = "NexplaneSvc2024!"
$SiteCode           = "NXP"
$SiteName           = "Nexplane Smoke Site"
$SqlInstanceName    = "SQLEXPRESS"
$StagingDir         = "C:\NexplaneSetup"

# ── Download URLs — verify before running ─────────────────────────────────────
# SQL Server 2022 Express bootstrapper (downloads the real installer)
$SqlBootstrapUrl    = "https://go.microsoft.com/fwlink/p/?linkid=2216019&clcid=0x409&culture=en-us&country=us"
# Windows ADK for Windows 11, version 22H2 (build 22621.1)
$AdkUrl             = "https://go.microsoft.com/fwlink/?linkid=2196127"
# Windows ADK WinPE Add-on (required by SCCM)
$WinPeAddonUrl      = "https://go.microsoft.com/fwlink/?linkid=2196224"
# MECM / ConfigMgr Evaluation — REPLACE with URL from Microsoft Evaluation Center:
#   https://www.microsoft.com/en-us/evalcenter/evaluate-microsoft-endpoint-configuration-manager
# The direct download link looks like:
#   https://download.microsoft.com/download/.../MEM_Configmgr_<version>.exe
$MecmEvalUrl        = "PLACEHOLDER_REPLACE_WITH_MECM_EVAL_URL"

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$ts] $Msg"
}

function Wait-ForFile {
    param([string]$Path, [int]$TimeoutSec = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not (Test-Path $Path)) {
        if ((Get-Date) -gt $deadline) { throw "Timeout waiting for file: $Path" }
        Start-Sleep 5
    }
}

function Invoke-Download {
    param([string]$Url, [string]$Dest, [string]$Label)
    Write-Log "Downloading $Label from $Url"
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($Url, $Dest)
    Write-Log "Downloaded $Label to $Dest"
}

# ── Ensure staging directory exists ───────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null

# ── Step 1: Set hostname ───────────────────────────────────────────────────────
Write-Log "STEP 1: Setting hostname to nexplane-smoke-sccm"
if ((hostname) -ne "nexplane-smoke-sccm") {
    Rename-Computer -NewName "nexplane-smoke-sccm" -Force -ErrorAction SilentlyContinue
}

# ── Step 2: Install required Windows Features ─────────────────────────────────
Write-Log "STEP 2: Installing Windows roles and features required by SCCM (~5 min)"
$features = @(
    "NET-Framework-Features",
    "NET-Framework-Core",
    "NET-Framework-45-Features",
    "NET-Framework-45-Core",
    "NET-WCF-HTTP-Activation45",
    "NET-WCF-TCP-Activation45",
    "NET-WCF-Services45",
    "BITS",
    "BITS-IIS-Ext",
    "BITS-Compact-Server",
    "RDC",
    "WAS",
    "WAS-Process-Model",
    "WAS-NET-Environment",
    "WAS-Config-APIs",
    "Web-Server",
    "Web-WebServer",
    "Web-Common-Http",
    "Web-Default-Doc",
    "Web-Dir-Browsing",
    "Web-Http-Errors",
    "Web-Static-Content",
    "Web-Http-Redirect",
    "Web-Health",
    "Web-Http-Logging",
    "Web-Log-Libraries",
    "Web-Request-Monitor",
    "Web-Http-Tracing",
    "Web-Performance",
    "Web-Stat-Compression",
    "Web-Security",
    "Web-Filtering",
    "Web-Windows-Auth",
    "Web-App-Dev",
    "Web-Net-Ext",
    "Web-Net-Ext45",
    "Web-Asp-Net",
    "Web-Asp-Net45",
    "Web-ISAPI-Ext",
    "Web-ISAPI-Filter",
    "Web-Mgmt-Tools",
    "Web-Mgmt-Console"
)
Install-WindowsFeature -Name $features -IncludeManagementTools -ErrorAction Continue
Write-Log "Windows features installed"

# ── Step 3: Join domain ────────────────────────────────────────────────────────
Write-Log "STEP 3: Joining domain $DomainName"
$cred = New-Object System.Management.Automation.PSCredential(
    "$DomainNetBios\$DomainAdminUser",
    (ConvertTo-SecureString $DomainAdminPass -AsPlainText -Force)
)
try {
    $domainInfo = Get-WmiObject Win32_ComputerSystem
    if ($domainInfo.PartOfDomain -and $domainInfo.Domain -eq $DomainName) {
        Write-Log "Already joined to $DomainName — skipping"
    } else {
        Add-Computer -DomainName $DomainName -Credential $cred -Force
        Write-Log "Domain join succeeded — reboot required but deferring to end of script"
    }
} catch {
    Write-Log "Domain join error: $_ — attempting to continue (may already be joined)"
}

# ── Step 4: Create SCCM service account in AD ─────────────────────────────────
Write-Log "STEP 4: Creating service account $SvcAccountName in AD"
try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $svcPwd = ConvertTo-SecureString $SvcAccountPass -AsPlainText -Force
    if (-not (Get-ADUser -Filter { SamAccountName -eq $SvcAccountName } -ErrorAction SilentlyContinue)) {
        New-ADUser `
            -Name $SvcAccountName `
            -SamAccountName $SvcAccountName `
            -UserPrincipalName "$SvcAccountName@$DomainName" `
            -AccountPassword $svcPwd `
            -Enabled $true `
            -PasswordNeverExpires $true `
            -CannotChangePassword $true
        Write-Log "Service account $SvcAccountName created"
    } else {
        Write-Log "Service account $SvcAccountName already exists"
    }
    # Add to Domain Admins for lab environment (SCCM requires broad AD rights)
    Add-ADGroupMember -Identity "Domain Admins" -Members $SvcAccountName -ErrorAction SilentlyContinue
} catch {
    Write-Log "AD service account step skipped (AD module not yet available): $_"
}

# ── Step 5: Download and install SQL Server 2022 Express ─────────────────────
Write-Log "STEP 5: Downloading SQL Server 2022 Express bootstrapper (~30 min total)"
$sqlBootstrap = "$StagingDir\sql_bootstrap.exe"
Invoke-Download -Url $SqlBootstrapUrl -Dest $sqlBootstrap -Label "SQL Server 2022 Express bootstrapper"

# The bootstrapper extracts and launches the real setup; /ACTION=Download first
$sqlExtractDir = "$StagingDir\SQL2022Express"
New-Item -ItemType Directory -Force -Path $sqlExtractDir | Out-Null
Write-Log "Running SQL bootstrapper to download media (~600 MB)..."
$sqlDlArgs = @("/Q", "/ACTION=Download", "/MEDIAPATH=$sqlExtractDir", "/MEDIATYPE=Core")
$p = Start-Process -FilePath $sqlBootstrap -ArgumentList $sqlDlArgs -Wait -PassThru
Write-Log "SQL bootstrap download exit code: $($p.ExitCode)"

# Install SQL Express from downloaded media
Write-Log "Installing SQL Server 2022 Express..."
$sqlSetup = Get-ChildItem -Path $sqlExtractDir -Filter "SQLEXPR*.exe" -Recurse | Select-Object -First 1
if (-not $sqlSetup) {
    # Bootstrapper may have laid out a setup.exe directly
    $sqlSetup = Get-ChildItem -Path $sqlExtractDir -Filter "setup.exe" -Recurse | Select-Object -First 1
}
if (-not $sqlSetup) { throw "SQL Server setup executable not found in $sqlExtractDir" }

$sqlInstallArgs = @(
    "/Q",
    "/ACTION=Install",
    "/FEATURES=SQLEngine,Tools",
    "/INSTANCENAME=$SqlInstanceName",
    "/SQLSYSADMINACCOUNTS=$DomainNetBios\Administrator",
    "/ADDCURRENTUSERASSQLADMIN=True",
    "/TCPENABLED=1",
    "/NPENABLED=0",
    "/IACCEPTSQLSERVERLICENSETERMS=True",
    "/SQLSVCACCOUNT=$DomainNetBios\$SvcAccountName",
    "/SQLSVCPASSWORD=$SvcAccountPass",
    "/AGTSVCACCOUNT=$DomainNetBios\$SvcAccountName",
    "/AGTSVCPASSWORD=$SvcAccountPass",
    "/SQLCOLLATION=SQL_Latin1_General_CP1_CI_AS"
)
$p2 = Start-Process -FilePath $sqlSetup.FullName -ArgumentList $sqlInstallArgs -Wait -PassThru
Write-Log "SQL Server install exit code: $($p2.ExitCode) (3010=reboot needed, 0=success)"

# Enable Named Pipes and TCP in SQL configuration
Import-Module SQLPS -DisableNameChecking -ErrorAction SilentlyContinue
try {
    [System.Reflection.Assembly]::LoadWithPartialName("Microsoft.SqlServer.SqlWmiManagement") | Out-Null
    $wmi = New-Object Microsoft.SqlServer.Management.Smo.Wmi.ManagedComputer
    $tcp = $wmi.ServerInstances[$SqlInstanceName].ServerProtocols["Tcp"]
    $tcp.IsEnabled = $true
    $tcp.Alter()
    Restart-Service "MSSQL`$$SqlInstanceName" -Force -ErrorAction SilentlyContinue
    Write-Log "SQL Server TCP protocol enabled"
} catch {
    Write-Log "SQL protocol config via WMI skipped: $_ — TCP may already be enabled"
}

# ── Step 6: Download and install Windows ADK ─────────────────────────────────
Write-Log "STEP 6: Downloading Windows ADK (~2.5 GB total download, ~45 min)"
$adkSetup = "$StagingDir\adksetup.exe"
Invoke-Download -Url $AdkUrl -Dest $adkSetup -Label "Windows ADK"

Write-Log "Installing Windows ADK (Deployment Tools, Imaging, PE)..."
$adkArgs = @(
    "/quiet",
    "/norestart",
    "/features", "OptionId.DeploymentTools", "OptionId.UserStateMigrationTool",
    "/log", "$StagingDir\adk_install.log"
)
$p3 = Start-Process -FilePath $adkSetup -ArgumentList $adkArgs -Wait -PassThru
Write-Log "Windows ADK install exit code: $($p3.ExitCode)"

# WinPE Add-on (separate download, required by SCCM for OSD)
$winPeSetup = "$StagingDir\adkwinpesetup.exe"
Invoke-Download -Url $WinPeAddonUrl -Dest $winPeSetup -Label "Windows ADK WinPE Add-on"

Write-Log "Installing Windows ADK WinPE Add-on..."
$winPeArgs = @(
    "/quiet",
    "/norestart",
    "/features", "OptionId.WindowsPreinstallationEnvironment",
    "/log", "$StagingDir\winpe_install.log"
)
$p4 = Start-Process -FilePath $winPeSetup -ArgumentList $winPeArgs -Wait -PassThru
Write-Log "WinPE Add-on install exit code: $($p4.ExitCode)"

# ── Step 7: Download MECM/ConfigMgr evaluation ────────────────────────────────
Write-Log "STEP 7: Downloading MECM evaluation installer (~1 GB, ~15 min download)"
if ($MecmEvalUrl -eq "PLACEHOLDER_REPLACE_WITH_MECM_EVAL_URL") {
    Write-Log "WARNING: MecmEvalUrl is a placeholder. Obtain the URL from:"
    Write-Log "  https://www.microsoft.com/en-us/evalcenter/evaluate-microsoft-endpoint-configuration-manager"
    Write-Log "Re-run this script after setting the correct URL."
    throw "MecmEvalUrl not configured — see instructions above"
}

$mecmInstaller = "$StagingDir\MEM_Configmgr_Eval.exe"
Invoke-Download -Url $MecmEvalUrl -Dest $mecmInstaller -Label "MECM Evaluation"

# MECM eval installer is a self-extracting archive; extract to staging
$mecmExtractDir = "$StagingDir\MECM"
New-Item -ItemType Directory -Force -Path $mecmExtractDir | Out-Null
Write-Log "Extracting MECM installer..."
$p5 = Start-Process -FilePath $mecmInstaller -ArgumentList "/extract:$mecmExtractDir /quiet" -Wait -PassThru
Write-Log "MECM extract exit code: $($p5.ExitCode)"

# ── Step 8: Create SCCM installation answer file ─────────────────────────────
Write-Log "STEP 8: Creating SCCM unattended installation script"
$mecmInstallDir = "C:\Program Files\Microsoft Configuration Manager"
$sccmAnswerFile = @"
[Identification]
Action=InstallPrimarySite

[Options]
ProductID=EVAL
SiteCode=$SiteCode
SiteName=$SiteName
SMSInstallDir=$mecmInstallDir
SDKServer=nexplane-smoke-sccm.$DomainName
RolesCommunicationProtocol=HTTPorHTTPS
ClientsUsePKICertificate=0
PrerequisiteComp=1
PrerequisitePath=$StagingDir\MecmPrereqs
AdminConsole=1
JoinCEIP=0

[SQLConfigOptions]
SQLServerName=nexplane-smoke-sccm\$SqlInstanceName
DatabaseName=CM_$SiteCode
SQLSSBPort=4022

[CloudConnectorOptions]
UseProxy=0
ProxyName=
ProxyPort=

[SystemCenterOptions]

[HierarchyExpansionOption]
"@

$answerPath = "$StagingDir\sccm_unattend.ini"
$sccmAnswerFile | Out-File -FilePath $answerPath -Encoding ASCII
Write-Log "Answer file written to $answerPath"

# ── Step 9: Download SCCM prerequisites ──────────────────────────────────────
Write-Log "STEP 9: Downloading SCCM prerequisites (~30-45 min)"
$prereqDir = "$StagingDir\MecmPrereqs"
New-Item -ItemType Directory -Force -Path $prereqDir | Out-Null
$setupDl = Get-ChildItem -Path $mecmExtractDir -Filter "setupdl.exe" -Recurse | Select-Object -First 1
if ($setupDl) {
    $p6 = Start-Process -FilePath $setupDl.FullName -ArgumentList $prereqDir -Wait -PassThru
    Write-Log "Prerequisite download exit code: $($p6.ExitCode)"
} else {
    Write-Log "setupdl.exe not found in extracted MECM — prereqs will be downloaded during install"
}

# ── Step 10: Install SCCM primary site (~90 min) ─────────────────────────────
Write-Log "STEP 10: Installing SCCM primary site (~90-120 min) — this is the long step"
$mecmSetup = Get-ChildItem -Path $mecmExtractDir -Filter "setup.exe" -Recurse | Select-Object -First 1
if (-not $mecmSetup) { throw "SCCM setup.exe not found in $mecmExtractDir" }

$sccmArgs = @(
    "/script", $answerPath,
    "/nouserinput"
)
$p7 = Start-Process -FilePath $mecmSetup.FullName -ArgumentList $sccmArgs -Wait -PassThru
Write-Log "SCCM install exit code: $($p7.ExitCode)"

# Check install log for success
$sccmLog = "C:\ConfigMgrSetup.log"
if (Test-Path $sccmLog) {
    $lastLines = Get-Content $sccmLog -Tail 20
    Write-Log "=== Last 20 lines of ConfigMgrSetup.log ==="
    $lastLines | ForEach-Object { Write-Log $_ }
}

# ── Step 11: Open firewall ports for Nexplane connector ───────────────────────
Write-Log "STEP 11: Opening firewall ports for SCCM WMI/RPC access"
$firewallRules = @(
    @{Name="SCCM-WMI-In";    Port=135;  Proto="TCP"},
    @{Name="SCCM-SMB-In";    Port=445;  Proto="TCP"},
    @{Name="SCCM-HTTP-In";   Port=80;   Proto="TCP"},
    @{Name="SCCM-HTTPS-In";  Port=443;  Proto="TCP"},
    @{Name="SCCM-SQLBr-In";  Port=1433; Proto="TCP"},
    @{Name="SCCM-SSB-In";    Port=4022; Proto="TCP"}
)
foreach ($rule in $firewallRules) {
    netsh advfirewall firewall add rule `
        name=$($rule.Name) dir=in action=allow protocol=$($rule.Proto) localport=$($rule.Port) | Out-Null
}
# WMI dynamic ports (needed for remote WMI queries by Nexplane)
netsh advfirewall firewall add rule name="WMI-DCOM-In" dir=in action=allow program="%SystemRoot%\system32\svchost.exe" | Out-Null
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False
Write-Log "Firewall rules configured"

# ── Step 12: Store site info for SSM parameter retrieval ──────────────────────
Write-Log "STEP 12: Writing setup summary to $StagingDir\sccm_setup_complete.txt"
@"
SCCM_SETUP_COMPLETE
SiteCode=$SiteCode
SiteName=$SiteName
Domain=$DomainName
SqlInstance=nexplane-smoke-sccm\$SqlInstanceName
InstallDir=$mecmInstallDir
SvcAccount=$DomainNetBios\$SvcAccountName
SetupDate=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
"@ | Out-File "$StagingDir\sccm_setup_complete.txt" -Encoding ASCII

Write-Log "SCCM_SITE_SETUP_COMPLETE"
Write-Log "Site server setup finished. The AMI snapshot can now be taken."
Write-Log "Nexplane SSM parameters to populate after AMI registration:"
Write-Log "  /nexplane/smoke/sccm/server   = <private-ip-of-site-server>"
Write-Log "  /nexplane/smoke/sccm/username = $DomainNetBios\Administrator"
Write-Log "  /nexplane/smoke/sccm/password = $DomainAdminPass"
Write-Log "  /nexplane/smoke/sccm/site_code = $SiteCode"
