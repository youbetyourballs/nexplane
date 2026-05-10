//go:build windows

package deepdiscover

import (
	"encoding/json"
	"os/exec"
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
		workloads = append(workloads, DiscoveredWorkload{
			Name:            name,
			RuntimeType:     RuntimeSystemd,
			ListeningPorts:  []PortEntry{},
			OutboundConns:   []ConnEdge{},
			InboundConns:    []ConnEdge{},
			IPCSockets:      []string{},
			DataDirectories: []DirInfo{},
			Dependencies:    []string{},
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
