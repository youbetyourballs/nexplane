// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

//go:build !windows

package winmigrate

import "fmt"

func WinventoryExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("win_inventory is only supported on Windows")
}

func RobocopyPushExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("win_robocopy_push is only supported on Windows")
}

func ApplyReplacementsExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("win_apply_replacements is only supported on Windows")
}
