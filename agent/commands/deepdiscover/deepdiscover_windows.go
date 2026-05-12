//go:build windows

package deepdiscover

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// execCommandWindows is the exec.Command hook, mockable in tests.
var execCommandWindows = exec.Command

// executeOS orchestrates deep discovery on Windows.
func executeOS(_ map[string]any) (*DeepDiscoveryResult, error) {
	var workloads []DiscoveredWorkload

	// Windows services
	serviceWorkloads := collectWindowsServices()
	workloads = append(workloads, serviceWorkloads...)

	// Docker containers (Docker Desktop on Windows)
	dockerWorkloads := detectDockerContainersWindows()
	workloads = append(workloads, dockerWorkloads...)

	// TCP connections
	tcpConns := collectWindowsTCPConnections()

	// Attach TCP connections to all service workloads
	for i := range workloads {
		workloads[i].OutboundConns = tcpConns
	}

	// Hybrid edges (v1: empty)
	hybridEdges := detectHybridEdgesWindows(workloads)

	// Ensure non-nil slices
	if workloads == nil {
		workloads = []DiscoveredWorkload{}
	}

	return &DeepDiscoveryResult{
		Workloads:   workloads,
		HybridEdges: hybridEdges,
		CollectedAt: nowISO(),
		OS:          "windows",
	}, nil
}

// collectWindowsServices discovers running Windows services via PowerShell.
func collectWindowsServices() []DiscoveredWorkload {
	out, err := execCommandWindows("powershell", "-NoProfile", "-Command",
		"Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object Name,DisplayName | ConvertTo-Json -Compress").Output()
	if err != nil {
		return []DiscoveredWorkload{}
	}

	raw := strings.TrimSpace(string(out))
	if raw == "" {
		return []DiscoveredWorkload{}
	}

	type svcEntry struct {
		Name        string `json:"Name"`
		DisplayName string `json:"DisplayName"`
	}

	// Handle both single object and array responses
	var services []svcEntry
	if strings.HasPrefix(raw, "[") {
		if err := json.Unmarshal([]byte(raw), &services); err != nil {
			return []DiscoveredWorkload{}
		}
	} else {
		var single svcEntry
		if err := json.Unmarshal([]byte(raw), &single); err != nil {
			return []DiscoveredWorkload{}
		}
		services = []svcEntry{single}
	}

	var workloads []DiscoveredWorkload
	for _, svc := range services {
		name := svc.Name
		if name == "" {
			name = svc.DisplayName
		}

		// Look up PID via tasklist
		pid := 0
		if name != "" {
			out2, err2 := execCommandWindows("tasklist", "/FI",
				fmt.Sprintf("IMAGENAME eq %s.exe", name),
				"/FO", "CSV", "/NH").Output()
			if err2 == nil {
				lines := strings.Split(strings.TrimSpace(string(out2)), "\n")
				if len(lines) > 0 {
					fields := strings.Split(lines[0], ",")
					if len(fields) >= 2 {
						fmt.Sscanf(strings.Trim(fields[1], `"`), "%d", &pid)
					}
				}
			}
		}

		// Collect config files from common Windows service directories
		var cfgFiles []string
		for _, baseDir := range []string{`C:\ProgramData`, `C:\Program Files`, `C:\Program Files (x86)`} {
			dir := filepath.Join(baseDir, name)
			if entries, err2 := os.ReadDir(dir); err2 == nil {
				for _, e := range entries {
					if !e.IsDir() {
						cfgFiles = append(cfgFiles, filepath.Join(dir, e.Name()))
					}
				}
			}
		}

		workloads = append(workloads, DiscoveredWorkload{
			Name:               name,
			RuntimeType:        RuntimeSystemd,
			ListeningPorts:     []PortEntry{},
			OutboundConns:      []ConnEdge{},
			InboundConns:       []ConnEdge{},
			IPCSockets:         []string{},
			DataDirectories:    []DirInfo{},
			Dependencies:       []string{},
			PIDFound:           pid > 0,
			EnvVarNames:        collectEnvVarNamesWindows(pid),
			OpenFiles:          collectOpenFilesWindows(pid),
			RuntimeDeps:        collectRuntimeDepsWindows(name),
			ConfigIntelligence: parseConfigFiles(cfgFiles),
		})
	}

	if workloads == nil {
		return []DiscoveredWorkload{}
	}
	return workloads
}

