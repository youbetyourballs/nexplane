//go:build linux

package deepdiscover

import (
	"encoding/json"
	"os"
	"os/exec"
	"strconv"
	"strings"
)

// execCommandLinux is the exec.Command hook, mockable in tests.
var execCommandLinux = exec.Command

// executeOS orchestrates deep discovery on Linux.
func executeOS(_ map[string]any) (*DeepDiscoveryResult, error) {
	var workloads []DiscoveredWorkload

	// Systemd services
	systemdWorkloads := collectSystemdServicesLinux()
	workloads = append(workloads, systemdWorkloads...)

	// Container runtimes
	containerWorkloads := detectContainerRuntimesLinux()
	workloads = append(workloads, containerWorkloads...)

	// Kubernetes pods
	kubeWorkloads := detectKubePodsLinux()
	workloads = append(workloads, kubeWorkloads...)

	listeningPorts := collectListeningPortsLinux()
	establishedConns := collectEstablishedConnsLinux()

	// Attach all listening ports and established connections to workloads.
	// v1: attach ports to all workloads (PID-level attribution deferred to v2).
	// Connections are stored at result level for fleet cross-reference.
	for i := range workloads {
		workloads[i].ListeningPorts = listeningPorts
		workloads[i].OutboundConns = establishedConns
	}

	// Attach IPC sockets
	attachIPCSockets(workloads)

	// Detect hybrid edges
	hybridEdges := detectHybridEdges(workloads)

	// Ensure non-nil slices
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
		OS:          "linux",
	}, nil
}

// collectSystemdServicesLinux discovers running systemd services.
func collectSystemdServicesLinux() []DiscoveredWorkload {
	out, err := execCommandLinux("systemctl", "list-units", "--type=service",
		"--state=running", "--no-pager", "--no-legend").Output()
	if err != nil {
		return nil
	}

	var workloads []DiscoveredWorkload
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 1 {
			continue
		}
		unitName := fields[0]
		if unitName == "" {
			continue
		}

		deps := collectSystemdDepsLinux(unitName)
		workloads = append(workloads, DiscoveredWorkload{
			Name:               strings.TrimSuffix(unitName, ".service"),
			RuntimeType:        RuntimeSystemd,
			SystemdUnit:        unitName,
			ListeningPorts:     []PortEntry{},
			OutboundConns:      []ConnEdge{},
			InboundConns:       []ConnEdge{},
			IPCSockets:         []string{},
			DataDirectories:    []DirInfo{},
			Dependencies:       deps,
			PIDFound:           false,
			EnvVarNames:        []string{},
			OpenFiles:          []string{},
			RuntimeDeps:        []string{},
			ConfigIntelligence: []ConfigEntry{},
		})
	}
	return workloads
}

// collectSystemdDepsLinux returns dependency unit names for a given systemd unit.
func collectSystemdDepsLinux(unit string) []string {
	out, err := execCommandLinux("systemctl", "list-dependencies", unit, "--plain", "--no-pager").Output()
	if err != nil {
		return []string{}
	}

	var deps []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		dep := strings.TrimSpace(line)
		if dep != "" && dep != unit {
			deps = append(deps, dep)
		}
	}
	if deps == nil {
		return []string{}
	}
	return deps
}

// collectListeningPortsLinux discovers all listening ports via ss.
func collectListeningPortsLinux() []PortEntry {
	out, err := execCommandLinux("ss", "-tulnp").Output()
	if err != nil {
		return []PortEntry{}
	}

	var ports []PortEntry
	lines := strings.Split(string(out), "\n")
	if len(lines) <= 1 {
		return []PortEntry{}
	}
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		proto := fields[0] // tcp/udp/tcp6/udp6
		localAddr := fields[4]

		idx := strings.LastIndex(localAddr, ":")
		if idx < 0 {
			continue
		}
		addrPart := localAddr[:idx]
		portPart := localAddr[idx+1:]

		port, err := strconv.Atoi(portPart)
		if err != nil || port == 0 {
			continue
		}

		protocol := "tcp"
		if strings.HasPrefix(proto, "udp") {
			protocol = "udp"
		}

		ports = append(ports, PortEntry{
			Port:     port,
			Protocol: protocol,
			Address:  addrPart,
		})
	}
	if ports == nil {
		return []PortEntry{}
	}
	return ports
}

