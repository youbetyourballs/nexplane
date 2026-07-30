// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

//go:build windows

package winmigrate

import (
	"encoding/json"
	"fmt"
	"os"
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

// WinventoryExecute collects full Windows inventory and scans for hostname references.
// Params: hostname (string, required), registry_keys ([]string, optional extra paths).
func WinventoryExecute(params map[string]any) (map[string]any, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("win_inventory: hostname param is required")
	}

	script := fmt.Sprintf(`
$hostname = '%s'
$ErrorActionPreference = 'SilentlyContinue'

# Services
$services = Get-WmiObject Win32_Service | ForEach-Object {
    [PSCustomObject]@{ name=$_.Name; display_name=$_.DisplayName; state=$_.State; start_mode=$_.StartMode; path=$_.PathName; account=$_.StartName }
} | ConvertTo-Json -Depth 2 -Compress

# Scheduled tasks
$tasks = Get-ScheduledTask | ForEach-Object {
    $t = $_
    [PSCustomObject]@{
        name=$t.TaskName; path=$t.TaskPath; state=$t.State.ToString(); run_as=$t.Principal.UserId
        triggers=($t.Triggers | Select-Object CimClass,StartBoundary | ConvertTo-Json -Compress)
        actions=($t.Actions | Select-Object Execute,Arguments | ConvertTo-Json -Compress)
    }
} | ConvertTo-Json -Depth 2 -Compress

# IIS sites
$iis = '[]'
if (Get-Module -ListAvailable -Name WebAdministration) {
    Import-Module WebAdministration -ErrorAction SilentlyContinue
    $iis = Get-Website | ForEach-Object {
        $s = $_
        [PSCustomObject]@{
            name=$s.name; state=$s.state; physical_path=$s.physicalPath
            bindings=($s.bindings.Collection | ForEach-Object { $_.bindingInformation } | ConvertTo-Json -Compress)
        }
    } | ConvertTo-Json -Depth 2 -Compress
    if (-not $iis) { $iis = '[]' }
}

# Environment variables (machine scope)
$envVars = [System.Environment]::GetEnvironmentVariables('Machine').GetEnumerator() | ForEach-Object {
    [PSCustomObject]@{ key=$_.Key; value=$_.Value }
} | ConvertTo-Json -Compress

# Certificates
$certs = Get-ChildItem Cert:\LocalMachine -Recurse | Where-Object { !$_.PSIsContainer } | ForEach-Object {
    [PSCustomObject]@{
        store=($_.PSParentPath -split '\\')[-1]; thumbprint=$_.Thumbprint
        subject=$_.Subject; not_after=$_.NotAfter.ToString('o')
    }
} | ConvertTo-Json -Compress

# Config files (common app paths)
$configFiles = @()
@('C:\inetpub','C:\apps','C:\testapp','C:\services','C:\ProgramData\myapp') | ForEach-Object {
    if (Test-Path $_) {
        Get-ChildItem -Path $_ -Recurse -Include 'web.config','app.config','*.ini','*.cfg','*.conf' -ErrorAction SilentlyContinue | ForEach-Object {
            $configFiles += [PSCustomObject]@{ path=$_.FullName; content=(Get-Content $_.FullName -Raw) }
        }
    }
}
$configFilesJson = $configFiles | ConvertTo-Json -Depth 2 -Compress
if (-not $configFilesJson) { $configFilesJson = '[]' }

# Registry (HKLM\SOFTWARE excluding Microsoft/Windows noise)
$regKeys = @()
Get-ChildItem 'HKLM:\SOFTWARE' -ErrorAction SilentlyContinue | Where-Object {
    $_.PSChildName -notin @('Microsoft','Windows NT','Classes','RegisteredApplications','Wow6432Node')
} | ForEach-Object {
    $k = $_; $vals = @{}
    $k.GetValueNames() | ForEach-Object { $vals[$_] = $k.GetValue($_).ToString() }
    $regKeys += [PSCustomObject]@{ path=$k.Name; values=($vals | ConvertTo-Json -Compress) }
}
$regKeysJson = $regKeys | ConvertTo-Json -Depth 2 -Compress
if (-not $regKeysJson) { $regKeysJson = '[]' }

# Hostname reference scan
$refs = @()
$configFiles | ForEach-Object {
    if ($_.content -and $_.content.Contains($hostname)) {
        $refs += [PSCustomObject]@{ location='file'; path=$_.path; type='hostname'; value=$hostname }
    }
}
$regKeys | ForEach-Object {
    if ($_.values -and $_.values.Contains($hostname)) {
        $refs += [PSCustomObject]@{ location='registry'; path=$_.path; type='hostname'; value=$hostname }
    }
}
if ($iis -ne '[]') {
    $iisList = $iis | ConvertFrom-Json
    $iisList | ForEach-Object {
        if ($_.bindings -and $_.bindings.Contains($hostname)) {
            $refs += [PSCustomObject]@{ location='iis'; path=$_.name; type='hostname'; value=$hostname }
        }
    }
}
$refsJson = $refs | ConvertTo-Json -Compress
if (-not $refsJson) { $refsJson = '[]' }

[PSCustomObject]@{
    services=$services; scheduled_tasks=$tasks; iis_sites=$iis
    env_vars=$envVars; certificates=$certs; config_files=$configFilesJson
    registry_keys=$regKeysJson; hostname_refs=$refsJson
} | ConvertTo-Json -Depth 1 -Compress
`, strings.ReplaceAll(hostname, "'", "''"))

	out, err := runPS(script)
	if err != nil {
		return nil, fmt.Errorf("win_inventory: %w", err)
	}

	var result map[string]any
	if err := json.Unmarshal([]byte(out), &result); err != nil {
		return nil, fmt.Errorf("win_inventory: failed to parse result: %w\noutput: %s", err, out)
	}

	// Parse hostname_refs from JSON string into []map[string]any
	if refsStr, ok := result["hostname_refs"].(string); ok {
		var refs []any
		if json.Unmarshal([]byte(refsStr), &refs) == nil {
			result["hostname_refs"] = refs
		}
	}

	return result, nil
}

