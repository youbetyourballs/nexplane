// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package winmigrate

import (
	"runtime"
	"strings"
	"testing"
)

func TestWinMigrateCommandsReturnErrorOnNonWindows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("stub test only runs on non-Windows")
	}
	tests := []struct {
		name string
		fn   func(map[string]any) (map[string]any, error)
	}{
		{"WinventoryExecute", WinventoryExecute},
		{"RobocopyPushExecute", RobocopyPushExecute},
		{"ApplyReplacementsExecute", ApplyReplacementsExecute},
		{name: "win_run_ps", fn: WinRunPsExecute},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, err := tc.fn(map[string]any{})
			if err == nil {
				t.Fatalf("%s: expected error on non-Windows, got nil", tc.name)
			}
			if !strings.Contains(err.Error(), "only supported on Windows") {
				t.Errorf("%s: expected 'only supported on Windows' in error, got: %v", tc.name, err)
			}
		})
	}
}

func TestCommandNameConstants(t *testing.T) {
	if CmdWinventory != "win_inventory" {
		t.Errorf("CmdWinventory = %q, want %q", CmdWinventory, "win_inventory")
	}
	if CmdRobocopyPush != "win_robocopy_push" {
		t.Errorf("CmdRobocopyPush = %q, want %q", CmdRobocopyPush, "win_robocopy_push")
	}
	if CmdApplyReplacements != "win_apply_replacements" {
		t.Errorf("CmdApplyReplacements = %q, want %q", CmdApplyReplacements, "win_apply_replacements")
	}
}
