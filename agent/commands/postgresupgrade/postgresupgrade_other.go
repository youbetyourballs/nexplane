//go:build !linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package postgresupgrade

import "fmt"

func preflightPG(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("postgres upgrade is only supported on Linux")
}

func executePG(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("postgres upgrade is only supported on Linux")
}

func verifyPG(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("postgres upgrade is only supported on Linux")
}

func rollbackPG(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("postgres upgrade is only supported on Linux")
}
