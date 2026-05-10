package deepdiscover

import (
	"fmt"
	"time"
)

// RuntimeType identifies the container/process runtime.
type RuntimeType string

const (
	RuntimeSystemd     RuntimeType = "systemd"
	RuntimeDocker      RuntimeType = "docker"
	RuntimeContainerd  RuntimeType = "containerd"
	RuntimePodman      RuntimeType = "podman"
	RuntimeKubePod     RuntimeType = "kubernetes_pod"
	RuntimeProcessOnly RuntimeType = "process_only"
)

// PortEntry represents a listening port.
type PortEntry struct {
	Port     int    `json:"port"`
	Protocol string `json:"protocol"`
	Address  string `json:"address"`
}

// ConnEdge represents a network connection.
type ConnEdge struct {
	LocalAddr  string `json:"local_addr"`
	RemoteAddr string `json:"remote_addr"`
	RemoteHost string `json:"remote_host,omitempty"`
	State      string `json:"state"`
	Protocol   string `json:"protocol"`
}

// DirInfo represents a directory and its size.
type DirInfo struct {
	Path      string `json:"path"`
	SizeBytes int64  `json:"size_bytes"`
}

// DiscoveredWorkload represents a running workload (service, container, pod, process).
type DiscoveredWorkload struct {
	Name            string      `json:"name"`
	RuntimeType     RuntimeType `json:"runtime_type"`
	Pid             int         `json:"pid,omitempty"`
	SystemdUnit     string      `json:"systemd_unit,omitempty"`
	ContainerID     string      `json:"container_id,omitempty"`
	ImageName       string      `json:"image_name,omitempty"`
	PodName         string      `json:"pod_name,omitempty"`
	ListeningPorts  []PortEntry `json:"listening_ports"`
	OutboundConns   []ConnEdge  `json:"outbound_connections"`
	InboundConns    []ConnEdge  `json:"inbound_connections"`
	IPCSockets      []string    `json:"ipc_sockets"`
	DataDirectories []DirInfo   `json:"data_directories"`
	Dependencies    []string    `json:"dependencies"`
}

// HybridEdge represents a cross-runtime network connection.
type HybridEdge struct {
	SourceName    string `json:"source_name"`
	SourceRuntime string `json:"source_runtime"`
	TargetName    string `json:"target_name"`
	TargetRuntime string `json:"target_runtime"`
	Port          int    `json:"port"`
	Protocol      string `json:"protocol"`
}

// DeepDiscoveryResult is the top-level result returned by the command.
type DeepDiscoveryResult struct {
	Workloads   []DiscoveredWorkload `json:"workloads"`
	HybridEdges []HybridEdge        `json:"hybrid_edges"`
	CollectedAt string              `json:"collected_at"`
	OS          string              `json:"os"`
}

// Execute is the CommandFunc entry point called by the agent framework.
func Execute(params map[string]any) (map[string]any, error) {
	result, err := executeOS(params)
	if err != nil {
		return nil, fmt.Errorf("deep_discover: %w", err)
	}

	return map[string]any{
		"action":       "deep_discover",
		"workloads":    workloadsToMaps(result.Workloads),
		"hybrid_edges": hybridEdgesToMaps(result.HybridEdges),
		"collected_at": result.CollectedAt,
		"os":           result.OS,
	}, nil
}

// nowISO returns the current UTC time in RFC3339 format.
func nowISO() string {
	return time.Now().UTC().Format(time.RFC3339)
}

// portStr converts a port integer to a string.
func portStr(p int) string {
	return fmt.Sprintf("%d", p)
}

// workloadsToMaps converts []DiscoveredWorkload to []map[string]any.
func workloadsToMaps(workloads []DiscoveredWorkload) []map[string]any {
	out := make([]map[string]any, 0, len(workloads))
	for _, w := range workloads {
		out = append(out, map[string]any{
			"name":                 w.Name,
			"runtime_type":         string(w.RuntimeType),
			"pid":                  w.Pid,
			"systemd_unit":         w.SystemdUnit,
			"container_id":         w.ContainerID,
			"image_name":           w.ImageName,
			"pod_name":             w.PodName,
			"listening_ports":      portEntriesToMaps(w.ListeningPorts),
			"outbound_connections": connEdgesToMaps(w.OutboundConns),
			"inbound_connections":  connEdgesToMaps(w.InboundConns),
			"ipc_sockets":          w.IPCSockets,
			"data_directories":     dirInfosToMaps(w.DataDirectories),
			"dependencies":         w.Dependencies,
		})
	}
	return out
}

// hybridEdgesToMaps converts []HybridEdge to []map[string]any.
func hybridEdgesToMaps(edges []HybridEdge) []map[string]any {
	out := make([]map[string]any, 0, len(edges))
	for _, e := range edges {
		out = append(out, map[string]any{
			"source_name":    e.SourceName,
			"source_runtime": e.SourceRuntime,
			"target_name":    e.TargetName,
			"target_runtime": e.TargetRuntime,
			"port":           e.Port,
			"protocol":       e.Protocol,
		})
	}
	return out
}

// portEntriesToMaps converts []PortEntry to []map[string]any.
func portEntriesToMaps(ports []PortEntry) []map[string]any {
	out := make([]map[string]any, 0, len(ports))
	for _, p := range ports {
		out = append(out, map[string]any{
			"port":     p.Port,
			"protocol": p.Protocol,
			"address":  p.Address,
		})
	}
	return out
}

// connEdgesToMaps converts []ConnEdge to []map[string]any.
func connEdgesToMaps(conns []ConnEdge) []map[string]any {
	out := make([]map[string]any, 0, len(conns))
	for _, c := range conns {
		out = append(out, map[string]any{
			"local_addr":  c.LocalAddr,
			"remote_addr": c.RemoteAddr,
			"remote_host": c.RemoteHost,
			"state":       c.State,
			"protocol":    c.Protocol,
		})
	}
	return out
}

// dirInfosToMaps converts []DirInfo to []map[string]any.
func dirInfosToMaps(dirs []DirInfo) []map[string]any {
	out := make([]map[string]any, 0, len(dirs))
	for _, d := range dirs {
		out = append(out, map[string]any{
			"path":       d.Path,
			"size_bytes": d.SizeBytes,
		})
	}
	return out
}
