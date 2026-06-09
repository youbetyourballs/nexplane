//go:build darwin

package deepdiscover

import (
	"encoding/json"
	"os/exec"
	"strconv"
	"strings"
)

var execCommandDarwin = exec.Command

func executeOS(_ map[string]any) (*DeepDiscoveryResult, error) {
	var workloads []DiscoveredWorkload

	workloads = append(workloads, collectLaunchdServicesDarwin()...)
	workloads = append(workloads, detectContainerRuntimesDarwin()...)

	listeningPorts := collectListeningPortsDarwin()
	establishedConns := collectEstablishedConnsDarwin()

	for i := range workloads {
		workloads[i].ListeningPorts = listeningPorts
		workloads[i].OutboundConns = establishedConns
	}

	hybridEdges := detectHybridEdgesDarwin(workloads)

	if workloads == nil {
		workloads = []DiscoveredWorkload{}
	}
	if hybridEdges == nil {
		hybridEdges = []HybridEdge{}
	}

	return &DeepDiscoveryResult{
		Workloads:   workloads,
		HybridEdges: hybridEdges,
		CollectedAt: nowISO(),
		OS:          "darwin",
	}, nil
}

func collectLaunchdServicesDarwin() []DiscoveredWorkload {
	out, err := execCommandDarwin("launchctl", "list").Output()
	if err != nil {
		return nil
	}
	var workloads []DiscoveredWorkload
	lines := strings.Split(string(out), "\n")
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 3 || fields[0] == "-" {
			continue
		}
		label := fields[2]
		workloads = append(workloads, DiscoveredWorkload{
			Name:               label,
			RuntimeType:        RuntimeSystemd, // closest analog for launchd
			ListeningPorts:     []PortEntry{},
			OutboundConns:      []ConnEdge{},
			InboundConns:       []ConnEdge{},
			IPCSockets:         []string{},
			DataDirectories:    []DirInfo{},
			Dependencies:       []string{},
			EnvVarNames:        []string{},
			OpenFiles:          []string{},
			RuntimeDeps:        []string{},
			ConfigIntelligence: []ConfigEntry{},
		})
	}
	return workloads
}

func detectContainerRuntimesDarwin() []DiscoveredWorkload {
	out, err := execCommandDarwin("docker", "ps", "--format", "{{json .}}").Output()
	if err != nil {
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
			Name:               name,
			RuntimeType:        RuntimeDocker,
			ContainerID:        id,
			ImageName:          image,
			ListeningPorts:     []PortEntry{},
			OutboundConns:      []ConnEdge{},
			InboundConns:       []ConnEdge{},
			IPCSockets:         []string{},
			DataDirectories:    []DirInfo{},
			Dependencies:       []string{},
			EnvVarNames:        []string{},
			OpenFiles:          []string{},
			RuntimeDeps:        []string{},
			ConfigIntelligence: []ConfigEntry{},
		})
	}
	return workloads
}

func collectListeningPortsDarwin() []PortEntry {
	out, err := execCommandDarwin("netstat", "-an", "-p", "tcp").Output()
	if err != nil {
		return []PortEntry{}
	}
	var ports []PortEntry
	for _, line := range strings.Split(string(out), "\n") {
		if !strings.Contains(line, "LISTEN") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		addr := fields[3]
		if idx := strings.LastIndex(addr, "."); idx != -1 {
			if p, err := strconv.Atoi(addr[idx+1:]); err == nil {
				addrPart := addr[:idx]
				ports = append(ports, PortEntry{
					Port:     p,
					Protocol: "tcp",
					Address:  addrPart,
				})
			}
		}
	}
	if ports == nil {
		return []PortEntry{}
	}
	return ports
}

func collectEstablishedConnsDarwin() []ConnEdge {
	out, err := execCommandDarwin("netstat", "-an", "-p", "tcp").Output()
	if err != nil {
		return []ConnEdge{}
	}
	var conns []ConnEdge
	for _, line := range strings.Split(string(out), "\n") {
		if !strings.Contains(line, "ESTABLISHED") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		conns = append(conns, ConnEdge{
			LocalAddr:  fields[3],
			RemoteAddr: fields[4],
			State:      "established",
			Protocol:   "tcp",
		})
	}
	if conns == nil {
		return []ConnEdge{}
	}
	return conns
}

func detectHybridEdgesDarwin(workloads []DiscoveredWorkload) []HybridEdge {
	var edges []HybridEdge
	for i := range workloads {
		for _, port := range workloads[i].ListeningPorts {
			for j := range workloads {
				if i == j {
					continue
				}
				if string(workloads[i].RuntimeType) == string(workloads[j].RuntimeType) {
					continue
				}
				for _, conn := range workloads[j].OutboundConns {
					if strings.HasSuffix(conn.RemoteAddr, "."+portStr(port.Port)) ||
						strings.HasSuffix(conn.RemoteAddr, ":"+portStr(port.Port)) {
						edges = append(edges, HybridEdge{
							SourceName:    workloads[j].Name,
							SourceRuntime: string(workloads[j].RuntimeType),
							TargetName:    workloads[i].Name,
							TargetRuntime: string(workloads[i].RuntimeType),
							Port:          port.Port,
							Protocol:      port.Protocol,
						})
					}
				}
			}
		}
	}
	return edges
}
