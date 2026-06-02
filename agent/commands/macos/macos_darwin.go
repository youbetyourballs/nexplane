//go:build darwin

package macos

import (
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func run(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func runTimeout(timeout time.Duration, name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	done := make(chan struct{})
	var out []byte
	var err error
	go func() {
		out, err = cmd.CombinedOutput()
		close(done)
	}()
	select {
	case <-done:
		return strings.TrimSpace(string(out)), err
	case <-time.After(timeout):
		cmd.Process.Kill()
		return "", fmt.Errorf("command timed out after %s", timeout)
	}
}

// filevaultStatus runs fdesetup status and returns enabled flag + raw status string.
func filevaultStatus(_ map[string]any) (map[string]any, error) {
	out, err := run("fdesetup", "status")
	if err != nil {
		return nil, fmt.Errorf("fdesetup status: %s: %w", out, err)
	}
	enabled := strings.Contains(strings.ToLower(out), "filevault is on")
	return map[string]any{"enabled": enabled, "status": out}, nil
}

// filevaultEnable enables FileVault and returns the generated recovery key.
func filevaultEnable(_ map[string]any) (map[string]any, error) {
	out, err := run("fdesetup", "enable", "-outputplist", "-")
	if err != nil {
		return nil, fmt.Errorf("fdesetup enable: %s: %w", out, err)
	}
	// Extract recovery key from plist output (key is in <string>XXXX-XXXX-...</string> after RecoveryKey)
	key := ""
	lines := strings.Split(out, "\n")
	for i, line := range lines {
		if strings.Contains(line, "RecoveryKey") && i+1 < len(lines) {
			val := lines[i+1]
			val = strings.TrimSpace(val)
			val = strings.TrimPrefix(val, "<string>")
			val = strings.TrimSuffix(val, "</string>")
			key = val
			break
		}
	}
	return map[string]any{"recovery_key": key, "plist": out}, nil
}

// gatekeeperStatus returns whether Gatekeeper is enabled.
func gatekeeperStatus(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--status")
	if err != nil {
		// spctl --status exits non-zero on some macOS versions even when working
		if !strings.Contains(out, "assessments") {
			return nil, fmt.Errorf("spctl --status: %s: %w", out, err)
		}
	}
	enabled := strings.Contains(strings.ToLower(out), "assessments enabled")
	return map[string]any{"enabled": enabled, "status": out}, nil
}

// gatekeeperEnable enables Gatekeeper via spctl --master-enable.
func gatekeeperEnable(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--master-enable")
	if err != nil {
		return nil, fmt.Errorf("spctl --master-enable: %s: %w", out, err)
	}
	return map[string]any{"enabled": true, "output": out}, nil
}

// gatekeeperDisable disables Gatekeeper via spctl --master-disable.
func gatekeeperDisable(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--master-disable")
	if err != nil {
		return nil, fmt.Errorf("spctl --master-disable: %s: %w", out, err)
	}
	return map[string]any{"enabled": false, "output": out}, nil
}

// softwareupdateList lists available macOS software updates.
func softwareupdateList(_ map[string]any) (map[string]any, error) {
	out, err := run("softwareupdate", "--list", "--all")
	if err != nil && !strings.Contains(out, "Software Update found") && !strings.Contains(out, "No new software available") {
		return nil, fmt.Errorf("softwareupdate --list: %s: %w", out, err)
	}
	updates := parseUpdates(out)
	return map[string]any{"updates": updates, "raw": out}, nil
}

// parseUpdates parses softwareupdate --list output into a slice of label/size maps.
func parseUpdates(out string) []map[string]any {
	var updates []map[string]any
	lines := strings.Split(out, "\n")
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "* Label:") {
			label := strings.TrimPrefix(trimmed, "* Label:")
			label = strings.TrimSpace(label)
			size := ""
			// Look ahead for Size line
			for j := i + 1; j < len(lines) && j < i+5; j++ {
				next := strings.TrimSpace(lines[j])
				if strings.HasPrefix(next, "Size:") {
					size = strings.TrimPrefix(next, "Size:")
					size = strings.TrimSpace(size)
					break
				}
			}
			updates = append(updates, map[string]any{"label": label, "size": size})
		}
	}
	return updates
}

// softwareupdateInstall installs all or a specific labeled update.
func softwareupdateInstall(params map[string]any) (map[string]any, error) {
	const timeout = 30 * time.Minute
	label, _ := params["label"].(string)
	var out string
	var err error
	if label != "" {
		out, err = runTimeout(timeout, "softwareupdate", "--install", label)
	} else {
		out, err = runTimeout(timeout, "softwareupdate", "--install", "--all")
	}
	if err != nil {
		return nil, fmt.Errorf("softwareupdate --install: %s: %w", out, err)
	}
	return map[string]any{"output": out}, nil
}

