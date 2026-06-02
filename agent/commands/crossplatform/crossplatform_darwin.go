//go:build darwin

package crossplatform

import "fmt"

func tlsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("tls_config not supported on macOS")
}

func tlsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("tls_config rollback not supported on macOS")
}

func dnsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("dns_config not supported on macOS")
}

func dnsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("dns_config rollback not supported on macOS")
}

func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("inventory not supported on macOS")
}
