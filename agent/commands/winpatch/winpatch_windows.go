//go:build windows

package winpatch

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

const psSearchScript = `
$Session  = New-Object -ComObject Microsoft.Update.Session
$Searcher = $Session.CreateUpdateSearcher()
$Query    = "IsInstalled=0 and Type='Software' and BrowseOnly=0 and IsAssigned=1"
$Results  = $Searcher.Search($Query)
$out = @()
foreach ($u in $Results.Updates) {
    $cves = @($u.SecurityBulletinIDs) + @($u.CVEIDs)
    $out += [PSCustomObject]@{
        KBArticleID = ($u.KBArticleIDs | Select-Object -First 1)
        Title       = $u.Title
        SizeInBytes = $u.MaxDownloadSize
        Severity    = $u.MsrcSeverity
        CVEIDs      = $cves
    }
}
$out | ConvertTo-Json -Compress
`

const psInstallScript = `
param([string]$Mode, [string]$KBFilter)
$Session   = New-Object -ComObject Microsoft.Update.Session
$Searcher  = $Session.CreateUpdateSearcher()
$Query     = "IsInstalled=0 and Type='Software' and BrowseOnly=0 and IsAssigned=1"
$Results   = $Searcher.Search($Query)
$ToInstall = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $Results.Updates) {
    $kb = ($u.KBArticleIDs | Select-Object -First 1)
    if ($Mode -eq "kb" -and $kb -ne $KBFilter) { continue }
    if ($Mode -eq "security_only" -and $u.MsrcSeverity -notin @("Critical","Important")) { continue }
    $u.AcceptEula()
    $ToInstall.Add($u) | Out-Null
}
if ($ToInstall.Count -eq 0) {
    Write-Output '{"installed":[],"reboot_required":false}'
    exit 0
}
$Downloader            = $Session.CreateUpdateDownloader()
$Downloader.Updates    = $ToInstall
$Downloader.Download() | Out-Null
$Installer             = $Session.CreateUpdateInstaller()
$Installer.Updates     = $ToInstall
$InstallResult         = $Installer.Install()
$installed = @()
for ($i=0; $i -lt $ToInstall.Count; $i++) {
    $u  = $ToInstall.Item($i)
    $kb = ($u.KBArticleIDs | Select-Object -First 1)
    $installed += [PSCustomObject]@{
        kb_id        = "KB$kb"
        title        = $u.Title
        size_bytes   = $u.MaxDownloadSize
        installed_at = (Get-Date -Format o)
    }
}
[PSCustomObject]@{
    installed       = $installed
    reboot_required = $InstallResult.RebootRequired
} | ConvertTo-Json -Compress
`

const psRebootPendingScript = `
$pending = $false
$keys = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager"
)
if (Test-Path $keys[0]) { $pending = $true }
$sm = Get-ItemProperty $keys[1] -ErrorAction SilentlyContinue
if ($sm.PendingFileRenameOperations) { $pending = $true }
Write-Output ($pending.ToString().ToLower())
`

func applyPatchesOS(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	kbID, _ := params["kb_id"].(string)
	dryRun, _ := params["dry_run"].(bool)
	deferReboot, _ := params["defer_reboot"].(bool)

	if dryRun {
		available, err := runPS(psSearchScript)
		if err != nil {
			return nil, fmt.Errorf("dry_run search failed: %w", err)
		}
		var updates []map[string]any
		if err := json.Unmarshal([]byte(available), &updates); err != nil {
			updates = []map[string]any{}
		}
		return map[string]any{
			"dry_run":           true,
			"updates_installed": updates,
			"reboot_required":   false,
		}, nil
	}

	script := fmt.Sprintf(`& { %s } -Mode '%s' -KBFilter '%s'`, psInstallScript, mode, strings.ReplaceAll(kbID, "'", "''"))
	out, err := runPS(script)
	if err != nil {
		return nil, fmt.Errorf("patch installation failed: %w", err)
	}

	var result struct {
		Installed      []map[string]any `json:"installed"`
		RebootRequired bool             `json:"reboot_required"`
	}
	if err := json.Unmarshal([]byte(out), &result); err != nil {
		return nil, fmt.Errorf("failed to parse patch result: %w", err)
	}

	rebootScheduled := ""
	if result.RebootRequired && !deferReboot {
		rebootScheduled = scheduleReboot(params)
	}

	return map[string]any{
		"dry_run":           false,
		"updates_installed": result.Installed,
		"reboot_required":   result.RebootRequired,
		"reboot_scheduled":  rebootScheduled,
	}, nil
}

func rollbackPatchesOS(params map[string]any) (map[string]any, error) {
	installed, _ := params["updates_installed"].([]any)
	uninstalled := []string{}

	for _, raw := range installed {
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		kbID, _ := entry["kb_id"].(string)
		if kbID == "" {
			continue
		}
		num := strings.TrimPrefix(kbID, "KB")
		script := fmt.Sprintf("wusa.exe /uninstall /kb:%s /quiet /norestart; exit $LASTEXITCODE", num)
		if _, err := runPS(script); err != nil {
			return nil, fmt.Errorf("uninstall of %s failed: %w", kbID, err)
		}
		uninstalled = append(uninstalled, kbID)
	}

	return map[string]any{
		"uninstalled": uninstalled,
	}, nil
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	installedScript := `Get-HotFix | Select-Object -ExpandProperty HotFixID | ConvertTo-Json -Compress`
	installedOut, err := runPS(installedScript)
	if err != nil {
		return nil, fmt.Errorf("get installed KBs: %w", err)
	}
	var installedKBs []string
	_ = json.Unmarshal([]byte(installedOut), &installedKBs)

	pendingOut, err := runPS(psSearchScript)
	if err != nil {
		return nil, fmt.Errorf("search pending updates: %w", err)
	}
	var pending []map[string]any
	_ = json.Unmarshal([]byte(pendingOut), &pending)

	rebootOut, err := runPS(psRebootPendingScript)
	if err != nil {
		return nil, fmt.Errorf("check reboot pending: %w", err)
	}
	rebootPending := strings.TrimSpace(rebootOut) == "true"

	return map[string]any{
		"installed_kbs":            installedKBs,
		"security_updates_pending": pending,
		"last_update_check":        time.Now().UTC().Format(time.RFC3339),
		"reboot_pending":           rebootPending,
	}, nil
}

func runPS(script string) (string, error) {
	cmd := exec.Command("powershell.exe", "-NonInteractive", "-NoProfile", "-Command", script)
	out, err := cmd.Output()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			return "", fmt.Errorf("powershell exit %d: %s", exitErr.ExitCode(), exitErr.Stderr)
		}
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

func scheduleReboot(params map[string]any) string {
	rebootWindow, _ := params["reboot_window"].(string)
	if rebootWindow == "" {
		t := time.Now().UTC().Add(5 * time.Minute)
		timeStr := t.Format("15:04")
		dateStr := t.Format("2006-01-02")
		script := fmt.Sprintf(
			`schtasks /Create /TN "NexplaneReboot" /TR "shutdown /r /t 30" /SC ONCE /ST %s /SD %s /F /RL HIGHEST`,
			timeStr, dateStr,
		)
		if _, err := runPS(script); err != nil {
			return ""
		}
		return t.Format(time.RFC3339)
	}
	return ""
}