// profilesList lists installed configuration profiles by running profiles list -output stdout-xml.
func profilesList(_ map[string]any) (map[string]any, error) {
	out, err := run("profiles", "list", "-output", "stdout-xml")
	if err != nil {
		return nil, fmt.Errorf("profiles list: %s: %w", out, err)
	}
	// Return the raw plist; structured parsing would require an xml library
	// which adds no value for the dispatch/audit use case.
	return map[string]any{"profiles_plist": out}, nil
}

// launchctlList lists running launchd services.
func launchctlList(_ map[string]any) (map[string]any, error) {
	out, err := run("launchctl", "list")
	if err != nil {
		return nil, fmt.Errorf("launchctl list: %s: %w", out, err)
	}
	services := parseLaunchctl(out)
	return map[string]any{"services": services, "raw": out}, nil
}

// parseLaunchctl parses tab-separated launchctl list output.
func parseLaunchctl(out string) []map[string]any {
	var services []map[string]any
	lines := strings.Split(out, "\n")
	for i, line := range lines {
		if i == 0 { // header
			continue
		}
		parts := strings.Fields(line)
		if len(parts) < 3 {
			continue
		}
		pid := parts[0]
		status := parts[1]
		label := parts[2]
		services = append(services, map[string]any{"pid": pid, "status": status, "label": label})
	}
	return services
}

// defaultsWrite reads the current value of a defaults key, then writes the new value.
func defaultsWrite(params map[string]any) (map[string]any, error) {
	domain, _ := params["domain"].(string)
	key, _ := params["key"].(string)
	value, _ := params["value"].(string)
	typ, _ := params["type"].(string)
	if domain == "" || key == "" || value == "" {
		return nil, fmt.Errorf("defaults_write requires domain, key, and value")
	}
	if typ == "" {
		typ = "string"
	}

	// Capture previous value for rollback; non-zero exit means key didn't exist.
	prevOut, prevErr := run("defaults", "read", domain, key)
	var previousValue any
	if prevErr == nil {
		previousValue = prevOut
	}

	out, err := run("defaults", "write", domain, key, "-"+typ, value)
	if err != nil {
		return nil, fmt.Errorf("defaults write %s %s: %s: %w", domain, key, out, err)
	}

	result := map[string]any{
		"domain":         domain,
		"key":            key,
		"value":          value,
		"type":           typ,
		"previous_value": previousValue,
		"written_at":     time.Now().UTC().Format(time.RFC3339),
	}
	return result, nil
}

// santaCheck audits Santa binary allowlisting status via santactl status.
func santaCheck(_ map[string]any) (map[string]any, error) {
	checkedAt := time.Now().UTC().Format(time.RFC3339)

	out, err := run("santactl", "status")
	if err != nil {
		// santactl not installed or not running — return installed:false, don't error.
		return map[string]any{"installed": false, "checked_at": checkedAt}, nil
	}

	result := map[string]any{
		"installed":    true,
		"mode":         "",
		"file_logging": false,
		"raw_output":   out,
		"checked_at":   checkedAt,
	}

	for _, line := range strings.Split(out, "\n") {
		parts := strings.SplitN(line, "|", 2)
		if len(parts) != 2 {
			continue
		}
		k := strings.TrimSpace(parts[0])
		v := strings.TrimSpace(parts[1])
		switch k {
		case "Mode":
			result["mode"] = v
		case "File Logging":
			result["file_logging"] = strings.EqualFold(v, "yes") || strings.EqualFold(v, "true")
		case "Watch Items":
			result["watch_items"] = v
		}
	}

	return result, nil
}

func profilesInstall(params map[string]any) (map[string]any, error) {
	plistB64, _ := params["plist_b64"].(string)
	if plistB64 == "" {
		return nil, fmt.Errorf("profiles_install requires plist_b64")
	}

	plistBytes, err := base64.StdEncoding.DecodeString(plistB64)
	if err != nil {
		return nil, fmt.Errorf("profiles_install: invalid base64: %w", err)
	}

	identifier := extractPlistKey(string(plistBytes), "PayloadIdentifier")
	if identifier == "" {
		return nil, fmt.Errorf("profiles_install: PayloadIdentifier not found in plist")
	}

	tmp, err := os.CreateTemp("", "nexplane-profile-*.mobileconfig")
	if err != nil {
		return nil, fmt.Errorf("profiles_install: temp file: %w", err)
	}
	defer os.Remove(tmp.Name())

	if _, err := tmp.Write(plistBytes); err != nil {
		return nil, fmt.Errorf("profiles_install: write temp: %w", err)
	}
	tmp.Close()

	out, err := run("profiles", "install", "-path", tmp.Name())
	if err != nil {
		// profiles install can fail on non-MDM-enrolled hosts; return graceful result so CR completes
		return map[string]any{"identifier": identifier, "installed": false, "error": out}, nil
	}
	return map[string]any{"identifier": identifier, "installed": true, "output": out}, nil
}

