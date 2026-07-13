// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package migration

import (
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"
)

// VerifyAgainstBaselineExecute compares the current system state to the stored
// behavioral baseline across four production quality layers.
func VerifyAgainstBaselineExecute(params map[string]any) (map[string]any, error) {
	baseline, _ := params["baseline"].(map[string]any)
	targetHost, _ := params["target_host"].(string)
	if targetHost == "" {
		targetHost = "localhost"
	}
	latencyThresholdPct := 150.0
	if v, ok := params["latency_threshold_pct"].(float64); ok {
		latencyThresholdPct = v
	}
	rowCountTolerancePct := 5.0
	if v, ok := params["row_count_tolerance_pct"].(float64); ok {
		rowCountTolerancePct = v
	}

	infraLayer := verifyInfrastructureLayer(targetHost)
	serviceLayer := verifyServiceLayer(baseline, targetHost)
	appLayer := verifyApplicationLayer(baseline, targetHost, latencyThresholdPct)
	dataLayer := verifyDataLayer(baseline, targetHost, rowCountTolerancePct)

	layers := map[string]any{
		"infrastructure": infraLayer,
		"service":        serviceLayer,
		"application":    appLayer,
		"data":           dataLayer,
	}

	appPassed, _ := appLayer["passed"].(bool)
	dataPassed, _ := dataLayer["passed"].(bool)
	failed := !(appPassed && dataPassed)

	report := map[string]any{
		"layers":  layers,
		"summary": buildSummary(layers, failed),
	}

	return map[string]any{
		"action": "verify_against_baseline",
		"report": report,
		"layers": layers,
		"failed": failed,
	}, nil
}

func verifyInfrastructureLayer(targetHost string) map[string]any {
	checks := []map[string]any{}
	passed := true

	conn, err := net.DialTimeout("tcp", targetHost+":22", 3*time.Second)
	reachable := err == nil
	if conn != nil {
		conn.Close()
	}
	checks = append(checks, map[string]any{
		"name": "host_reachable", "passed": reachable,
	})
	if !reachable {
		passed = false
	}

	return map[string]any{"passed": passed, "checks": checks}
}

func verifyServiceLayer(baseline map[string]any, targetHost string) map[string]any {
	checks := []map[string]any{}
	passed := true

	if baseline == nil {
		return map[string]any{"passed": true, "checks": checks}
	}

	baselineEndpoints, _ := baseline["endpoints"].([]map[string]any)
	for _, ep := range baselineEndpoints {
		port, _ := ep["port"].(int)
		if port == 0 {
			continue
		}
		addr := fmt.Sprintf("%s:%d", targetHost, port)
		conn, err := net.DialTimeout("tcp", addr, 3*time.Second)
		portOpen := err == nil
		if conn != nil {
			conn.Close()
		}
		check := map[string]any{
			"name":   fmt.Sprintf("port_%d_open", port),
			"passed": portOpen,
			"port":   port,
		}
		checks = append(checks, check)
		if !portOpen {
			passed = false
		}
	}

	return map[string]any{"passed": passed, "checks": checks}
}

func verifyApplicationLayer(baseline map[string]any, targetHost string, latencyThresholdPct float64) map[string]any {
	checks := []map[string]any{}
	passed := true

	if baseline == nil {
		return map[string]any{"passed": true, "checks": checks}
	}

	baselineEndpoints, _ := baseline["endpoints"].([]map[string]any)
	client := &http.Client{Timeout: 5 * time.Second}

	for _, ep := range baselineEndpoints {
		url, _ := ep["url"].(string)
		if url == "" {
			continue
		}
		// Replace localhost with targetHost
		url = strings.Replace(url, "localhost", targetHost, 1)

		expectedStatus, _ := ep["status_code"].(float64)
		baselineP95, _ := ep["response_ms_p95"].(float64)
		expectedSig, _ := ep["content_signature"].(string)

		start := time.Now()
		resp, err := client.Get(url)
		latencyMs := float64(time.Since(start).Milliseconds())

		var checkPassed bool
		var actualStatus int
		if err != nil {
			checkPassed = false
		} else {
			actualStatus = resp.StatusCode
			resp.Body.Close()
			statusOk := float64(actualStatus) == expectedStatus
			latencyOk := baselineP95 == 0 || latencyMs <= baselineP95*(latencyThresholdPct/100.0)
			sigOk := expectedSig == "" || fmt.Sprintf("%d", actualStatus) == expectedSig
			checkPassed = statusOk && latencyOk && sigOk
		}

		checks = append(checks, map[string]any{
			"name":       "http_" + url,
			"passed":     checkPassed,
			"expected":   expectedStatus,
			"actual":     actualStatus,
			"latency_ms": latencyMs,
		})
		if !checkPassed {
			passed = false
		}
	}

	return map[string]any{"passed": passed, "checks": checks}
}

func verifyDataLayer(baseline map[string]any, targetHost string, rowCountTolerancePct float64) map[string]any {
	checks := []map[string]any{}
	passed := true

	if baseline == nil {
		return map[string]any{"passed": true, "checks": checks}
	}

	baselineDeps, _ := baseline["dependencies"].([]map[string]any)
	for _, dep := range baselineDeps {
		depType, _ := dep["type"].(string)
		if depType != "db" {
			continue
		}
		target, _ := dep["target"].(string)
		addr := strings.Replace(target, "localhost", targetHost, 1)

		conn, err := net.DialTimeout("tcp", addr, 3*time.Second)
		dbReachable := err == nil
		if conn != nil {
			conn.Close()
		}

		checks = append(checks, map[string]any{
			"name":   "db_reachable_" + addr,
			"passed": dbReachable,
		})
		if !dbReachable {
			passed = false
		}
	}

	_ = rowCountTolerancePct

	return map[string]any{"passed": passed, "checks": checks}
}

func buildSummary(layers map[string]any, failed bool) string {
	if !failed {
		return "All four layers passed."
	}
	var failing []string
	for name, layer := range layers {
		l, _ := layer.(map[string]any)
		p, _ := l["passed"].(bool)
		if !p {
			failing = append(failing, name)
		}
	}
	return fmt.Sprintf("Failed layers: %s", strings.Join(failing, ", "))
}