// collectEstablishedConnsLinux returns all established TCP/UDP connections.
func collectEstablishedConnsLinux() []ConnEdge {
	out, err := execCommandLinux("ss", "-tunap", "state", "established").Output()
	if err != nil {
		return []ConnEdge{}
	}

	var conns []ConnEdge
	lines := strings.Split(string(out), "\n")
	if len(lines) <= 1 {
		return []ConnEdge{}
	}
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 6 {
			continue
		}
		proto := fields[0]
		localAddr := fields[4]
		remoteAddr := fields[5]

		protocol := "tcp"
		if strings.HasPrefix(proto, "udp") {
			protocol = "udp"
		}

		conns = append(conns, ConnEdge{
			LocalAddr:  localAddr,
			RemoteAddr: remoteAddr,
			State:      "established",
			Protocol:   protocol,
		})
	}
	if conns == nil {
		return []ConnEdge{}
	}
	return conns
}

// detectContainerRuntimesLinux tries docker, crictl, and podman.
func detectContainerRuntimesLinux() []DiscoveredWorkload {
	var workloads []DiscoveredWorkload

	if dw := collectDockerWorkloads(); dw != nil {
		workloads = append(workloads, dw...)
	}
	if cw := collectCrictlWorkloads(); cw != nil {
		workloads = append(workloads, cw...)
	}
	if pw := collectPodmanWorkloads(); pw != nil {
		workloads = append(workloads, pw...)
	}

	if workloads == nil {
		return []DiscoveredWorkload{}
	}
	return workloads
}

func collectDockerWorkloads() []DiscoveredWorkload {
	out, err := execCommandLinux("docker", "ps", "--format", "{{json .}}").Output()
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
			PIDFound:           false,
			EnvVarNames:        []string{},
			OpenFiles:          []string{},
			RuntimeDeps:        []string{},
			ConfigIntelligence: []ConfigEntry{},
		})
	}
	return workloads
}

func collectCrictlWorkloads() []DiscoveredWorkload {
	out, err := execCommandLinux("crictl", "ps", "-o", "json").Output()
	if err != nil {
		return nil
	}

	var payload struct {
		Containers []struct {
			ID       string `json:"id"`
			Metadata struct {
				Name string `json:"name"`
			} `json:"metadata"`
			Image struct {
				Image string `json:"image"`
			} `json:"image"`
		} `json:"containers"`
	}
	if err := json.Unmarshal(out, &payload); err != nil {
		return nil
	}

	var workloads []DiscoveredWorkload
	for _, c := range payload.Containers {
		workloads = append(workloads, DiscoveredWorkload{
			Name:               c.Metadata.Name,
			RuntimeType:        RuntimeContainerd,
			ContainerID:        c.ID,
			ImageName:          c.Image.Image,
			ListeningPorts:     []PortEntry{},
			OutboundConns:      []ConnEdge{},
			InboundConns:       []ConnEdge{},
			IPCSockets:         []string{},
			DataDirectories:    []DirInfo{},
			Dependencies:       []string{},
			PIDFound:           false,
			EnvVarNames:        []string{},
			OpenFiles:          []string{},
			RuntimeDeps:        []string{},
			ConfigIntelligence: []ConfigEntry{},
		})
	}
	return workloads
}

