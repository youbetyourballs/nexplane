//go:build darwin

package ossecurity

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

var execCommandAppArmor = exec.Command

type santaRule struct {
	SHA256  string `json:"sha256,omitempty"`
	TeamID  string `json:"team_id,omitempty"`
	Comment string `json:"comment,omitempty"`
}

func apparmorExecuteOS(params map[string]any) (map[string]any, error) {
	profileName, _ := params["profile_name"].(string)
	profileContent, _ := params["profile_content"].(string)
	mode, _ := params["mode"].(string)

	snapOut, _ := execCommandAppArmor("santactl", "rule", "list").Output()
	snapshot := map[string]any{
		"aa_status": strings.TrimSpace(string(snapOut)),
		"profile":   profileName,
	}

	var rulesAdded []string
	var teamIDsAdded []string

	if profileContent != "" {
		var rules []santaRule
		if err := json.Unmarshal([]byte(profileContent), &rules); err != nil {
			var rule santaRule
			if err2 := json.Unmarshal([]byte(profileContent), &rule); err2 != nil {
				return nil, fmt.Errorf("profile_content must be JSON array or object of santa rules: %w", err)
			}
			rules = []santaRule{rule}
		}

		for _, r := range rules {
			if r.SHA256 != "" {
				args := []string{"rule", "--allow", "--sha256", r.SHA256}
				if r.Comment != "" {
					args = append(args, "--comment", r.Comment)
				}
				if out, err := execCommandAppArmor("santactl", args...).CombinedOutput(); err != nil {
					return nil, fmt.Errorf("santactl rule --allow --sha256: %s: %w", out, err)
				}
				rulesAdded = append(rulesAdded, r.SHA256)
			} else if r.TeamID != "" {
				args := []string{"rule", "--allow", "--teamid", r.TeamID}
				if r.Comment != "" {
					args = append(args, "--comment", r.Comment)
				}
				if out, err := execCommandAppArmor("santactl", args...).CombinedOutput(); err != nil {
					return nil, fmt.Errorf("santactl rule --allow --teamid: %s: %w", out, err)
				}
				teamIDsAdded = append(teamIDsAdded, r.TeamID)
			}
		}
	}

	appliedMode := ""
	if mode == "complain" || mode == "disable" {
		appliedMode = "monitor"
	} else if mode == "enforce" {
		appliedMode = "enforce"
	}

	snapshot["rules_added"] = rulesAdded
	snapshot["team_ids_added"] = teamIDsAdded

	return map[string]any{
		"profile_name": profileName,
		"mode_applied": appliedMode,
		"snapshot":     snapshot,
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func apparmorRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if rulesRaw, _ := snapshot["rules_added"].([]any); len(rulesRaw) > 0 {
		for _, r := range rulesRaw {
			if sha, ok := r.(string); ok && sha != "" {
				execCommandAppArmor("santactl", "rule", "--remove", "--sha256", sha).Run()
			}
		}
	}
	if teamRaw, _ := snapshot["team_ids_added"].([]any); len(teamRaw) > 0 {
		for _, r := range teamRaw {
			if tid, ok := r.(string); ok && tid != "" {
				execCommandAppArmor("santactl", "rule", "--remove", "--teamid", tid).Run()
			}
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