// RobocopyPushExecute copies C:\ from source to dest via SMB admin share using temp credentials.
// Params: dest_host (string), dest_user (string), dest_password (string),
//         excludes ([]string, optional extra dirs to exclude).
func RobocopyPushExecute(params map[string]any) (map[string]any, error) {
	destHost, _ := params["dest_host"].(string)
	destUser, _ := params["dest_user"].(string)
	destPassword, _ := params["dest_password"].(string)
	if destHost == "" || destUser == "" || destPassword == "" {
		return nil, fmt.Errorf("win_robocopy_push: dest_host, dest_user, dest_password are required")
	}

	excludesRaw, _ := params["excludes"].([]any)
	defaultExcludes := []string{"Windows", "Program Files", "Program Files (x86)", "ProgramData\\Microsoft", "$Recycle.Bin", "System Volume Information"}
	var excludeDirs []string
	excludeDirs = append(excludeDirs, defaultExcludes...)
	for _, ex := range excludesRaw {
		if s, ok := ex.(string); ok {
			excludeDirs = append(excludeDirs, s)
		}
	}

	xdArgs := make([]string, len(excludeDirs))
	for i, d := range excludeDirs {
		xdArgs[i] = fmt.Sprintf(`"%s"`, d)
	}
	xdStr := strings.Join(xdArgs, " ")

	unc := fmt.Sprintf(`\\%s\C$`, destHost)

	// Escape single quotes in password for PowerShell
	safePassword := strings.ReplaceAll(destPassword, "'", "''")
	safeUser := strings.ReplaceAll(destUser, "'", "''")

	script := fmt.Sprintf(`
$unc = '%s'
$user = '.\%s'
$pw = '%s'

# Mount admin share
$netResult = & net use $unc /user:$user $pw 2>&1
if ($LASTEXITCODE -ne 0) { throw "net use failed (exit $LASTEXITCODE): $netResult" }

try {
    # Robocopy C:\ to dest C$
    $args = @('C:\', $unc, '/MIR', '/COPYALL', '/R:3', '/W:5', '/NP', '/LOG:C:\nexplane-robocopy.log', '/XD', %s)
    & robocopy @args
    $rc = $LASTEXITCODE
    # Exit codes 0-7 are success (8+ indicate errors)
    if ($rc -ge 8) { throw "robocopy failed with exit code $rc" }
    $logContent = Get-Content 'C:\nexplane-robocopy.log' -Tail 20 -ErrorAction SilentlyContinue | Out-String
    [PSCustomObject]@{ exit_code=$rc; log_tail=$logContent } | ConvertTo-Json -Compress
} finally {
    & net use $unc /delete /y 2>&1 | Out-Null
}
`, unc, safeUser, safePassword, xdStr)

	out, err := runPS(script)
	if err != nil {
		return nil, fmt.Errorf("win_robocopy_push: %w", err)
	}

	var result map[string]any
	if err := json.Unmarshal([]byte(out), &result); err != nil {
		return map[string]any{"raw_output": out}, nil
	}
	return result, nil
}

