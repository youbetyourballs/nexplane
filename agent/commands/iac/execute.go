package iac

import (
	"context"
	"encoding/json"
	"fmt"
)

func toJSON(v map[string]any) []byte {
	b, _ := json.Marshal(v)
	return b
}

func TerraformPlanExecute(params map[string]any) (map[string]any, error) {
	var p TerraformParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid terraform params: %w", err)
	}
	out, err := TerraformPlan(context.Background(), p)
	return map[string]any{"plan_output": out}, err
}

func TerraformApplyExecute(params map[string]any) (map[string]any, error) {
	var p TerraformParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid terraform params: %w", err)
	}
	out, err := TerraformApply(context.Background(), p)
	return map[string]any{"apply_output": out}, err
}

func TerraformRollbackExecute(params map[string]any) (map[string]any, error) {
	var p TerraformParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid terraform params: %w", err)
	}
	resources, _ := params["resources"].([]string)
	destroy, _ := params["destroy"].(bool)
	out, err := TerraformRollback(context.Background(), p, resources, destroy)
	return map[string]any{"rollback_output": out}, err
}

func AnsibleCheckExecute(params map[string]any) (map[string]any, error) {
	var p AnsibleParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid ansible params: %w", err)
	}
	out, err := AnsibleCheck(context.Background(), p)
	return map[string]any{"check_output": out}, err
}

func AnsibleRunExecute(params map[string]any) (map[string]any, error) {
	var p AnsibleParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid ansible params: %w", err)
	}
	out, err := AnsibleRun(context.Background(), p, false)
	return map[string]any{"run_output": out}, err
}

func AnsibleRollbackExecute(params map[string]any) (map[string]any, error) {
	var p AnsibleParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid ansible params: %w", err)
	}
	out, err := AnsibleRun(context.Background(), p, true)
	return map[string]any{"rollback_output": out}, err
}

func HelmDiffExecute(params map[string]any) (map[string]any, error) {
	var p HelmParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid helm params: %w", err)
	}
	out, err := HelmDiff(context.Background(), p)
	return map[string]any{"diff_output": out}, err
}

func HelmUpgradeExecute(params map[string]any) (map[string]any, error) {
	var p HelmParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid helm params: %w", err)
	}
	out, err := HelmUpgrade(context.Background(), p)
	return map[string]any{"upgrade_output": out}, err
}

func HelmRollbackExecute(params map[string]any) (map[string]any, error) {
	var p HelmParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid helm params: %w", err)
	}
	revision := 0
	if r, ok := params["previous_revision"].(float64); ok {
		revision = int(r)
	}
	out, err := HelmRollback(context.Background(), p, revision)
	return map[string]any{"rollback_output": out}, err
}
