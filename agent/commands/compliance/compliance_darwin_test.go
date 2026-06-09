//go:build darwin

package compliance

import (
	"os/exec"
	"testing"
)

func TestAuditCISComplianceOS_Darwin(t *testing.T) {
	execCommandCompliance = func(name string, args ...string) *exec.Cmd {
		switch name {
		case "csrutil":
			return exec.Command("echo", "System Integrity Protection status: enabled.")
		case "spctl":
			return exec.Command("echo", "assessments enabled")
		case "/usr/libexec/ApplicationFirewall/socketfilterfw":
			return exec.Command("echo", "Firewall is enabled. (State = 1)")
		case "sw_vers":
			return exec.Command("echo", "14.0")
		case "systemsetup":
			return exec.Command("echo", "Network Time: On")
		default:
			return exec.Command("echo", "enabled")
		}
	}
	t.Cleanup(func() { execCommandCompliance = exec.Command })

	result, err := auditCISComplianceOS(1, "darwin")
	if err != nil {
		t.Fatalf("auditCISComplianceOS: %v", err)
	}
	if result["level"] != 1 {
		t.Fatalf("expected level=1; got %v", result["level"])
	}
	controls, _ := result["controls"].([]ControlResult)
	if len(controls) == 0 {
		t.Fatal("expected controls list")
	}
}
