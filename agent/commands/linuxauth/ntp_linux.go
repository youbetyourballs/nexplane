//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func detectNTPDaemon() (configPath, svcName string) {
	for _, p := range []string{"/etc/chrony.conf", "/etc/chrony/chrony.conf"} {
		if _, err := os.Stat(p); err == nil {
			return p, "chronyd"
		}
	}
	if _, err := os.Stat("/etc/systemd/timesyncd.conf"); err == nil {
		return "/etc/systemd/timesyncd.conf", "systemd-timesyncd"
	}
	return "/etc/ntp.conf", "ntpd"
}

func ntpExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_ntp requires root privileges")
	}

	serversRaw, _ := params["servers"].([]any)
	servers := make([]string, 0, len(serversRaw))
	for _, s := range serversRaw {
		if str, ok := s.(string); ok {
			servers = append(servers, str)
		}
	}

	iburst := true
	if v, ok := params["require_iburst"].(bool); ok {
		iburst = v
	}
	makestep := "1.0 3"
	if v, ok := params["makestep"].(string); ok && v != "" {
		makestep = v
	}

	configPath, svcName := detectNTPDaemon()
	configData, _ := os.ReadFile(configPath)
	snapshot := string(configData)

	var serverLines []string
	for _, s := range servers {
		line := "server " + s
		if iburst {
			line += " iburst"
		}
		serverLines = append(serverLines, line)
	}
	serverBlock := strings.Join(serverLines, "\n")

	content := snapshot
	if svcName == "chronyd" {
		var kept []string
		for _, line := range strings.Split(content, "\n") {
			if strings.HasPrefix(line, "server ") || strings.HasPrefix(line, "pool ") {
				continue
			}
			kept = append(kept, line)
		}
		content = strings.Join(kept, "\n")
		extra := ""
		if !strings.Contains(content, "makestep") {
			extra += "\nmakestep " + makestep
		}
		if !strings.Contains(content, "rtcsync") {
			extra += "\nrtcsync"
		}
		content = insertManagedBlock(content, serverBlock+extra)
	} else if svcName == "systemd-timesyncd" {
		lines := strings.Split(content, "\n")
		ntpLine := "NTP=" + strings.Join(servers, " ")
		replaced := false
		for i, l := range lines {
			if strings.HasPrefix(l, "NTP=") {
				lines[i] = ntpLine
				replaced = true
				break
			}
		}
		if !replaced {
			for i, l := range lines {
				if l == "[Time]" {
					lines = append(lines[:i+1], append([]string{ntpLine}, lines[i+1:]...)...)
					break
				}
			}
		}
		content = strings.Join(lines, "\n")
	} else {
		content = insertManagedBlock(content, serverBlock)
	}

	if err := os.WriteFile(configPath, []byte(content), 0644); err != nil {
		return nil, fmt.Errorf("writing NTP config: %w", err)
	}
	exec.Command("systemctl", "restart", svcName).Run() //nolint:errcheck

	return map[string]any{
		"daemon": svcName, "config_path": configPath,
		"servers_configured": servers,
		"snapshot":           snapshot,
		"applied_at":         time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ntpRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	configPath, _ := params["config_path"].(string)
	if configPath == "" {
		configPath, _ = detectNTPDaemon()
	}
	if err := os.WriteFile(configPath, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring NTP config: %w", err)
	}
	_, svcName := detectNTPDaemon()
	exec.Command("systemctl", "restart", svcName).Run() //nolint:errcheck
	return map[string]any{"rolled_back": true, "config_path": configPath}, nil
}
