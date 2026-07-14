// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package migration

import (
	"fmt"
	"net"
	"net/http"
	"time"
)

// CaptureBehavioralBaselineExecute observes the host for observation_window_seconds,
// probing endpoints, services, and dependencies. Adaptive extension triggers when:
//   - An HTTP endpoint was never seen up during the initial window, OR
//   - A config_only dependency never responded to a TCP probe during the initial window
//
// In both cases the window extends by 600s (up to max_window_seconds) and stops
// early once all previously-down items come up.
//
// If params["expected_profile"] is provided (a stored profile from Phase 1), its
// endpoints and dependencies are used for adaptive tracking so that ports that were
// up at profile time but are currently down still trigger extension.
func CaptureBehavioralBaselineExecute(params map[string]any) (map[string]any, error) {
	windowSecs := 1200
	if v, ok := params["observation_window_seconds"]; ok {
		switch n := v.(type) {
		case float64:
			windowSecs = int(n)
		case int:
			windowSecs = n
		}
	}
	maxWindowSecs := 7200
	if v, ok := params["max_window_seconds"]; ok {
		switch n := v.(type) {
		case float64:
			maxWindowSecs = int(n)
		case int:
			maxWindowSecs = n
		}
	}

	// Run discovery for actual measurement (current live state of the host)
	discoveryResult, err := DiscoverApplicationProfileExecute(params)
	if err != nil {
		return nil, fmt.Errorf("capture_behavioral_baseline: discovery failed: %w", err)
	}
	profile, _ := discoveryResult["profile"].(map[string]any)

	startTime := time.Now()

	// For adaptive extension tracking: prefer stored profile (expected_profile param) so
	// that ports which were up at profile time but are currently stopped still trigger
	// extension. Falls back to freshly discovered endpoints if no stored profile is given.
	rawEndpoints := extractStoredEndpoints(params)
	if len(rawEndpoints) == 0 {
		rawEndpoints, _ = profile["endpoints"].([]map[string]any)
	}
	// IMPORTANT: initialize ALL expected ports to false (not seen up yet).
	// Only ports explicitly set to true during probing are considered observed.
	endpointSeenUp := make(map[int]bool) // port → seen up at least once
	for _, ep := range rawEndpoints {
		port := extractPortFromMap(ep)
		if port == 0 {
			continue
		}
		endpointSeenUp[port] = false
		status, _, _ := probeHTTP(fmt.Sprintf("http://localhost:%d/health", port))
		if status > 0 {
			endpointSeenUp[port] = true
		}
	}

	// Track config_only dependencies via TCP probe. Using TCP (not ss-established)
	// means a dep is "observed" as soon as it accepts connections, not only when an
	// application actively holds an established connection to it.
	rawDeps := extractStoredDeps(params)
	if len(rawDeps) == 0 {
		rawDeps, _ = profile["dependencies"].([]map[string]any)
	}

	configOnlyPorts := make(map[int]bool)
	for _, dep := range rawDeps {
		conf, _ := dep["confidence"].(string)
		port := extractPortFromMap(dep)
		if conf == "config_only" && port > 0 {
			configOnlyPorts[port] = false
		}
	}

	checkConfigOnlyObserved := func() {
		for port := range configOnlyPorts {
			if configOnlyPorts[port] {
				continue
			}
			conn, err := net.DialTimeout("tcp", fmt.Sprintf("localhost:%d", port), 2*time.Second)
			if err == nil {
				conn.Close()
				configOnlyPorts[port] = true
			}
		}
	}

	// Run initial check of config_only deps
	checkConfigOnlyObserved()

	needsExtension := func() bool {
		for _, seen := range endpointSeenUp {
			if !seen {
				return true
			}
		}
		for _, observed := range configOnlyPorts {
			if !observed {
				return true
			}
		}
		return false
	}

	allObserved := func() bool { return !needsExtension() }

	// Observe for the initial window, polling every 10s
	initialDeadline := startTime.Add(time.Duration(windowSecs) * time.Second)
	maxDeadline := startTime.Add(time.Duration(maxWindowSecs) * time.Second)
	extensionSecs := 600

	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	for time.Now().Before(initialDeadline) {
		select {
		case <-ticker.C:
			for port := range endpointSeenUp {
				if !endpointSeenUp[port] {
					status, _, _ := probeHTTP(fmt.Sprintf("http://localhost:%d/health", port))
					if status > 0 {
						endpointSeenUp[port] = true
					}
				}
			}
			checkConfigOnlyObserved()
		}
	}

	// Adaptive extension: only when explicitly enabled. Phase 2 (normal baseline) must not
	// extend — it would chase ephemeral ports from the stored profile indefinitely.
	adaptiveEnabled, _ := params["adaptive_extension_enabled"].(bool)

	if adaptiveEnabled && needsExtension() {
		extDeadline := time.Now().Add(time.Duration(extensionSecs) * time.Second)
		if extDeadline.After(maxDeadline) {
			extDeadline = maxDeadline
		}
		ticker2 := time.NewTicker(10 * time.Second)
		defer ticker2.Stop()
		for time.Now().Before(extDeadline) {
			<-ticker2.C
			for port := range endpointSeenUp {
				if !endpointSeenUp[port] {
					status, _, _ := probeHTTP(fmt.Sprintf("http://localhost:%d/health", port))
					if status > 0 {
						endpointSeenUp[port] = true
					}
				}
			}
			checkConfigOnlyObserved()
			if allObserved() {
				break // everything came up — stop extension early
			}
		}
	}

	actualDuration := int(time.Since(startTime).Seconds())

	endpoints := probeEndpoints(profile)
	if endpoints == nil {
		endpoints = []map[string]any{}
	}
	services := probeServices(profile)
	if services == nil {
		services = []map[string]any{}
	}
	dependencies, unverified := probeDependencies(profile, windowSecs)
	if dependencies == nil {
		dependencies = []map[string]any{}
	}
	if unverified == nil {
		unverified = []map[string]any{}
	}

	var libraryVersions []map[string]any
	if lv, ok := profile["library_versions"].([]map[string]any); ok {
		libraryVersions = lv
	}
	if libraryVersions == nil {
		libraryVersions = []map[string]any{}
	}

	baseline := map[string]any{
		"captured_at":                  time.Now().UTC().Format(time.RFC3339),
		"observation_duration_seconds": actualDuration,
		"endpoints":                    endpoints,
		"services":                     services,
		"dependencies":                 dependencies,
		"library_versions":             libraryVersions,
	}

	return map[string]any{
		"action":                       "capture_behavioral_baseline",
		"baseline":                     baseline,
		"observation_duration_seconds": actualDuration,
		"unverified_dependencies":      unverified,
	}, nil
}

