//go:build !linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package drift

import "fmt"

func restoreDriftState(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("restore_drift_state is only supported on Linux")
}
