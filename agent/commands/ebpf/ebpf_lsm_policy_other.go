//go:build !linux

package ebpf

import "fmt"

func ConfigureEbpfLsmExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_lsm requires Linux")
}

func ConfigureEbpfLsmRollback(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_lsm requires Linux")
}
