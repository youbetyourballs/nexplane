// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package drift

// CaptureDriftStateExecute reads a host surface and returns its normalized state.
// params: {"surface_type": string}
// returns: {"status": "success"|"failed", "state": {...}} or error
func CaptureDriftStateExecute(params map[string]any) (map[string]any, error) {
	return captureDriftState(params)
}

// RestoreDriftStateExecute restores a host surface to a target state.
// params: {"surface_type": string, "target_state": {...}}
func RestoreDriftStateExecute(params map[string]any) (map[string]any, error) {
	return restoreDriftState(params)
}

// WriteFileExecute writes content to a file on the host.
// Intended for smoke-test use (simulating out-of-band mutations).
// params: {"path": string, "content": string, "mode": optional float64}
func WriteFileExecute(params map[string]any) (map[string]any, error) {
	return writeFile(params)
}
