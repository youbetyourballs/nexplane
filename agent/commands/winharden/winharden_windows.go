//go:build windows

package winharden

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// runPS runs a PowerShell command and returns combined output.
func runPS(script string) ([]byte, error) {
	return exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).CombinedOutput()
}

// regExport exports a registry key to a temp file and returns the content.
func regExport(key string) (string, error) {
	tmp, err := os.CreateTemp("", "nexplane-reg-*.reg")
	if err != nil {
		return "", err
	}
	tmp.Close()
	defer os.Remove(tmp.Name())
	if out, err := exec.Command("reg", "export", key, tmp.Name(), "/y").CombinedOutput(); err != nil {
		return "", fmt.Errorf("reg export %s: %s: %w", key, out, err)
	}
	data, err := os.ReadFile(tmp.Name())
	return string(data), err
}

// regImport restores a registry snapshot.
func regImport(regContent string) (map[string]any, error) {
	tmp, err := os.CreateTemp("", "nexplane-reg-restore-*.reg")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(regContent); err != nil {
		return nil, err
	}
	tmp.Close()
	if out, err := exec.Command("reg", "import", tmp.Name()).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("reg import: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}

// --- configure_laps ---

func lapsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	snapshot, _ := regExport(`HKLM\SOFTWARE\Policies\Microsoft Services\AdmPwd`)

	passwordAgeDays := 30
	if v, _ := params["password_age_days"].(float64); v > 0 {
		passwordAgeDays = int(v)
	}
	passwordLength := 14
	if v, _ := params["password_length"].(float64); v > 0 {
		passwordLength = int(v)
	}

	if action == "enable" {
		script := fmt.Sprintf(`
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Force | Out-Null
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "AdmPwdEnabled" -Value 1 -Type DWord -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "PasswordAgeDays" -Value %d -Type DWord -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "PasswordLength" -Value %d -Type DWord -Force
gpupdate /force | Out-Null`, passwordAgeDays, passwordLength)
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("enabling LAPS: %s: %w", out, err)
		}
	} else {
		script := `Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "AdmPwdEnabled" -Value 0 -Type DWord -Force; gpupdate /force | Out-Null`
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("disabling LAPS: %s: %w", out, err)
		}
	}
	return map[string]any{
		"action": action, "password_age_days": passwordAgeDays, "password_length": passwordLength,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func lapsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	return regImport(snapshot)
}

// --- enable_credential_guard ---

func credGuardExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	uefiLock, _ := params["require_uefi_lock"].(bool)
	snapshot, _ := regExport(`HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard`)

	if action == "enable" {
		lsaCfgFlags := 1
		if uefiLock {
			lsaCfgFlags = 2
		}
		script := fmt.Sprintf(`
$key = "HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard"
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name "EnableVirtualizationBasedSecurity" -Value 1 -Type DWord -Force
Set-ItemProperty -Path $key -Name "RequirePlatformSecurityFeatures" -Value 1 -Type DWord -Force
Set-ItemProperty -Path $key -Name "LsaCfgFlags" -Value %d -Type DWord -Force
Set-ItemProperty -Path $key -Name "HypervisorEnforcedCodeIntegrity" -Value 1 -Type DWord -Force`, lsaCfgFlags)
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("enabling Credential Guard: %s: %w", out, err)
		}
	} else {
		script := `
$key = "HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard"
Set-ItemProperty -Path $key -Name "EnableVirtualizationBasedSecurity" -Value 0 -Type DWord -Force
Set-ItemProperty -Path $key -Name "LsaCfgFlags" -Value 0 -Type DWord -Force`
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("disabling Credential Guard: %s: %w", out, err)
		}
	}
	return map[string]any{
		"action": action, "uefi_lock": uefiLock, "reboot_required": true,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func credGuardRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if uefiLock, _ := params["uefi_lock"].(bool); uefiLock {
		return map[string]any{
			"rolled_back": false,
			"warning":     "UEFI lock prevents registry rollback — firmware intervention required",
		}, nil
	}
	return regImport(snapshot)
}

// --- enforce_powershell_clm ---

func psclmExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	mechanism, _ := params["mechanism"].(string)
	if mechanism == "" {
		mechanism = "registry"
	}
	snapshot, _ := regExport(`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`)

	if action == "enable" {
		script := `New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" -Name "__PSLockdownPolicy" -Value "4" -PropertyType String -Force | Out-Null`
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("enabling CLM: %s: %w", out, err)
		}
	} else {
		script := `Remove-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" -Name "__PSLockdownPolicy" -ErrorAction SilentlyContinue`
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("disabling CLM: %s: %w", out, err)
		}
	}
	return map[string]any{
		"action": action, "mechanism": mechanism,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func psclmRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	return regImport(snapshot)
}

// --- deploy_applocker_policy ---

func applockerExecuteOS(params map[string]any) (map[string]any, error) {
	policy, _ := params["policy"].(string)
	enforce, _ := params["enforce"].(bool)

	snapshotOut, _ := runPS(`Get-AppLockerPolicy -Effective -Xml`)
	snapshot := string(snapshotOut)

	tmp, err := os.CreateTemp("", "nexplane-applocker-*.xml")
	if err != nil {
		return nil, fmt.Errorf("creating temp policy file: %w", err)
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(policy); err != nil {
		return nil, fmt.Errorf("writing policy: %w", err)
	}
	tmp.Close()

	mode := "AuditOnly"
	if enforce {
		mode = "Enabled"
	}

	script := fmt.Sprintf(`
Set-AppLockerPolicy -XmlPolicy "%s"
Set-Service AppIDSvc -StartupType Automatic
Start-Service AppIDSvc -ErrorAction SilentlyContinue`, tmp.Name())
	if out, err := runPS(script); err != nil {
		return nil, fmt.Errorf("applying AppLocker policy: %s: %w", out, err)
	}
	return map[string]any{
		"enforce": enforce, "mode": mode,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func applockerRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	tmp, err := os.CreateTemp("", "nexplane-applocker-restore-*.xml")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	tmp.WriteString(snapshot)
	tmp.Close()
	if out, err := runPS(fmt.Sprintf(`Set-AppLockerPolicy -XmlPolicy "%s"`, tmp.Name())); err != nil {
		return nil, fmt.Errorf("restoring AppLocker: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}

// --- harden_smb ---

func smbExecuteOS(params map[string]any) (map[string]any, error) {
	disableSMB1 := true
	if v, ok := params["disable_smb1"].(bool); ok {
		disableSMB1 = v
	}
	requireSigning := true
	if v, ok := params["require_signing"].(bool); ok {
		requireSigning = v
	}
	disableGuest := true
	if v, ok := params["disable_guest_access"].(bool); ok {
		disableGuest = v
	}

	snapshotOut, _ := runPS(`Get-SmbServerConfiguration | ConvertTo-Json`)
	snapshot := string(snapshotOut)

	if disableSMB1 {
		runPS(`Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force`)              //nolint:errcheck
		runPS(`Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart`) //nolint:errcheck
	}
	if requireSigning {
		runPS(`Set-SmbServerConfiguration -RequireSecuritySignature $true -Force`) //nolint:errcheck
		runPS(`Set-SmbClientConfiguration -RequireSecuritySignature $true -Force`) //nolint:errcheck
	}
	if disableGuest {
		runPS(`Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Services\LanManWorkstation\Parameters -Name AllowInsecureGuestAuth -Value 0`) //nolint:errcheck
	}

	return map[string]any{
		"smb1_disabled": disableSMB1, "signing_required": requireSigning, "guest_disabled": disableGuest,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func smbRollbackOS(params map[string]any) (map[string]any, error) {
	_, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if smb1Disabled, _ := params["smb1_disabled"].(bool); smb1Disabled {
		runPS(`Enable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart`) //nolint:errcheck
		runPS(`Set-SmbServerConfiguration -EnableSMB1Protocol $true -Force`)               //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}

// --- enable_bitlocker ---

func bitlockerExecuteOS(params map[string]any) (map[string]any, error) {
	drive, _ := params["drive_letter"].(string)
	if drive == "" {
		drive = "C:"
	}
	protector, _ := params["protector"].(string)
	if protector == "" {
		protector = "tpm"
	}
	method, _ := params["encryption_method"].(string)
	if method == "" {
		method = "XtsAes256"
	}

	snapshotOut, _ := runPS(fmt.Sprintf(`Get-BitLockerVolume -MountPoint "%s" | Select-Object VolumeStatus,EncryptionMethod,ProtectionStatus | ConvertTo-Json`, drive))
	snapshot := string(snapshotOut)

	switch protector {
	case "tpm":
		runPS(fmt.Sprintf(`Add-BitLockerKeyProtector -MountPoint "%s" -TpmProtector`, drive)) //nolint:errcheck
	case "tpm_pin":
		pin, _ := params["pin"].(string)
		runPS(fmt.Sprintf(`Add-BitLockerKeyProtector -MountPoint "%s" -TpmAndPinProtector -Pin (ConvertTo-SecureString "%s" -AsPlainText -Force)`, drive, pin)) //nolint:errcheck
	}

	recoveryOut, _ := runPS(fmt.Sprintf(`(Add-BitLockerKeyProtector -MountPoint "%s" -RecoveryPasswordProtector).KeyProtector | Where-Object KeyProtectorType -eq "RecoveryPassword" | Select-Object -ExpandProperty RecoveryPassword`, drive))
	recoveryKey := strings.TrimSpace(string(recoveryOut))

	if out, err := runPS(fmt.Sprintf(`Enable-BitLocker -MountPoint "%s" -EncryptionMethod %s -UsedSpaceOnly`, drive, method)); err != nil {
		return nil, fmt.Errorf("enabling BitLocker: %s: %w", out, err)
	}

	return map[string]any{
		"drive": drive, "encryption_method": method, "protector": protector,
		"recovery_key": recoveryKey, "volume_status": "EncryptionInProgress",
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func bitlockerRollbackOS(params map[string]any) (map[string]any, error) {
	drive, _ := params["drive"].(string)
	if drive == "" {
		drive = "C:"
	}
	if out, err := runPS(fmt.Sprintf(`Disable-BitLocker -MountPoint "%s"`, drive)); err != nil {
		return nil, fmt.Errorf("disabling BitLocker: %s: %w", out, err)
	}
	return map[string]any{
		"rolled_back": true,
		"warning":     "BitLocker decryption in progress — this may take hours on large volumes",
	}, nil
}

// --- configure_windows_firewall ---

func winfirewallExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)

	tmp, err := os.CreateTemp("", "nexplane-fw-*.wfw")
	if err != nil {
		return nil, err
	}
	snapshotPath := tmp.Name()
	tmp.Close()
	defer os.Remove(snapshotPath)
	runPS(fmt.Sprintf(`netsh advfirewall export "%s"`, snapshotPath)) //nolint:errcheck
	snapshotData, _ := os.ReadFile(snapshotPath)
	snapshot := string(snapshotData)

	rule, _ := params["rule"].(map[string]any)
	switch action {
	case "add_rule":
		name, _ := rule["name"].(string)
		direction, _ := rule["direction"].(string)
		proto, _ := rule["protocol"].(string)
		if proto == "" {
			proto = "TCP"
		}
		port, _ := rule["local_port"].(string)
		actionType, _ := rule["action_type"].(string)
		if actionType == "" {
			actionType = "Allow"
		}
		script := fmt.Sprintf(`New-NetFirewallRule -DisplayName "%s" -Direction %s -Protocol %s -LocalPort %s -Action %s -Enabled True`, name, direction, proto, port, actionType)
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("adding firewall rule: %s: %w", out, err)
		}
	case "remove_rule":
		name, _ := rule["name"].(string)
		if out, err := runPS(fmt.Sprintf(`Remove-NetFirewallRule -DisplayName "%s"`, name)); err != nil {
			return nil, fmt.Errorf("removing firewall rule: %s: %w", out, err)
		}
	case "set_default_action":
		defAction, _ := params["default_action"].(map[string]any)
		inbound, _ := defAction["inbound"].(string)
		outbound, _ := defAction["outbound"].(string)
		runPS(fmt.Sprintf(`Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultInboundAction %s -DefaultOutboundAction %s`, inbound, outbound)) //nolint:errcheck
	}

	return map[string]any{
		"action": action, "snapshot": snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func winfirewallRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	tmp, err := os.CreateTemp("", "nexplane-fw-restore-*.wfw")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	tmp.WriteString(snapshot)
	tmp.Close()
	if out, err := runPS(fmt.Sprintf(`netsh advfirewall import "%s"`, tmp.Name())); err != nil {
		return nil, fmt.Errorf("restoring firewall: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}

// --- harden_tls_protocols ---

func tlsExecuteOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := regExport(`HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL`)

	disableProtocols := []string{"SSL 2.0", "SSL 3.0", "TLS 1.0", "TLS 1.1"}
	enableProtocols := []string{"TLS 1.2", "TLS 1.3"}

	if dp, ok := params["disable_protocols"].([]any); ok {
		disableProtocols = nil
		for _, p := range dp {
			if s, ok := p.(string); ok {
				disableProtocols = append(disableProtocols, s)
			}
		}
	}
	if ep, ok := params["enabled_protocols"].([]any); ok {
		enableProtocols = nil
		for _, p := range ep {
			if s, ok := p.(string); ok {
				enableProtocols = append(enableProtocols, s)
			}
		}
	}

	sides := []string{"Server", "Client"}
	for _, proto := range disableProtocols {
		for _, side := range sides {
			key := fmt.Sprintf(`HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\%s\%s`, proto, side)
			runPS(fmt.Sprintf(`New-Item -Path "%s" -Force | Out-Null; Set-ItemProperty -Path "%s" -Name "Enabled" -Value 0 -Type DWord -Force; Set-ItemProperty -Path "%s" -Name "DisabledByDefault" -Value 1 -Type DWord -Force`, key, key, key)) //nolint:errcheck
		}
	}
	for _, proto := range enableProtocols {
		for _, side := range sides {
			key := fmt.Sprintf(`HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\%s\%s`, proto, side)
			runPS(fmt.Sprintf(`New-Item -Path "%s" -Force | Out-Null; Set-ItemProperty -Path "%s" -Name "Enabled" -Value 1 -Type DWord -Force; Set-ItemProperty -Path "%s" -Name "DisabledByDefault" -Value 0 -Type DWord -Force`, key, key, key)) //nolint:errcheck
		}
	}

	return map[string]any{
		"disabled_protocols": disableProtocols, "enabled_protocols": enableProtocols,
		"reboot_required": true,
		"snapshot":        snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func tlsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	result, err := regImport(snapshot)
	if err != nil {
		return nil, err
	}
	result["reboot_required"] = true
	return result, nil
}

// --- harden_rdp ---

func rdpExecuteOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := regExport(`HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp`)

	requireNLA := true
	if v, ok := params["require_nla"].(bool); ok {
		requireNLA = v
	}
	nlaVal := 0
	if requireNLA {
		nlaVal = 1
	}

	script := fmt.Sprintf(`
$key = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
Set-ItemProperty -Path $key -Name "UserAuthentication" -Value %d -Type DWord -Force
Set-ItemProperty -Path $key -Name "SecurityLayer" -Value 2 -Type DWord -Force`, nlaVal)

	if idleTimeout, _ := params["idle_timeout_minutes"].(float64); idleTimeout > 0 {
		script += fmt.Sprintf("\nSet-ItemProperty -Path $key -Name 'MaxIdleTime' -Value %d -Type DWord -Force", int(idleTimeout)*60*1000)
	}
	if port, _ := params["port"].(float64); port > 0 {
		script += fmt.Sprintf("\nSet-ItemProperty -Path $key -Name 'PortNumber' -Value %d -Type DWord -Force", int(port))
	}
	script += "\nRestart-Service TermService -Force"

	if out, err := runPS(script); err != nil {
		return nil, fmt.Errorf("hardening RDP: %s: %w", out, err)
	}
	return map[string]any{
		"nla": requireNLA, "snapshot": snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rdpRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	result, err := regImport(snapshot)
	if err != nil {
		return nil, err
	}
	runPS(`Restart-Service TermService -Force`) //nolint:errcheck
	return result, nil
}

// --- configure_windows_audit_policy ---

func auditpolExecuteOS(params map[string]any) (map[string]any, error) {
	snapshotOut, _ := exec.Command("auditpol", "/get", "/category:*", "/r").Output()
	snapshot := string(snapshotOut)

	profile, _ := params["profile"].(string)
	if profile == "" {
		profile = "cis_level1"
	}

	type auditEntry struct{ success, failure bool }
	settings := map[string]auditEntry{
		"Logon":                    {true, true},
		"Account Logon":            {true, true},
		"Object Access":            {false, true},
		"Privilege Use":            {false, true},
		"Detailed Tracking":        {true, false},
		"Policy Change":            {true, false},
		"Account Management":       {true, true},
		"Directory Service Access": {false, true},
		"System":                   {true, true},
	}
	if profile == "cis_level2" || profile == "stig" {
		settings["Object Access"] = auditEntry{true, true}
		settings["Privilege Use"] = auditEntry{true, true}
	}

	for category, entry := range settings {
		successFlag, failureFlag := "disable", "disable"
		if entry.success {
			successFlag = "enable"
		}
		if entry.failure {
			failureFlag = "enable"
		}
		exec.Command("auditpol", "/set", fmt.Sprintf(`/category:"%s"`, category), "/success:"+successFlag, "/failure:"+failureFlag).Run() //nolint:errcheck
	}
	runPS(`Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit" -Name "ProcessCreationIncludeCmdLine_Enabled" -Value 1 -Type DWord -Force`) //nolint:errcheck

	return map[string]any{
		"profile_applied":  profile,
		"categories_count": len(settings),
		"snapshot":         snapshot,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func auditpolRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	for _, line := range strings.Split(snapshot, "\n") {
		fields := strings.Split(line, ",")
		if len(fields) < 5 || fields[0] == "Machine Name" {
			continue
		}
		subcategory := fields[2]
		setting := fields[4]
		success, failure := "disable", "disable"
		if strings.Contains(setting, "Success") {
			success = "enable"
		}
		if strings.Contains(setting, "Failure") {
			failure = "enable"
		}
		exec.Command("auditpol", "/set", fmt.Sprintf(`/subcategory:"%s"`, subcategory), "/success:"+success, "/failure:"+failure).Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}

// --- audit_scheduled_tasks ---

func auditTasksOS(_ map[string]any) (map[string]any, error) {
	script := `Get-ScheduledTask | ForEach-Object { [PSCustomObject]@{ TaskName=$_.TaskName; TaskPath=$_.TaskPath; State=$_.State; RunLevel=$_.Principal.RunLevel; Hidden=$_.Settings.Hidden } } | ConvertTo-Json -Depth 2`
	tasksOut, _ := runPS(script)

	findings := []map[string]any{}
	if len(tasksOut) > 0 {
		findings = append(findings, map[string]any{
			"tag":         "scheduled-task-inventory",
			"description": "Scheduled task inventory collected — review for unexpected entries",
			"raw":         string(tasksOut),
		})
	}
	tags := []string{}
	if len(findings) > 0 {
		tags = append(tags, "scheduled-task-findings")
	}
	return map[string]any{
		"findings":   findings,
		"total":      len(findings),
		"tags":       tags,
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// --- harden_registry ---

func registryExecuteOS(params map[string]any) (map[string]any, error) {
	type regSetting struct {
		path  string
		name  string
		value int
	}
	settings := map[string]regSetting{
		"disable_autorun":              {`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer`, "NoDriveTypeAutoRun", 255},
		"disable_lm_hash":              {`HKLM\SYSTEM\CurrentControlSet\Control\Lsa`, "NoLMHash", 1},
		"disable_ntlmv1":               {`HKLM\SYSTEM\CurrentControlSet\Control\Lsa`, "LmCompatibilityLevel", 5},
		"disable_wdigest":              {`HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest`, "UseLogonCredential", 0},
		"enable_safe_dll_search":       {`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager`, "SafeDllSearchMode", 1},
		"enforce_uac_prompt":           {`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`, "ConsentPromptBehaviorAdmin", 2},
		"disable_print_spooler_remote": {`HKLM\Software\Policies\Microsoft\Windows NT\Printers`, "RegisterSpoolerRemoteRpcEndPoint", 2},
	}

	snapshots := map[string]string{}
	keysSeen := map[string]bool{}
	for _, s := range settings {
		if !keysSeen[s.path] {
			snap, _ := regExport(s.path)
			snapshots[s.path] = snap
			keysSeen[s.path] = true
		}
	}

	applied := []string{}
	for name, s := range settings {
		if v, ok := params[name].(bool); ok && !v {
			continue
		}
		script := fmt.Sprintf(`reg add "%s" /v "%s" /t REG_DWORD /d %d /f`, s.path, s.name, s.value)
		runPS(script) //nolint:errcheck
		applied = append(applied, name)
	}

	return map[string]any{
		"settings_applied": applied,
		"snapshot":         snapshots,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func registryRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	for _, content := range snapshot {
		if s, ok := content.(string); ok && s != "" {
			regImport(s) //nolint:errcheck
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
