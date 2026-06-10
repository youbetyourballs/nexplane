package compliance

import (
	"fmt"
	"time"
)

// ControlResult holds the pass/fail outcome for a single CIS control.
type ControlResult struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Section  string `json:"section"`
	Status   string `json:"status"`   // "pass" | "fail" | "skip"
	Expected string `json:"expected"`
	Actual   string `json:"actual"`
}

// AuditCISComplianceExecute is the command entrypoint registered in executor.go.
// params: {"level": 1|2, "os_family": "rhel"|"debian"|"ubuntu"|"darwin"}
func AuditCISComplianceExecute(params map[string]any) (map[string]any, error) {
	levelF, ok := params["level"].(float64)
	if !ok {
		// default to level 1 when not provided
		levelF = 1
	}
	level := int(levelF)
	if level != 1 && level != 2 {
		return nil, fmt.Errorf("level must be 1 or 2, got %d", level)
	}
	osFamily, _ := params["os_family"].(string)
	switch osFamily {
	case "rhel", "debian", "ubuntu", "darwin":
	default:
		return nil, fmt.Errorf("unsupported os_family %q: must be rhel, debian, ubuntu, or darwin", osFamily)
	}
	return auditCISComplianceOS(level, osFamily)
}

// CollectEvidenceExecute is the command entrypoint for evidence collection.
// params: {"framework": "soc2"|"pci"|"iso27001", "control_id": "CC6.1", "evidence_types": [...]}
func CollectEvidenceExecute(params map[string]any) (map[string]any, error) {
	framework, _ := params["framework"].(string)
	switch framework {
	case "soc2", "pci", "iso27001":
	default:
		return nil, fmt.Errorf("unsupported framework %q: must be soc2, pci, or iso27001", framework)
	}
	controlID, _ := params["control_id"].(string)
	if controlID == "" {
		return nil, fmt.Errorf("control_id is required")
	}
	return collectEvidenceOS(params)
}

// CalculateScore returns passing controls / non-skip controls.
// Returns 1.0 if all controls are skipped.
// Exported for testing.
func CalculateScore(controls []ControlResult) float64 {
	total := 0
	pass := 0
	for _, c := range controls {
		if c.Status == "skip" {
			continue
		}
		total++
		if c.Status == "pass" {
			pass++
		}
	}
	if total == 0 {
		return 1.0
	}
	return float64(pass) / float64(total)
}

// collectedNow returns current UTC time formatted as RFC3339.
func collectedNow() string {
	return time.Now().UTC().Format(time.RFC3339)
}
