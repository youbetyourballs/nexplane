//go:build linux

package linuxharden

import (
	"bufio"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func lynisAuditExecute(params map[string]any) (map[string]any, error) {
	// Install Lynis if not present
	if _, err := exec.LookPath("lynis"); err != nil {
		exec.Command("sh", "-c",
			"yum install -y lynis 2>/dev/null || apt-get install -y lynis 2>/dev/null || true").Run()
	}

	out, err := exec.Command("lynis", "audit", "system", "--quick", "--quiet",
		"--no-colors", "--log-file", "/tmp/lynis.log").CombinedOutput()
	_ = err // lynis exits non-zero even when complete

	var warnings, suggestions []string
	var score int
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "  !") {
			warnings = append(warnings, strings.TrimSpace(strings.TrimPrefix(line, "  !")))
		} else if strings.HasPrefix(line, "  *") {
			suggestions = append(suggestions, strings.TrimSpace(strings.TrimPrefix(line, "  *")))
		} else if strings.Contains(line, "Hardening index") {
			fmt.Sscanf(line, "%*s %*s %*s %*s %d", &score)
		}
	}

	warnLimit := len(warnings)
	if warnLimit > 20 {
		warnLimit = 20
	}
	suggLimit := len(suggestions)
	if suggLimit > 20 {
		suggLimit = 20
	}

	return map[string]any{
		"action":           "lynis_audit",
		"hardening_score":  score,
		"warning_count":    len(warnings),
		"suggestion_count": len(suggestions),
		"warnings":         warnings[:warnLimit],
		"suggestions":      suggestions[:suggLimit],
		"audited_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func lynisAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "lynis_audit_rollback", "status": "read_only"}, nil
}
