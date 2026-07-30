// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// Package winmigrate implements Windows parallel migration agent commands.
// On non-Windows platforms only the stubs in winmigrate_other.go are compiled.
package winmigrate

// Command names registered in agent/executor/executor.go.
const (
	CmdWinventory        = "win_inventory"
	CmdRobocopyPush      = "win_robocopy_push"
	CmdApplyReplacements = "win_apply_replacements"
)
