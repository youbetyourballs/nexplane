//go:build linux

package linuxharden

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func apparmorLearnExecute(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		serviceName = "nexplane-learn"
	}

	if _, err := exec.LookPath("aa-status"); err != nil {
		return nil, fmt.Errorf("AppArmor not available: aa-status not found in PATH")
	}

	profileName := "nexplane-learn-" + serviceName
	profilePath := filepath.Join("/etc/apparmor.d", profileName)
	complain := fmt.Sprintf(`#include <tunables/global>
profile %s flags=(complain) {
  #include <abstractions/base>
  /** mrwklix,
  capability,
  network,
}
`, profileName)

	if err := os.WriteFile(profilePath, []byte(complain), 0644); err != nil {
		return nil, fmt.Errorf("writing learn profile: %w", err)
	}
	if out, err := exec.Command("apparmor_parser", "-r", profilePath).CombinedOutput(); err != nil {
		os.Remove(profilePath)
		return nil, fmt.Errorf("loading learn profile: %s: %w", out, err)
	}

	// Record audit log offset before observation
	logPath := "/var/log/audit/audit.log"
	if _, err := os.Stat(logPath); err != nil {
		logPath = "/var/log/syslog"
	}
	startOffset := int64(0)
	if info, err := os.Stat(logPath); err == nil {
		startOffset = info.Size()
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Parse new AVC lines since startOffset
	events := parseApparmorEvents(logPath, startOffset, profileName)

	// Cleanup temp profile
	exec.Command("apparmor_parser", "-R", profilePath).Run() //nolint:errcheck
	os.Remove(profilePath)

	return map[string]any{
		"action":           "apparmor_learn",
		"service_name":     serviceName,
		"duration_seconds": int(duration),
		"apparmor_events":  events,
		"event_count":      len(events),
	}, nil
}

func parseApparmorEvents(logPath string, startOffset int64, profileName string) []map[string]any {
	f, err := os.Open(logPath)
	if err != nil {
		return nil
	}
	defer f.Close()
	f.Seek(startOffset, 0) //nolint:errcheck

	seen := map[string]bool{}
	var events []map[string]any
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.Contains(line, `apparmor="ALLOWED"`) {
			continue
		}
		if !strings.Contains(line, profileName) {
			continue
		}
		event := parseAVCLine(line)
		if event == nil {
			continue
		}
		key := event["operation"].(string) + ":" + event["resource"].(string)
		if !seen[key] {
			seen[key] = true
			events = append(events, event)
		}
	}
	return events
}

func parseAVCLine(line string) map[string]any {
	fields := map[string]string{}
	for _, token := range strings.Fields(line) {
		if idx := strings.IndexByte(token, '='); idx > 0 {
			key := token[:idx]
			val := strings.Trim(token[idx+1:], `"`)
			fields[key] = val
		}
	}
	op := fields["operation"]
	if op == "" {
		return nil
	}

	resource := ""
	switch op {
	case "file_read", "file_write", "file_exec", "file_append", "file_link":
		resource = fields["name"]
	case "capable":
		op = "capability"
		resource = fields["capname"]
	case "connect", "bind", "accept", "listen", "sendmsg", "recvmsg":
		op = "network"
		proto := fields["family"]
		if proto == "" {
			proto = "inet"
		}
		resource = proto
	default:
		return nil
	}

	if resource == "" {
		return nil
	}
	return map[string]any{"operation": op, "resource": resource}
}

func apparmorLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "apparmor_learn_rollback", "status": "no_state_to_revert"}, nil
}
