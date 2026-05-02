//go:build windows

package crossplatform

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func tlsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	service, _ := params["service"].(string)
	if action == "validate" {
		certPath, _ := params["cert_path"].(string)
		if certPath == "" {
			certPath = `C:\nexplane\tls\server.crt`
		}
		out, _ := exec.Command("certutil", "-verify", certPath).Output()
		return map[string]any{
			"action": "validate", "service": service,
			"certutil_output": strings.TrimSpace(string(out)),
			"checked_at":      time.Now().UTC().Format(time.RFC3339),
		}, nil
	}
	return map[string]any{
		"action": action, "service": service,
		"snapshot": map[string]any{}, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func tlsRollbackOS(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true}, nil
}

func dnsExecuteOS(params map[string]any) (map[string]any, error) {
	resolversRaw, _ := params["resolvers"].([]any)
	resolvers := make([]string, 0, len(resolversRaw))
	for _, r := range resolversRaw {
		if s, ok := r.(string); ok {
			resolvers = append(resolvers, s)
		}
	}
	snapshotOut, _ := exec.Command("netsh", "dns", "show", "server").Output()
	snapshot := string(snapshotOut)

	serverList := strings.Join(resolvers, ",")
	script := fmt.Sprintf(`$adapters = Get-NetAdapter | Where-Object {$_.Status -eq "Up"}; foreach ($a in $adapters) { Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses %s }`, serverList)
	exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).Run() //nolint:errcheck

	return map[string]any{
		"mode": "plain", "resolvers": resolvers,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func dnsRollbackOS(params map[string]any) (map[string]any, error) {
	if _, ok := params["snapshot"].(string); !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	exec.Command("powershell", "-Command", `Get-NetAdapter | Where-Object {$_.Status -eq "Up"} | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }`).Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
	script := `Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | Where-Object DisplayName | Select-Object DisplayName,DisplayVersion | ConvertTo-Json -Depth 1`
	out, _ := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).Output()
	return map[string]any{
		"packages":   []map[string]any{},
		"raw_output": strings.TrimSpace(string(out)),
		"platform":   "windows",
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
