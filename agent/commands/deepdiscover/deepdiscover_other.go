//go:build !linux && !darwin && !windows

package deepdiscover

func executeOS(_ map[string]any) (*DeepDiscoveryResult, error) {
	return &DeepDiscoveryResult{
		Workloads:   []DiscoveredWorkload{},
		HybridEdges: []HybridEdge{},
		CollectedAt: nowISO(),
		OS:          "unsupported",
	}, nil
}