// collectWindowsTCPConnections collects established TCP connections via PowerShell.
func collectWindowsTCPConnections() []ConnEdge {
	out, err := execCommandWindows("powershell", "-NoProfile", "-Command",
		"Get-NetTCPConnection | Where-Object {$_.State -eq 'Established'} | Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort | ConvertTo-Json -Compress").Output()
	if err != nil {
		return []ConnEdge{}
	}

	raw := strings.TrimSpace(string(out))
	if raw == "" {
		return []ConnEdge{}
	}

	type connEntry struct {
		LocalAddress  string `json:"LocalAddress"`
		LocalPort     int    `json:"LocalPort"`
		RemoteAddress string `json:"RemoteAddress"`
		RemotePort    int    `json:"RemotePort"`
	}

	// Handle both single object and array responses
	var connections []connEntry
	if strings.HasPrefix(raw, "[") {
		if err := json.Unmarshal([]byte(raw), &connections); err != nil {
			return []ConnEdge{}
		}
	} else {
		var single connEntry
		if err := json.Unmarshal([]byte(raw), &single); err != nil {
			return []ConnEdge{}
		}
		connections = []connEntry{single}
	}

	var conns []ConnEdge
	for _, c := range connections {
		localAddr := c.LocalAddress + ":" + portStr(c.LocalPort)
		remoteAddr := c.RemoteAddress + ":" + portStr(c.RemotePort)
		conns = append(conns, ConnEdge{
			LocalAddr:  localAddr,
			RemoteAddr: remoteAddr,
			State:      "ESTABLISHED",
			Protocol:   "tcp",
		})
	}

	if conns == nil {
		return []ConnEdge{}
	}
	return conns
}

// detectDockerContainersWindows discovers Docker containers (Docker Desktop on Windows).
func detectDockerContainersWindows() []DiscoveredWorkload {
	out, err := execCommandWindows("docker", "ps", "--format", "{{json .}}").Output()
	if err != nil {
		// Docker Desktop not installed is normal
		return nil
	}

	var workloads []DiscoveredWorkload
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var row map[string]any
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			continue
		}
		name, _ := row["Names"].(string)
		id, _ := row["ID"].(string)
		image, _ := row["Image"].(string)
		workloads = append(workloads, DiscoveredWorkload{
			Name:            name,
			RuntimeType:     RuntimeDocker,
			ContainerID:     id,
			ImageName:       image,
			ListeningPorts:  []PortEntry{},
			OutboundConns:   []ConnEdge{},
			InboundConns:    []ConnEdge{},
			IPCSockets:      []string{},
			DataDirectories: []DirInfo{},
			Dependencies:    []string{},
		})
	}
	return workloads
}

// detectHybridEdgesWindows returns empty hybrid edges (v1).
func detectHybridEdgesWindows(_ []DiscoveredWorkload) []HybridEdge {
	return []HybridEdge{}
}

// collectEnvVarNamesWindows returns environment variable key names for a process via wmic.
// Returns empty slice for PID 0 or on error.
func collectEnvVarNamesWindows(pid int) []string {
	if pid <= 0 {
		return []string{}
	}
	out, err := execCommandWindows("wmic", "process",
		fmt.Sprintf("where ProcessId=%d", pid),
		"get", "EnvironmentVariables", "/format:csv").Output()
	if err != nil {
		return []string{}
	}
	names := []string{}
	seen := map[string]bool{}
	for _, line := range strings.Split(string(out), "\n") {
		idx := strings.LastIndex(line, ",")
		if idx < 0 {
			continue
		}
		pairs := strings.Split(line[idx+1:], ";")
		for _, pair := range pairs {
			eqIdx := strings.IndexByte(pair, '=')
			if eqIdx <= 0 {
				continue
			}
			key := strings.TrimSpace(pair[:eqIdx])
			if key != "" && !seen[key] {
				seen[key] = true
				names = append(names, key)
			}
		}
	}
	return names
}

// collectOpenFilesWindows returns open file paths for a process using handle.exe.
// Returns empty slice if handle.exe is not available or on error.
func collectOpenFilesWindows(pid int) []string {
	if pid <= 0 {
		return []string{}
	}
	out, err := execCommandWindows("handle.exe", "-p", fmt.Sprintf("%d", pid), "-nobanner").Output()
	if err != nil {
		return []string{}
	}
	files := []string{}
	seen := map[string]bool{}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if strings.Contains(line, "File") && strings.Contains(line, `:\`) {
			// Find the drive letter path (e.g., C:\path\to\file)
			bsIdx := strings.LastIndex(line, `\`)
			if bsIdx < 0 {
				continue
			}
			start := strings.LastIndex(line[:bsIdx], " ")
			if start >= 0 {
				path := strings.TrimSpace(line[start:])
				if len(path) > 2 && path[1] == ':' && !seen[path] {
					seen[path] = true
					files = append(files, path)
				}
			}
		}
	}
	return files
}

// collectRuntimeDepsWindows returns DLL paths loaded by a named binary using PowerShell.
// Returns empty slice if binary is empty or on error.
func collectRuntimeDepsWindows(binary string) []string {
	if binary == "" {
		return []string{}
	}
	procName := strings.TrimSuffix(filepath.Base(binary), ".exe")
	script := fmt.Sprintf(
		`(Get-Process -Name '%s' -ErrorAction SilentlyContinue | Select-Object -First 1).Modules.FileName -join ","`,
		procName,
	)
	out, err := execCommandWindows("powershell", "-NoProfile", "-Command", script).Output()
	if err != nil {
		return []string{}
	}
	deps := []string{}
	seen := map[string]bool{}
	for _, p := range strings.Split(strings.TrimSpace(string(out)), ",") {
		p = strings.TrimSpace(p)
		if p != "" && strings.HasSuffix(strings.ToLower(p), ".dll") && !seen[p] {
			seen[p] = true
			deps = append(deps, p)
		}
	}
	return deps
}
