// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package kernelupgrade

func PreflightExecute(params map[string]any) (map[string]any, error) {
	return preflightOS(params)
}

func ExecuteExecute(params map[string]any) (map[string]any, error) {
	return executeOS(params)
}

func VerifyExecute(params map[string]any) (map[string]any, error) {
	return verifyOS(params)
}

func VerifyServicesExecute(params map[string]any) (map[string]any, error) {
	return verifyServicesOS(params)
}

func RollbackExecute(params map[string]any) (map[string]any, error) {
	return rollbackOS(params)
}
