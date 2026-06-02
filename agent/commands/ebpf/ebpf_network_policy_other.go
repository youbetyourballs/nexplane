//go:build !linux && !darwin

package ebpf

import "fmt"

func ConfigureEbpfNetworkExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_network requires Linux")
}

func ConfigureEbpfNetworkRollback(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_network requires Linux")
}