func collectPodmanWorkloads() []DiscoveredWorkload {
	out, err := execCommandLinux("podman", "ps", "--format", "json").Output()
	if err != nil {
		return nil
	}

	var containers []struct {
		ID    string   `json:"Id"`
		Names []string `json:"Names"`
		Image string   `json:"Image"`
	}
	if err := json.Unmarshal(out, &containers); err != nil {
		return nil
	}

	var workloads []DiscoveredWorkload
	for _, c := range containers {
		name := c.ID
		if len(c.Names) > 0 {
			name = c.Names[0]
		}
		workloads = append(workloads, DiscoveredWorkload{
			Name:               name,
			RuntimeType:        RuntimePodman,
			ContainerID:        c.ID,
			ImageName:          c.Image,
			ListeningPorts:     []PortEntry{},
			OutboundConns:      []ConnEdge{},
			InboundConns:       []ConnEdge{},
			IPCSockets:         []string{},
			DataDirectories:    []DirInfo{},
			Dependencies:       []string{},
			PIDFound:           false,
			EnvVarNames:        []string{},
			OpenFiles:          []string{},
			RuntimeDeps:        []string{},
			ConfigIntelligence: []ConfigEntry{},
		})
	}
	return workloads
}

// detectKubePodsLinux checks for kubelet/containerd sockets and runs kubectl.
func detectKubePodsLinux() []DiscoveredWorkload {
	kubeletSock := "/var/run/kubelet.sock"
	containerdSock := "/run/containerd/containerd.sock"

	socketPaths := []string{kubeletSock, containerdSock}
	found := false
	for _, p := range socketPaths {
		if fi, err := os.Stat(p); err == nil && fi.Mode()&os.ModeSocket != 0 {
			found = true
			break
		}
	}
	if !found {
		return nil
	}

	out, err := execCommandLinux("kubectl", "get", "pods", "--all-namespaces", "-o", "json").Output()
	if err != nil {
		return nil
	}

	var payload struct {
		Items []struct {
			Metadata struct {
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"metadata"`
			Spec struct {
				Containers []struct {
					Image string `json:"image"`
				} `json:"containers"`
			} `json:"spec"`
		} `json:"items"`
	}
	if err := json.Unmarshal(out, &payload); err != nil {
		return nil
	}

	var workloads []DiscoveredWorkload
	for _, item := range payload.Items {
		image := ""
		if len(item.Spec.Containers) > 0 {
			image = item.Spec.Containers[0].Image
		}
		workloads = append(workloads, DiscoveredWorkload{
			Name:               item.Metadata.Name,
			RuntimeType:        RuntimeKubePod,
			PodName:            item.Metadata.Name,
			ImageName:          image,
			ListeningPorts:     []PortEntry{},
			OutboundConns:      []ConnEdge{},
			InboundConns:       []ConnEdge{},
			IPCSockets:         []string{},
			DataDirectories:    []DirInfo{},
			Dependencies:       []string{},
			PIDFound:           false,
			EnvVarNames:        []string{},
			OpenFiles:          []string{},
			RuntimeDeps:        []string{},
			ConfigIntelligence: []ConfigEntry{},
		})
	}
	return workloads
}

// detectHybridEdges finds cross-runtime connections.
func detectHybridEdges(workloads []DiscoveredWorkload) []HybridEdge {
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
					if strings.HasSuffix(conn.RemoteAddr, ":"+portStr(port.Port)) {
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

// attachIPCSockets discovers Unix domain sockets and attaches them to workloads.
func attachIPCSockets(workloads []DiscoveredWorkload) {
	out, err := execCommandLinux("sh", "-c",
		"ls /var/run/*.sock /run/*.sock /tmp/*.sock 2>/dev/null").Output()
	if err != nil || len(out) == 0 {
		return
	}

	var socks []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		s := strings.TrimSpace(line)
		if s != "" {
			socks = append(socks, s)
		}
	}

	// Attach all sockets to all workloads (best-effort heuristic)
	for i := range workloads {
		workloads[i].IPCSockets = append(workloads[i].IPCSockets, socks...)
	}

}
