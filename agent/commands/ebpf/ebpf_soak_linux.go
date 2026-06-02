//go:build linux

package ebpf

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func EbpfNetworkSoakExecute(params map[string]any) (map[string]any, error) {
	dur, ok := params["duration_seconds"]
	if !ok {
		return nil, fmt.Errorf("duration_seconds is required")
	}
	seconds := int(toFloat(dur))
	if seconds <= 0 {
		seconds = 30
	}

	seen := map[string]bool{}
	var flows []map[string]any

	deadline := time.Now().Add(time.Duration(seconds) * time.Second)
	for time.Now().Before(deadline) {
		out, err := exec.Command("ss", "-tanup").Output()
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}
		for _, line := range strings.Split(string(out), "\n") {
			f := parseSSLine(line)
			if f == nil {
				continue
			}
			key := fmt.Sprintf("%s|%s|%s", f["proto"], f["local_port"], f["remote_addr"])
			if !seen[key] {
				seen[key] = true
				flows = append(flows, f)
			}
		}
		time.Sleep(2 * time.Second)
	}

	return map[string]any{
		"flows":       flows,
		"flow_count":  len(flows),
		"duration":    seconds,
		"observed_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func EbpfNetworkSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

func EbpfLsmSoakExecute(params map[string]any) (map[string]any, error) {
	dur, ok := params["duration_seconds"]
	if !ok {
		return nil, fmt.Errorf("duration_seconds is required")
	}
	seconds := int(toFloat(dur))
	if seconds <= 0 {
		seconds = 30
	}

	time.Sleep(time.Duration(seconds) * time.Second)

	out, _ := exec.Command("ausearch", "-m", "AVC", "-ts", "boot").Output()
	seen := map[string]bool{}
	var events []map[string]any
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || seen[line] {
			continue
		}
		seen[line] = true
		events = append(events, map[string]any{"raw": line})
	}

	return map[string]any{
		"events":      events,
		"event_count": len(events),
		"duration":    seconds,
		"observed_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func EbpfLsmSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

func parseSSLine(line string) map[string]any {
	fields := strings.Fields(line)
	if len(fields) < 5 {
		return nil
	}
	proto := fields[0]
	if proto != "tcp" && proto != "udp" {
		return nil
	}
	local := fields[3]
	remote := fields[4]
	localPort := ""
	if idx := strings.LastIndex(local, ":"); idx >= 0 {
		localPort = local[idx+1:]
	}
	return map[string]any{
		"proto":       proto,
		"local_addr":  local,
		"local_port":  localPort,
		"remote_addr": remote,
	}
}

func toFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int:
		return float64(x)
	case int64:
		return float64(x)
	}
	return 0
}
