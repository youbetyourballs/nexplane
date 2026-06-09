package isolation

import (
	"context"
	"fmt"
)

// IsolationConfig is sent from the control plane as the step payload.
type IsolationConfig struct {
	ManagementCIDR  string `json:"management_cidr"`
	ControlPlaneURL string `json:"control_plane_url"`
}

// PreIsolationState is captured before isolation and stored for rollback.
type PreIsolationState struct {
	OS            string `json:"os"`
	IPTablesRules string `json:"iptables_rules,omitempty"`
	NFTablesRules string `json:"nftables_rules,omitempty"`
	WFWRules      string `json:"wfw_rules,omitempty"`
	PFRules       string `json:"pf_rules,omitempty"`
}

// validate checks that required fields are present.
func (c IsolationConfig) validate() error {
	if c.ManagementCIDR == "" {
		return fmt.Errorf("management_cidr is required")
	}
	if c.ControlPlaneURL == "" {
		return fmt.Errorf("control_plane_url is required")
	}
	return nil
}

// Isolate captures pre-state, applies isolation rules, and returns the state
// needed for rollback. Implementation is OS-specific (linux.go / windows.go).
func Isolate(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	return isolateOS(ctx, cfg)
}

// Restore applies the pre-isolation state captured by Isolate.
func Restore(ctx context.Context, state *PreIsolationState) error {
	if state == nil {
		return fmt.Errorf("state is required for restore")
	}
	if state.OS == "" {
		return fmt.Errorf("state.os is required for restore")
	}
	return restoreOS(ctx, state)
}

// Execute is the CommandFunc adapter for the executor — forwards to Isolate.
func Execute(params map[string]any) (map[string]any, error) {
	cfg := IsolationConfig{}
	cfg.ManagementCIDR, _ = params["management_cidr"].(string)
	cfg.ControlPlaneURL, _ = params["control_plane_url"].(string)
	state, err := Isolate(context.Background(), cfg)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"os":             state.OS,
		"iptables_rules": state.IPTablesRules,
		"nftables_rules": state.NFTablesRules,
		"wfw_rules":      state.WFWRules,
		"pf_rules":       state.PFRules,
	}, nil
}

// Rollback is the CommandFunc adapter for the executor — forwards to Restore.
func Rollback(params map[string]any) (map[string]any, error) {
	state := &PreIsolationState{
		OS:            getStr(params, "os"),
		IPTablesRules: getStr(params, "iptables_rules"),
		NFTablesRules: getStr(params, "nftables_rules"),
		WFWRules:      getStr(params, "wfw_rules"),
		PFRules:       getStr(params, "pf_rules"),
	}
	if err := Restore(context.Background(), state); err != nil {
		return nil, err
	}
	return map[string]any{"restored": true}, nil
}

func getStr(m map[string]any, key string) string {
	v, _ := m[key].(string)
	return v
}