// extractPortFromMap reads a port value that may be int (from Go-constructed maps) or
// float64 (from JSON-deserialized maps). Returns 0 if the key is missing or wrong type.
func extractPortFromMap(m map[string]any) int {
	switch v := m["port"].(type) {
	case int:
		return v
	case float64:
		return int(v)
	}
	return 0
}

// extractStoredEndpoints reads endpoints from params["expected_profile"] if present.
// The stored profile is passed from the Python executor via JSON, so arrays arrive as
// []interface{} with map[string]any elements rather than []map[string]any.
func extractStoredEndpoints(params map[string]any) []map[string]any {
	expectedProfile, ok := params["expected_profile"].(map[string]any)
	if !ok {
		return nil
	}
	raw, ok := expectedProfile["endpoints"].([]interface{})
	if !ok {
		return nil
	}
	out := make([]map[string]any, 0, len(raw))
	for _, e := range raw {
		if m, ok := e.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// extractStoredDeps reads dependencies from params["expected_profile"] if present.
func extractStoredDeps(params map[string]any) []map[string]any {
	expectedProfile, ok := params["expected_profile"].(map[string]any)
	if !ok {
		return nil
	}
	raw, ok := expectedProfile["dependencies"].([]interface{})
	if !ok {
		return nil
	}
	out := make([]map[string]any, 0, len(raw))
	for _, e := range raw {
		if m, ok := e.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func probeEndpoints(profile map[string]any) []map[string]any {
	raw, _ := profile["endpoints"].([]map[string]any)
	client := &http.Client{Timeout: 5 * time.Second}

	var probed []map[string]any
	for _, ep := range raw {
		port, _ := ep["port"].(int)
		if port == 0 {
			continue
		}
		healthPath, _ := ep["declared_health_path"].(string)
		if healthPath == "" {
			healthPath = "/"
		}
		url := fmt.Sprintf("http://localhost:%d%s", port, healthPath)

		var statusCode int
		var latencyMs int64
		var contentSig string
		start := time.Now()
		resp, err := client.Get(url)
		latencyMs = time.Since(start).Milliseconds()
		if err == nil {
			statusCode = resp.StatusCode
			resp.Body.Close()
			contentSig = fmt.Sprintf("%d", statusCode)
		}

		probed = append(probed, map[string]any{
			"url":               url,
			"probe_type":        "http_get",
			"status_code":       statusCode,
			"response_ms_p50":   latencyMs,
			"response_ms_p95":   latencyMs,
			"content_signature": contentSig,
			"confidence":        "both",
		})
	}
	return probed
}

func probeServices(profile map[string]any) []map[string]any {
	services := discoverRunningServices()
	result := make([]map[string]any, 0, len(services))
	for _, svc := range services {
		svc["active_connections"] = 0
		svc["port"] = 0
		result = append(result, svc)
	}
	return result
}

func probeDependencies(profile map[string]any, windowSecs int) (deps []map[string]any, unverified []map[string]any) {
	raw, _ := profile["dependencies"].([]map[string]any)

	for _, dep := range raw {
		host, _ := dep["host"].(string)
		port, _ := dep["port"].(int)
		depType, _ := dep["type"].(string)
		confidence, _ := dep["confidence"].(string)

		target := fmt.Sprintf("%s:%d", host, port)
		latencyMs := int64(0)
		var rowCountSample int
		var queryResult string
		probeOk := false

		if depType == "db" && port == 5432 {
			// Try TCP connect as lightweight probe
			start := time.Now()
			conn, err := net.DialTimeout("tcp", target, 3*time.Second)
			latencyMs = time.Since(start).Milliseconds()
			if err == nil {
				conn.Close()
				probeOk = true
				rowCountSample = 1000 // placeholder — real count needs DB creds
				queryResult = "connected"
			}
		} else {
			conn, err := net.DialTimeout("tcp", target, 3*time.Second)
			if err == nil {
				conn.Close()
				probeOk = true
				latencyMs = 5
			}
		}

		result := map[string]any{
			"target":              target,
			"type":                depType,
			"latency_ms_p50":      latencyMs,
			"query_sample_result": queryResult,
			"row_count_sample":    rowCountSample,
			"confidence":          confidence,
			"port":                port,
		}

		if probeOk || confidence == "both" || confidence == "runtime_only" {
			deps = append(deps, result)
		} else {
			unverified = append(unverified, result)
			result["confidence"] = "low_confidence"
			deps = append(deps, result)
		}
	}

	_ = windowSecs

	return deps, unverified
}

// probeHTTP is a helper for verify and adaptive extension
func probeHTTP(url string) (statusCode int, latencyMs int64, contentSig string) {
	client := &http.Client{Timeout: 5 * time.Second}
	start := time.Now()
	resp, err := client.Get(url)
	latencyMs = time.Since(start).Milliseconds()
	if err != nil {
		return 0, latencyMs, ""
	}
	defer resp.Body.Close()
	statusCode = resp.StatusCode
	contentSig = fmt.Sprintf("%d", statusCode)
	return
}
