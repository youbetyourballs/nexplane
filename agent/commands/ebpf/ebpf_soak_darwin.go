//go:build darwin

package ebpf

import (
	"os/exec"
	"strings"
	"time"
)

type flowEntry struct {
	Command    string
	PID        string
	Proto      string
	LocalAddr  string
	RemoteAddr string
	State      string
}

func EbpfNetworkSoakExecute(params map[string]any) (map[string]any, error) {
	durationSeconds := toFloat(params["duration_seconds"])
	if durationSeconds <= 0 {
		durationSeconds = 30
	}

	out, _ := exec.Command("lsof", "-i", "-n", "-P").Output()

	var flows []map[string]any
	for i, line := range strings.Split(string(out), "\n") {
		if i == 0 || strings.TrimSpace(line) == "" {
			continue // skip header and blank lines
		}
		fields := strings.Fields(line)
		if len(fields) < 9 {
			continue
		}
		command := fields[0]
		pid := fields[1]
		typeField := strings.ToLower(fields[7])
		proto := "tcp"
		if strings.Contains(typeField, "udp") {
			proto = "udp"
		}
		name := fields[len(fields)-1]

		var localAddr, remoteAddr string
		if strings.Contains(name, "->") {
			parts := strings.SplitN(name, "->", 2)
			localAddr = parts[0]
			remoteAddr = parts[1]
		} else {
			localAddr = name
			remoteAddr = ""
		}

		fe := flowEntry{
			Command:    command,
			PID:        pid,
			Proto:      proto,
			LocalAddr:  localAddr,
			RemoteAddr: remoteAddr,
		}
		flows = append(flows, map[string]any{
			"command":     fe.Command,
			"pid":         fe.PID,
			"proto":       fe.Proto,
			"local_addr":  fe.LocalAddr,
			"remote_addr": fe.RemoteAddr,
		})
	}

	return map[string]any{
		"flows":        flows,
		"flow_count":   len(flows),
		"duration":     durationSeconds,
		"collected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func EbpfNetworkSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"no_op": true}, nil
}

func EbpfLsmSoakExecute(params map[string]any) (map[string]any, error) {
	durationSeconds := toFloat(params["duration_seconds"])
	if durationSeconds <= 0 {
		durationSeconds = 30
	}

	seen := map[string]bool{}
	var events []map[string]any

	collectLogLines := func(args []string) {
		out, err := exec.Command("log", args...).Output()
		if err != nil {
			return
		}
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			if !seen[line] {
				seen[line] = true
				events = append(events, map[string]any{
					"line":   line,
					"source": "unified_log",
				})
			}
		}
	}

	collectLogLines([]string{"show", "--predicate", `eventMessage contains "denied"`, "--last", "5m", "--style", "syslog"})
	collectLogLines([]string{"show", "--predicate", `subsystem == "com.apple.security"`, "--last", "5m", "--style", "syslog"})

	return map[string]any{
		"events":       events,
		"event_count":  len(events),
		"duration":     durationSeconds,
		"collected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func EbpfLsmSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"no_op": true}, nil
}
