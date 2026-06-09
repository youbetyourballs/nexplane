//go:build darwin

package configsyslog

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

var darwinSyslogConf = "/etc/syslog.conf"
var execCommandSyslogDarwin = exec.Command

func executeOS(params map[string]any) (map[string]any, error) {
	host, _ := params["destination_host"].(string)
	if host == "" {
		return nil, fmt.Errorf("destination_host is required")
	}
	port := params["destination_port"]
	if port == nil {
		return nil, fmt.Errorf("destination_port is required")
	}
	proto, _ := params["protocol"].(string)
	if proto == "" {
		proto = "udp"
	}
	facility, _ := params["facility"].(string)
	if facility == "" {
		facility = "*.*"
	}
	portInt := toInt(port)
	if portInt == 0 {
		portInt = 514
	}

	existing, _ := os.ReadFile(darwinSyslogConf)
	snapshot := string(existing)

	prefix := "@"
	if proto == "tcp" {
		prefix = "@@"
	}
	forwardLine := fmt.Sprintf("%s\t%s%s:%d", facility, prefix, host, portInt)

	content := removeSyslogBlock(snapshot)
	block := fmt.Sprintf("\n%s\n%s\n%s\n", nexplaneBegin, forwardLine, nexplaneEnd)
	content += block

	if err := os.WriteFile(darwinSyslogConf, []byte(content), 0644); err != nil {
		return nil, fmt.Errorf("writing syslog.conf: %w", err)
	}

	execCommandSyslogDarwin("launchctl", "kickstart", "-k", "system/com.apple.syslogd").Run()

	return map[string]any{
		"daemon":      "syslogd",
		"config_path": darwinSyslogConf,
		"snapshot":    snapshot,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if err := os.WriteFile(darwinSyslogConf, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring syslog.conf: %w", err)
	}
	execCommandSyslogDarwin("launchctl", "kickstart", "-k", "system/com.apple.syslogd").Run()
	return map[string]any{"rolled_back": true}, nil
}

func removeSyslogBlock(content string) string {
	lines := strings.Split(content, "\n")
	var out []string
	inBlock := false
	for _, l := range lines {
		t := strings.TrimSpace(l)
		if t == nexplaneBegin {
			inBlock = true
			continue
		}
		if t == nexplaneEnd {
			inBlock = false
			continue
		}
		if !inBlock {
			out = append(out, l)
		}
	}
	return strings.Join(out, "\n")
}
