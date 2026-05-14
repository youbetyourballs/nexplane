//go:build windows

package winharden

import (
	"fmt"
	"time"
)

var asrRuleNames = map[string]string{
	"block-office-child-processes":      "D4F940AB-401B-4EFC-AADC-AD5F3C50688A",
	"block-credential-stealing":         "9E6C4E1F-7D60-472F-BA1A-A39EF669E4B2",
	"block-untrusted-executables-email": "BE9BA2D9-53EA-4CDC-84E5-9B1EEEE46550",
	"block-office-macro-win32-api":      "92E97FA1-2EDF-4476-BDD6-9DD0B4DDDC7B",
	"block-script-obfuscated-js-vbs":    "5BEB7EFE-FD9A-4556-801D-275E5FFC04CC",
	"block-js-vbs-launching-executable": "D3E037E1-3EB8-44C8-A917-57927947596D",
	"block-process-creation-psexec-wmi": "D1E49AAC-8F56-4280-B9BA-993A6D77406C",
	"block-untrusted-usb-processes":     "B2B3F03D-6A65-4F7B-A9C7-1C7EF74A9BA4",
}

func asrAuditExecuteOS(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	rules := asrRuleIDList(params)
	for _, ruleID := range rules {
		out, err := runPS(fmt.Sprintf(
			`Add-MpPreference -AttackSurfaceReductionRules_Ids %s -AttackSurfaceReductionRules_Actions AuditMode`,
			ruleID,
		))
		if err != nil {
			return nil, fmt.Errorf("ASR audit mode for %s: %s: %w", ruleID, out, err)
		}
	}
	time.Sleep(time.Duration(duration) * time.Second)
	out, _ := runPS(
		`Get-WinEvent -LogName "Microsoft-Windows-Windows Defender/Operational" ` +
			`-FilterXPath "*[System[EventID=1122]]" -MaxEvents 200 -ErrorAction SilentlyContinue ` +
			`| Select-Object -ExpandProperty Message`,
	)
	events := parseWDACLines(string(out))
	return map[string]any{
		"action":           "asr_audit",
		"duration_seconds": int(duration),
		"rules_audited":    rules,
		"audit_events":     events,
		"event_count":      len(events),
	}, nil
}

func asrEnforceExecuteOS(params map[string]any) (map[string]any, error) {
	rules := asrRuleIDList(params)
	for _, ruleID := range rules {
		out, err := runPS(fmt.Sprintf(
			`Add-MpPreference -AttackSurfaceReductionRules_Ids %s -AttackSurfaceReductionRules_Actions Enabled`,
			ruleID,
		))
		if err != nil {
			return nil, fmt.Errorf("ASR block mode for %s: %s: %w", ruleID, out, err)
		}
	}
	return map[string]any{"action": "asr_enforce", "rules_enforced": rules}, nil
}

func asrRollbackOS(params map[string]any) (map[string]any, error) {
	rules := asrRuleIDList(params)
	for _, ruleID := range rules {
		runPS(fmt.Sprintf(`Remove-MpPreference -AttackSurfaceReductionRules_Ids %s`, ruleID))
	}
	return map[string]any{"action": "asr_rollback", "rules_cleared": rules}, nil
}

func asrRuleIDList(params map[string]any) []string {
	var ids []string
	if raw, ok := params["rule_names"].([]any); ok {
		for _, r := range raw {
			if name, ok := r.(string); ok {
				if id, found := asrRuleNames[name]; found {
					ids = append(ids, id)
				}
			}
		}
	}
	if len(ids) == 0 {
		ids = []string{
			asrRuleNames["block-office-child-processes"],
			asrRuleNames["block-credential-stealing"],
			asrRuleNames["block-untrusted-executables-email"],
		}
	}
	return ids
}
