//go:build linux

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
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
			key := fmt.Sprintf("%s|%v|%s", f["dst_ip"], f["dst_port"], f["protocol"])
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

// EbpfLsmSoakExecute observes system call activity during the soak window by
// enumerating open file descriptors and memory maps of running processes.
// This gives us the syscall+path pairs needed by the LSM synthesizer.
func EbpfLsmSoakExecute(params map[string]any) (map[string]any, error) {
	dur, ok := params["duration_seconds"]
	if !ok {
		return nil, fmt.Errorf("duration_seconds is required")
	}
	seconds := int(toFloat(dur))
	if seconds <= 0 {
		seconds = 30
	}

	// Sample process file activity at start, middle, and end of window
	seen := map[string]bool{}
	var events []map[string]any
	interval := time.Duration(seconds/3+1) * time.Second
	deadline := time.Now().Add(time.Duration(seconds) * time.Second)
	for time.Now().Before(deadline) {
		collectProcEvents(seen, &events)
		time.Sleep(interval)
	}
	collectProcEvents(seen, &events)

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

// collectProcEvents scans /proc/*/maps and /proc/*/fd to build file-access events.
func collectProcEvents(seen map[string]bool, events *[]map[string]any) {
	procs, _ := filepath.Glob("/proc/[0-9]*/maps")
	for _, mapsPath := range procs {
		pid := filepath.Base(filepath.Dir(mapsPath))
		comm := readComm(pid)
		data, err := os.ReadFile(mapsPath)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(data), "\n") {
			// maps lines: addr perms offset dev inode pathname
			fields := strings.Fields(line)
			if len(fields) < 6 {
				continue
			}
			path := fields[5]
			if !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "/proc") || strings.HasPrefix(path, "/dev") || strings.HasPrefix(path, "/sys") {
				continue
			}
			// Determine syscall from permissions (r = read/mmap, w = write)
			perms := fields[1]
			syscall := "read"
			if strings.Contains(perms, "x") {
				syscall = "mmap"
			}
			key := fmt.Sprintf("%s|%s|%s", syscall, path, comm)
			if !seen[key] {
				seen[key] = true
				*events = append(*events, map[string]any{
					"syscall": syscall,
					"path":    path,
					"process": comm,
				})
			}
		}
	}
}

func readComm(pid string) string {
	data, err := os.ReadFile("/proc/" + pid + "/comm")
	if err != nil {
		return "*"
	}
	return strings.TrimSpace(string(data))
}

// parseSSLine parses a line from `ss -tanup` output.
// Column layout: Netid State Recv-Q Send-Q Local Peer [Process]
func parseSSLine(line string) map[string]any {
	fields := strings.Fields(line)
	if len(fields) < 6 {
		return nil
	}
	proto := fields[0]
	if proto != "tcp" && proto != "udp" {
		return nil
	}
	// fields[4] = Local Address:Port, fields[5] = Peer Address:Port
	peer := fields[5]
	if peer == "*" || peer == "0.0.0.0:*" || peer == "[::]:*" {
		return nil // skip LISTEN-only sockets
	}

	dstIP, dstPort := splitAddrPort(peer)
	if dstPort == 0 {
		return nil
	}

	// Extract process name from optional fields[6]: users:(("name",pid=N,fd=M))
	process := "*"
	if len(fields) > 6 {
		proc := fields[6]
		if i1 := strings.Index(proc, `"`); i1 >= 0 {
			if i2 := strings.Index(proc[i1+1:], `"`); i2 >= 0 {
				process = proc[i1+1 : i1+1+i2]
			}
		}
	}

	return map[string]any{
		"dst_ip":   dstIP,
		"dst_port": dstPort,
		"protocol": proto,
		"process":  process,
	}
}

func splitAddrPort(s string) (string, int) {
	idx := strings.LastIndex(s, ":")
	if idx < 0 {
		return s, 0
	}
	ip := s[:idx]
	port, err := strconv.Atoi(s[idx+1:])
	if err != nil {
		return ip, 0
	}
	return ip, port
}