func profilesRemove(params map[string]any) (map[string]any, error) {
	identifier, _ := params["identifier"].(string)
	if identifier == "" {
		return nil, fmt.Errorf("profiles_remove requires identifier")
	}

	listOut, _ := run("profiles", "list", "-output", "stdout-xml")
	prevPlistB64 := base64.StdEncoding.EncodeToString([]byte(listOut))

	out, err := run("profiles", "remove", "-identifier", identifier)
	if err != nil {
		return nil, fmt.Errorf("profiles remove %s: %s: %w", identifier, out, err)
	}
	return map[string]any{
		"identifier":         identifier,
		"removed":            true,
		"previous_plist_b64": prevPlistB64,
		"output":             out,
	}, nil
}

func extractPlistKey(plist, key string) string {
	lines := strings.Split(plist, "\n")
	for i, line := range lines {
		if strings.Contains(line, "<key>"+key+"</key>") && i+1 < len(lines) {
			val := strings.TrimSpace(lines[i+1])
			val = strings.TrimPrefix(val, "<string>")
			val = strings.TrimSuffix(val, "</string>")
			if val != lines[i+1] {
				return val
			}
		}
	}
	return ""
}

// homebrewList lists all installed Homebrew packages and versions.
func homebrewList(_ map[string]any) (map[string]any, error) {
	brewPath, err := exec.LookPath("brew")
	if err != nil {
		return map[string]any{"installed": false, "packages": []map[string]any{}}, nil
	}

	out, err := run(brewPath, "list", "--versions")
	if err != nil {
		return nil, fmt.Errorf("brew list --versions: %s: %w", out, err)
	}

	var packages []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		name := parts[0]
		version := ""
		if len(parts) > 1 {
			version = parts[len(parts)-1]
		}
		packages = append(packages, map[string]any{"name": name, "version": version})
	}
	if packages == nil {
		packages = []map[string]any{}
	}
	return map[string]any{"installed": true, "packages": packages}, nil
}

func santaRuleCheck(identifierType, identifier string) string {
	out, err := run("santactl", "rule", "--check", "--"+identifierType, identifier)
	if err != nil || strings.Contains(strings.ToLower(out), "no rule") || strings.Contains(strings.ToLower(out), "unknown") {
		return "absent"
	}
	lower := strings.ToLower(out)
	if strings.Contains(lower, "allowlist") || strings.Contains(lower, "allow") {
		return "allow"
	}
	if strings.Contains(lower, "denylist") || strings.Contains(lower, "deny") || strings.Contains(lower, "blocklist") {
		return "deny"
	}
	return "absent"
}

func ruleTypeFlag(ruleType string) string {
	switch ruleType {
	case "allowlist":
		return "--allowlist"
	case "silent_blocklist":
		return "--silent-blocklist"
	default:
		return "--denylist"
	}
}

func santaRuleAdd(params map[string]any) (map[string]any, error) {
	ruleType, _ := params["rule_type"].(string)
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	customMessage, _ := params["custom_message"].(string)

	if identifier == "" || identifierType == "" {
		return nil, fmt.Errorf("santa_rule_add requires identifier_type and identifier")
	}
	if ruleType == "" {
		ruleType = "denylist"
	}

	previousState := santaRuleCheck(identifierType, identifier)

	args := []string{"rule", "--add", ruleTypeFlag(ruleType), "--" + identifierType, identifier}
	if customMessage != "" {
		args = append(args, "--message", customMessage)
	}
	out, err := run("santactl", args...)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --add: %s: %w", out, err)
	}
	return map[string]any{
		"added":           true,
		"rule_type":       ruleType,
		"identifier_type": identifierType,
		"identifier":      identifier,
		"previous_state":  previousState,
		"output":          out,
	}, nil
}

func santaRuleRemove(params map[string]any) (map[string]any, error) {
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	if identifier == "" || identifierType == "" {
		return nil, fmt.Errorf("santa_rule_remove requires identifier_type and identifier")
	}

	previousState := santaRuleCheck(identifierType, identifier)
	if previousState == "absent" {
		return map[string]any{"removed": false, "reason": "rule not found", "previous_state": "absent"}, nil
	}

	out, err := run("santactl", "rule", "--remove", "--"+identifierType, identifier)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --remove: %s: %w", out, err)
	}
	return map[string]any{
		"removed":         true,
		"identifier_type": identifierType,
		"identifier":      identifier,
		"previous_state":  previousState,
		"output":          out,
	}, nil
}

