//go:build windows

package winharden

import (
	"fmt"
	"os"
	"strings"
	"time"
)

const wdacPolicyPath = `C:\Windows\System32\CodeIntegrity\SIPolicy.p7b`
const wdacXMLPath = `C:\Windows\System32\CodeIntegrity\nexplane-SIPolicy.xml`
const wdacEventLog = `Microsoft-Windows-CodeIntegrity/Operational`

const wdacAuditModeXML = `<?xml version="1.0" encoding="utf-8"?>
<SiPolicy xmlns="urn:schemas-microsoft-com:sipolicy">
  <VersionEx>10.0.0.0</VersionEx>
  <PolicyTypeID>{A244370E-44C9-4C06-B551-F6016E563076}</PolicyTypeID>
  <PlatformID>{2E07F7E4-194C-4D20-B96C-134C44A9F1A5}</PlatformID>
  <Rules>
    <Rule><Option>Enabled:Audit Mode</Option></Rule>
    <Rule><Option>Enabled:Advanced Boot Options Menu</Option></Rule>
  </Rules>
  <EKUs/>
  <FileRules/>
  <Signers/>
  <SigningScenarios>
    <SigningScenario Value="131" ID="ID_SIGNINGSCENARIO_DRIVERS" FriendlyName="Drivers">
      <ProductSigners/>
    </SigningScenario>
    <SigningScenario Value="12" ID="ID_SIGNINGSCENARIO_WINDOWS" FriendlyName="User Mode">
      <ProductSigners/>
    </SigningScenario>
  </SigningScenarios>
  <UpdatePolicySigners/>
  <CiSigners/>
  <HvciOptions>0</HvciOptions>
</SiPolicy>`

func wdacAuditExecuteOS(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	if err := os.WriteFile(wdacXMLPath, []byte(wdacAuditModeXML), 0644); err != nil {
		return nil, fmt.Errorf("write WDAC XML: %w", err)
	}
	if out, err := runPS(fmt.Sprintf(
		`ConvertFrom-CIPolicy -XmlFilePath "%s" -BinaryFilePath "%s"`, wdacXMLPath, wdacPolicyPath,
	)); err != nil {
		return nil, fmt.Errorf("ConvertFrom-CIPolicy: %s: %w", out, err)
	}
	runPS(`Invoke-CimMethod -Namespace root/Microsoft/Windows/CI -ClassName PS_UpdateAndCompareCIPolicy -MethodName Update -Arguments @{FilePath="` + wdacPolicyPath + `"}`)
	time.Sleep(time.Duration(duration) * time.Second)
	out, _ := runPS(fmt.Sprintf(
		`Get-WinEvent -LogName "%s" -FilterXPath "*[System[EventID=3076]]" -MaxEvents 100 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Message`,
		wdacEventLog,
	))
	events := parseWDACLines(string(out))
	return map[string]any{
		"action":           "wdac_audit",
		"duration_seconds": int(duration),
		"audit_events":     events,
		"event_count":      len(events),
		"policy_path":      wdacPolicyPath,
	}, nil
}

func wdacEnforceExecuteOS(params map[string]any) (map[string]any, error) {
	policyContent, _ := params["policy_xml"].(string)
	if policyContent == "" {
		return nil, fmt.Errorf("policy_xml is required for wdac_enforce")
	}
	if err := os.WriteFile(wdacXMLPath, []byte(policyContent), 0644); err != nil {
		return nil, fmt.Errorf("write WDAC XML: %w", err)
	}
	if out, err := runPS(fmt.Sprintf(
		`ConvertFrom-CIPolicy -XmlFilePath "%s" -BinaryFilePath "%s"`, wdacXMLPath, wdacPolicyPath,
	)); err != nil {
		return nil, fmt.Errorf("ConvertFrom-CIPolicy: %s: %w", out, err)
	}
	out, _ := runPS(`Invoke-CimMethod -Namespace root/Microsoft/Windows/CI -ClassName PS_UpdateAndCompareCIPolicy -MethodName Update -Arguments @{FilePath="` + wdacPolicyPath + `"}`)
	return map[string]any{
		"action":      "wdac_enforce",
		"policy_path": wdacPolicyPath,
		"output":      strings.TrimSpace(string(out)),
	}, nil
}

func wdacRollbackOS(_ map[string]any) (map[string]any, error) {
	_ = os.Remove(wdacPolicyPath)
	_ = os.Remove(wdacXMLPath)
	return map[string]any{"action": "wdac_rollback", "status": "policy_removed_reboot_required"}, nil
}

func parseWDACLines(s string) []string {
	var lines []string
	for _, l := range strings.Split(s, "\n") {
		l = strings.TrimSpace(l)
		if l != "" {
			lines = append(lines, l)
		}
	}
	return lines
}
