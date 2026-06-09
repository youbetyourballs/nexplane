//go:build windows

package configsyslog

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	host, _ := params["destination_host"].(string)
	port := params["destination_port"]
	proto, _ := params["protocol"].(string)

	if host == "" {
		return nil, fmt.Errorf("destination_host is required")
	}
	if port == nil {
		return nil, fmt.Errorf("destination_port is required")
	}
	if proto == "" {
		return nil, fmt.Errorf("protocol is required")
	}
	portInt := toInt(port)

	nxlogPath := `C:\Program Files\nxlog\conf\nxlog.conf`
	if _, err := os.Stat(nxlogPath); err == nil {
		return applyNXLog(nxlogPath, host, portInt)
	}
	return applyWEF(host, portInt)
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	if backup, ok := params["config_backup"].(string); ok && backup != "" {
		nxlogPath := `C:\Program Files\nxlog\conf\nxlog.conf`
		if err := os.WriteFile(nxlogPath, []byte(backup), 0644); err == nil {
			return map[string]any{"rolled_back": true, "method": "nxlog"}, nil
		}
	}
	if err := exec.Command("wecutil", "ds", "NexplaneForwarder").Run(); err != nil {
		return nil, fmt.Errorf("removing WEF subscription: %w", err)
	}
	return map[string]any{"rolled_back": true, "method": "wef"}, nil
}

func applyNXLog(cfgPath, host string, port int) (map[string]any, error) {
	existing, _ := os.ReadFile(cfgPath)
	snapshot := string(existing)
	block := fmt.Sprintf(`%s
<Output nexplane_out>
    Module  om_udp
    Host    %s
    Port    %d
    Exec    to_syslog_bsd();
</Output>
<Route nexplane_route>
    Path    eventlog => nexplane_out
</Route>
%s
`, nexplaneBegin, host, port, nexplaneEnd)
	cleaned := removeNexplaneBlock(snapshot)
	if err := os.WriteFile(cfgPath, []byte(cleaned+"\n"+block), 0644); err != nil {
		return nil, fmt.Errorf("writing nxlog.conf: %w", err)
	}
	exec.Command("net", "stop", "nxlog").Run()
	exec.Command("net", "start", "nxlog").Run()
	return map[string]any{
		"action": "configure_syslog", "applied": true,
		"config_backup": snapshot, "method": "nxlog",
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func applyWEF(host string, port int) (map[string]any, error) {
	subFile := filepath.Join(os.TempDir(), "nexplane-wef.xml")
	xml := fmt.Sprintf(`<Subscription xmlns="http://schemas.microsoft.com/2006/03/windows/events/subscription">
  <SubscriptionId>NexplaneForwarder</SubscriptionId>
  <SubscriptionType>CollectorInitiated</SubscriptionType>
  <EventSources><EventSource><Address>%s</Address></EventSource></EventSources>
</Subscription>`, host)
	if err := os.WriteFile(subFile, []byte(xml), 0644); err != nil {
		return nil, fmt.Errorf("writing WEF subscription: %w", err)
	}
	if err := exec.Command("wecutil", "cs", subFile).Run(); err != nil {
		return nil, fmt.Errorf("wecutil cs: %w", err)
	}
	return map[string]any{
		"action": "configure_syslog", "applied": true,
		"method": "wef", "subscription_id": "NexplaneForwarder",
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func removeNexplaneBlock(content string) string {
	lines := strings.Split(content, "\n")
	var out []string
	inBlock := false
	for _, line := range lines {
		if strings.TrimSpace(line) == nexplaneBegin {
			inBlock = true
			continue
		}
		if strings.TrimSpace(line) == nexplaneEnd {
			inBlock = false
			continue
		}
		if !inBlock {
			out = append(out, line)
		}
	}
	return strings.Join(out, "\n")
}

