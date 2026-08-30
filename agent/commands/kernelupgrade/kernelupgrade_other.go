//go:build !linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package kernelupgrade

import "fmt"

func preflightOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("kernel upgrade is only supported on Linux")
}

func executeOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("kernel upgrade is only supported on Linux")
}

func verifyOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("kernel upgrade is only supported on Linux")
}

func verifyServicesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("kernel upgrade is only supported on Linux")
}

func rollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("kernel upgrade is only supported on Linux")
}
