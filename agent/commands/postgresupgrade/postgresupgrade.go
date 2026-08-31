// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package postgresupgrade

func PreflightExecute(params map[string]any) (map[string]any, error) {
	return preflightPG(params)
}

func ExecuteExecute(params map[string]any) (map[string]any, error) {
	return executePG(params)
}

func VerifyExecute(params map[string]any) (map[string]any, error) {
	return verifyPG(params)
}

func RollbackExecute(params map[string]any) (map[string]any, error) {
	return rollbackPG(params)
}
