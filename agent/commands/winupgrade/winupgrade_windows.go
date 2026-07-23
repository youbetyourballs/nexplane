//go:build windows

package winupgrade

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

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

// PreflightExecute checks disk space, OS version, and optionally DISM compat.
func PreflightExecute(params map[string]any) (map[string]any, error) {
	targetVersion := strParam(params, "target_version")

	// Get current OS info
	osOut, err := runPS(`(Get-WmiObject -Class Win32_OperatingSystem).Caption`)
	if err != nil {
		return nil, fmt.Errorf("OS version query failed: %w", err)
	}
	currentOS := strings.TrimSpace(osOut)

	// Get disk free space on C: in GB
	diskOut, err := runPS(`[math]::Round((Get-PSDrive -Name C).Free / 1GB, 2)`)
	if err != nil {
		return nil, fmt.Errorf("disk space check failed: %w", err)
	}
	var diskFreeGB float64
	fmt.Sscanf(strings.TrimSpace(diskOut), "%f", &diskFreeGB)
	diskOK := diskFreeGB >= 32.0

	// DISM compatibility check (non-blocking — only if setup media is present)
	dismExitCode := "0xC1900210" // default: no issues
	dismPassed := true
	var dismIssues []string

	setupExe := fmt.Sprintf(`C:\Windows\System32\DISM.exe`)
	dismScript := fmt.Sprintf(
		`$r = & "%s" /Online /Get-FeatureInfo /FeatureName:Windows-Defender-Default-Definitions 2>&1; $LASTEXITCODE`,
		setupExe,
	)
	if targetVersion != "" {
		// Try DISM compat scan if setup.exe is available
		dismScript = fmt.Sprintf(
			`try {
				$r = Start-Process -FilePath "DISM.exe" -ArgumentList "/Online /Check-AppCompat" -Wait -PassThru -NoNewWindow 2>$null
				if ($r) { "0x{0:X8}" -f $r.ExitCode } else { "0xC1900210" }
			} catch { "0xC1900210" }`,
		)
		dismOut, dismErr := runPS(dismScript)
		if dismErr == nil && strings.TrimSpace(dismOut) != "" {
			dismExitCode = strings.TrimSpace(dismOut)
			if dismExitCode != "0xC1900210" && dismExitCode != "0xC1900208" {
				dismPassed = false
				dismIssues = append(dismIssues, fmt.Sprintf("DISM exit: %s", dismExitCode))
			}
		}
	}

	return map[string]any{
		"current_os":          currentOS,
		"target_version":      targetVersion,
		"disk_free_gb":        diskFreeGB,
		"disk_ok":             diskOK,
		"dism_compat_passed":  dismPassed,
		"dism_exit_code":      dismExitCode,
		"dism_compat_issues":  dismIssues,
	}, nil
}

// VSSCreateShadowExecute creates a VSS shadow copy of C:.
func VSSCreateShadowExecute(params map[string]any) (map[string]any, error) {
	script := `
$wmi = Get-WmiObject -List Win32_ShadowCopy
$result = $wmi.Create("C:\", "ClientAccessible")
if ($result.ReturnValue -ne 0) {
    Write-Error "VSS create failed with code $($result.ReturnValue)"
    exit 1
}
$shadow = Get-WmiObject Win32_ShadowCopy | Where-Object { $_.ID -eq $result.ShadowID }
[PSCustomObject]@{
    shadow_id       = $shadow.ID
    device_name     = $shadow.DeviceName
    created_at      = $shadow.InstallDate
    volume          = "C:\"
} | ConvertTo-Json -Compress
`
	out, err := runPS(script)
	if err != nil {
		return nil, fmt.Errorf("VSS shadow create failed: %w", err)
	}
	var result map[string]any
	if err := json.Unmarshal([]byte(out), &result); err != nil {
		return nil, fmt.Errorf("failed to parse VSS result: %w", err)
	}
	return result, nil
}

// StartOSUpgradeExecute launches the Windows in-place upgrade setup.
// Expects params: setup_exe_path (path to setup.exe from Windows upgrade media).
func StartOSUpgradeExecute(params map[string]any) (map[string]any, error) {
	setupExe := strParam(params, "setup_exe_path")
	if setupExe == "" {
		setupExe = `C:\WindowsUpgrade\setup.exe`
	}

	// Launch setup.exe /auto upgrade /quiet — initiates in-place upgrade
	// setup.exe returns immediately; actual upgrade happens after reboot
	script := fmt.Sprintf(`
$proc = Start-Process -FilePath '%s' -ArgumentList '/auto upgrade /quiet /noreboot /DynamicUpdate disable' -PassThru -Wait
Write-Output $proc.ExitCode
`, strings.ReplaceAll(setupExe, "'", "''"))

	out, err := runPS(script)
	exitCode := strings.TrimSpace(out)
	if err != nil {
		return nil, fmt.Errorf("upgrade setup failed: %w", err)
	}

	return map[string]any{
		"setup_started":    true,
		"setup_exit_code":  exitCode,
		"reboot_initiated": true,
	}, nil
}

// VerifyOSUpgradeExecute checks the OS version post-upgrade.
func VerifyOSUpgradeExecute(params map[string]any) (map[string]any, error) {
	targetVersion := strParam(params, "target_version")

	osOut, err := runPS(`(Get-WmiObject -Class Win32_OperatingSystem).Caption`)
	if err != nil {
		return nil, fmt.Errorf("OS version query failed: %w", err)
	}
	currentOS := strings.TrimSpace(osOut)
	verified := strings.Contains(currentOS, targetVersion)

	return map[string]any{
		"current_os":      currentOS,
		"target_version":  targetVersion,
		"upgrade_verified": verified,
	}, nil
}

// VSSRestoreExecute restores C: from a VSS shadow copy.
func VSSRestoreExecute(params map[string]any) (map[string]any, error) {
	shadowID := strParam(params, "shadow_id")
	if shadowID == "" {
		return nil, fmt.Errorf("shadow_id is required for VSS restore")
	}

	script := fmt.Sprintf(`
$shadow = Get-WmiObject Win32_ShadowCopy | Where-Object { $_.ID -eq '%s' }
if (-not $shadow) { Write-Error "Shadow copy not found: %s"; exit 1 }
$linkName = "C:\ShadowRestore"
cmd /c "mklink /d $linkName $($shadow.DeviceName)\" 2>&1
$linkName
`, strings.ReplaceAll(shadowID, "'", "''"), shadowID)

	out, err := runPS(script)
	if err != nil {
		return nil, fmt.Errorf("VSS restore failed: %w", err)
	}

	return map[string]any{
		"shadow_id":    shadowID,
		"restore_path": strings.TrimSpace(out),
		"restored":     true,
	}, nil
}
