//go:build linux

package configsyslog

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const nexplaneBegin = "# nexplane-managed-begin"
const nexplaneEnd = "# nexplane-managed-end"

func executeOS(params map[string]any) (map[string]any, error) {
	host, _ := params["destination_host"].(string)
	port := params["destination_port"]
	proto, _ := params["protocol"].(string)
	facility, _ := params["facility"].(string)

	if host == "" {
		return nil, fmt.Errorf("destination_host is required")
	}
	if port == nil {
		return nil, fmt.Errorf("destination_port is required")
	}
	if proto == "" {
		return nil, fmt.Errorf("protocol is required")
	}
	if facility == "" {
		facility = "*"
	}
	portInt := toInt(port)

	daemon, cfgPath := detectSyslogDaemon()
	existing, _ := os.ReadFile(cfgPath)
	snapshot := string(existing)

	var block string
	if daemon == "rsyslog" {
		prefix := "@"
		if strings.ToLower(proto) == "tcp" {
			prefix = "@@"
		}
		block = fmt.Sprintf("%s\n%s.* %s%s:%d\n%s\n", nexplaneBegin, facility, prefix, host, portInt, nexplaneEnd)
	} else {
		block = fmt.Sprintf(`%s
destination d_nexplane { network("%s" port(%d) transport("%s")); };
log { source(s_src); destination(d_nexplane); };
%s
`, nexplaneBegin, host, portInt, strings.ToLower(proto), nexplaneEnd)
	}

	cleaned := removeNexplaneBlock(snapshot)
	if err := os.WriteFile(cfgPath, []byte(cleaned+"\n"+block), 0644); err != nil {
		return nil, fmt.Errorf("writing %s: %w", cfgPath, err)
	}

	svc := "rsyslog"
	if daemon == "syslog-ng" {
		svc = "syslog-ng"
	}
	if err := exec.Command("systemctl", "restart", svc).Run(); err != nil {
		return nil, fmt.Errorf("restarting %s: %w", svc, err)
	}

	return map[string]any{
		"action":           "configure_syslog",
		"destination_host": host,
		"destination_port": portInt,
		"protocol":         proto,
		"applied":          true,
		"config_backup":    snapshot,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	backup, ok := params["config_backup"].(string)
	if !ok {
		return nil, fmt.Errorf("config_backup is required for rollback")
	}
	_, cfgPath := detectSyslogDaemon()
	if err := os.WriteFile(cfgPath, []byte(backup), 0644); err != nil {
		return nil, fmt.Errorf("restoring config: %w", err)
	}
	return map[string]any{"rolled_back": true}, nil
}

func detectSyslogDaemon() (string, string) {
	if _, err := os.Stat("/etc/rsyslog.conf"); err == nil {
		return "rsyslog", "/etc/rsyslog.conf"
	}
	return "syslog-ng", "/etc/syslog-ng/syslog-ng.conf"
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

func toInt(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case float64:
		return int(n)
	case int64:
		return int(n)
	}
	return 514
}