// ApplyReplacementsExecute rewrites hostname references on the dest instance.
// Params: replacements ([]map[string]any, each with location/path/old/new).
func ApplyReplacementsExecute(params map[string]any) (map[string]any, error) {
	replacementsRaw, _ := params["replacements"].([]any)
	if len(replacementsRaw) == 0 {
		return map[string]any{"applied": 0}, nil
	}

	applied := 0
	var errors []string

	for _, r := range replacementsRaw {
		item, ok := r.(map[string]any)
		if !ok {
			continue
		}
		location, _ := item["location"].(string)
		path, _ := item["path"].(string)
		oldVal, _ := item["old"].(string)
		newVal, _ := item["new"].(string)

		if path == "" || oldVal == "" {
			continue
		}

		switch location {
		case "file":
			content, err := os.ReadFile(path)
			if err != nil {
				errors = append(errors, fmt.Sprintf("read %s: %v", path, err))
				continue
			}
			updated := strings.ReplaceAll(string(content), oldVal, newVal)
			if err := os.WriteFile(path, []byte(updated), 0644); err != nil {
				errors = append(errors, fmt.Sprintf("write %s: %v", path, err))
				continue
			}
			applied++

		case "registry":
			// path is registry key path (e.g. HKLM\SOFTWARE\MyApp)
			// Replace hostname in all string values under that key
			psSafe := strings.ReplaceAll(path, "'", "''")
			oldSafe := strings.ReplaceAll(oldVal, "'", "''")
			newSafe := strings.ReplaceAll(newVal, "'", "''")
			script := fmt.Sprintf(`
$key = Get-Item 'Registry::%s' -ErrorAction SilentlyContinue
if ($key) {
    $key.GetValueNames() | ForEach-Object {
        $v = $key.GetValue($_)
        if ($v -is [string] -and $v.Contains('%s')) {
            Set-ItemProperty -Path 'Registry::%s' -Name $_ -Value ($v -replace [regex]::Escape('%s'), '%s')
        }
    }
}
`, psSafe, oldSafe, psSafe, oldSafe, newSafe)
			if _, err := runPS(script); err != nil {
				errors = append(errors, fmt.Sprintf("registry %s: %v", path, err))
				continue
			}
			applied++

		case "iis":
			// path is IIS site name; update hostname in bindings
			siteSafe := strings.ReplaceAll(path, "'", "''")
			oldSafe := strings.ReplaceAll(oldVal, "'", "''")
			newSafe := strings.ReplaceAll(newVal, "'", "''")
			script := fmt.Sprintf(`
Import-Module WebAdministration -ErrorAction SilentlyContinue
$site = Get-Website -Name '%s' -ErrorAction SilentlyContinue
if ($site) {
    $site.bindings.Collection | ForEach-Object {
        if ($_.bindingInformation.Contains('%s')) {
            $_.bindingInformation = $_.bindingInformation -replace [regex]::Escape('%s'), '%s'
        }
    }
    $site | Set-Item
}
`, siteSafe, oldSafe, oldSafe, newSafe)
			if _, err := runPS(script); err != nil {
				errors = append(errors, fmt.Sprintf("iis %s: %v", path, err))
				continue
			}
			applied++
		}
	}

	result := map[string]any{"applied": applied}
	if len(errors) > 0 {
		result["errors"] = strings.Join(errors, "; ")
	}
	return result, nil
}
