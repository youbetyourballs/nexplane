//go:build !linux && !darwin

package ebpf

import "fmt"

func EbpfNetworkSoakExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("ebpf_network_soak requires Linux")
}

func EbpfNetworkSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

func EbpfLsmSoakExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("ebpf_lsm_soak requires Linux")
}

func EbpfLsmSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

func parseSSLine(_ string) map[string]any       { return nil }
func splitAddrPort(s string) (string, int)       { return s, 0 }
func collectProcEvents(_ map[string]bool, _ *[]map[string]any) {}
func readComm(_ string) string                   { return "*" }