func santaRuleList(_ map[string]any) (map[string]any, error) {
	out, err := run("santactl", "rule", "--list")
	if err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "not installed") || strings.Contains(strings.ToLower(out), "not found") {
			return map[string]any{"installed": false, "rules": []map[string]any{}}, nil
		}
		return nil, fmt.Errorf("santactl rule --list: %s: %w", out, err)
	}
	var rules []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "Rule") {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) >= 2 {
			rules = append(rules, map[string]any{"identifier": parts[0], "type": parts[1]})
		}
	}
	if rules == nil {
		rules = []map[string]any{}
	}
	return map[string]any{"installed": true, "rules": rules, "rule_count": len(rules)}, nil
}

func santaModeSet(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode != "monitor" && mode != "lockdown" {
		return nil, fmt.Errorf("santa_mode_set: mode must be 'monitor' or 'lockdown'")
	}

	statusOut, _ := run("santactl", "status")
	previousMode := "monitor"
	for _, line := range strings.Split(statusOut, "\n") {
		parts := strings.SplitN(line, "|", 2)
		if len(parts) == 2 && strings.TrimSpace(parts[0]) == "Mode" {
			v := strings.ToLower(strings.TrimSpace(parts[1]))
			if strings.Contains(v, "lockdown") {
				previousMode = "lockdown"
			}
		}
	}

	modeInt := "1"
	if mode == "lockdown" {
		modeInt = "2"
	}
	out, err := run("defaults", "write", "/Library/Preferences/com.google.santa", "ClientMode", "-int", modeInt)
	if err != nil {
		return nil, fmt.Errorf("santa_mode_set defaults write: %s: %w", out, err)
	}
	run("santactl", "sync", "--clean")

	return map[string]any{"mode": mode, "previous_mode": previousMode, "output": out}, nil
}

func santaSyncTrigger(_ map[string]any) (map[string]any, error) {
	out, err := run("santactl", "sync")
	if err != nil {
		return map[string]any{"synced": false, "error": out}, nil
	}
	return map[string]any{"synced": true, "output": out}, nil
}

func santaEventExport(params map[string]any) (map[string]any, error) {
	limit := 100
	if l, ok := params["limit"].(float64); ok && l > 0 {
		limit = int(l)
	}

	out, err := run("santactl", "log")
	if err != nil {
		logPath := "/var/db/santa/santa.log"
		data, ferr := os.ReadFile(logPath)
		if ferr != nil {
			return map[string]any{"events": []map[string]any{}, "error": "santactl log not available"}, nil
		}
		out = string(data)
	}

	var events []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		if len(events) >= limit {
			break
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		event := map[string]any{"raw": line}
		if strings.Contains(line, "DENY") {
			event["decision"] = "DENY"
		} else if strings.Contains(line, "ALLOW") {
			event["decision"] = "ALLOW"
		}
		events = append(events, event)
	}
	if events == nil {
		events = []map[string]any{}
	}
	return map[string]any{"events": events, "count": len(events)}, nil
}

func santaBinaryCheck(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		return nil, fmt.Errorf("santa_binary_check requires path")
	}
	out, err := run("santactl", "check", "--path", path)
	if err != nil {
		return map[string]any{"path": path, "decision": "UNKNOWN", "error": out}, nil
	}

	result := map[string]any{"path": path, "decision": "UNKNOWN", "sha256": "", "rule_type": ""}
	for _, line := range strings.Split(out, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		k := strings.TrimSpace(parts[0])
		v := strings.TrimSpace(parts[1])
		switch k {
		case "Decision":
			result["decision"] = strings.ToUpper(v)
		case "SHA-256":
			result["sha256"] = v
		case "Rule":
			result["rule_type"] = v
		}
	}
	return result, nil
}

// macosSysinfo returns macOS version and hardware info.
func macosSysinfo(_ map[string]any) (map[string]any, error) {
	swVers, err := run("sw_vers")
	if err != nil {
		return nil, fmt.Errorf("sw_vers: %s: %w", swVers, err)
	}
	profiler, err := run("system_profiler", "SPHardwareDataType")
	if err != nil {
		return nil, fmt.Errorf("system_profiler: %s: %w", profiler, err)
	}

	result := map[string]any{
		"os_version": "",
		"build":      "",
		"model":      "",
		"serial":     "",
	}

	for _, line := range strings.Split(swVers, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		switch key {
		case "ProductVersion":
			result["os_version"] = val
		case "BuildVersion":
			result["build"] = val
		}
	}

	for _, line := range strings.Split(profiler, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		switch key {
		case "Model Name", "Model Identifier":
			if result["model"] == "" {
				result["model"] = val
			}
		case "Serial Number (system)":
			result["serial"] = val
		}
	}

	return result, nil
}
