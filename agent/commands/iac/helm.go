package iac

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
)

// HelmParams matches the helm_upgrade change type parameters.
type HelmParams struct {
	ReleaseName  string `json:"release_name"`
	Chart        string `json:"chart"`
	ChartVersion string `json:"chart_version"`
	Namespace    string `json:"namespace"`
	Values       string `json:"values"`
	Atomic       bool   `json:"atomic"`
	Timeout      string `json:"timeout"`
	DryRun       bool   `json:"dry_run"`
	ChangeID     string `json:"change_id"`
}

// HelmDiff runs helm diff upgrade, returns output for blast radius.
// Requires the helm-diff plugin to be installed on the host.
func HelmDiff(ctx context.Context, p HelmParams) (string, error) {
	args, cleanup, err := baseHelmArgs("diff", "upgrade", p)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	return captureCmd(ctx, ".", "helm", args...)
}

// HelmUpgrade runs helm upgrade --install with optional --atomic.
func HelmUpgrade(ctx context.Context, p HelmParams) (string, error) {
	args, cleanup, err := baseHelmArgs("upgrade", "--install", p)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	if p.Atomic {
		args = append(args, "--atomic")
	}
	timeout := p.Timeout
	if timeout == "" {
		timeout = "5m"
	}
	args = append(args, "--timeout", timeout)
	return captureCmd(ctx, ".", "helm", args...)
}

// HelmRollback rolls back to the given release revision.
func HelmRollback(ctx context.Context, p HelmParams, previousRevision int) (string, error) {
	args := []string{"rollback", p.ReleaseName, fmt.Sprintf("%d", previousRevision),
		"-n", helmNamespace(p)}
	return captureCmd(ctx, ".", "helm", args...)
}

func baseHelmArgs(subcmd1, subcmd2 string, p HelmParams) ([]string, func(), error) {
	args := []string{subcmd1, subcmd2, p.ReleaseName, p.Chart,
		"-n", helmNamespace(p)}
	if p.ChartVersion != "" {
		args = append(args, "--version", p.ChartVersion)
	}
	var cleanup func()
	if p.Values != "" {
		f, err := os.CreateTemp("", "nexplane-helm-values-"+p.ChangeID+"-*.yaml")
		if err != nil {
			return nil, nil, fmt.Errorf("writing values: %w", err)
		}
		if _, err := f.WriteString(p.Values); err != nil {
			return nil, nil, err
		}
		f.Close()
		vpath, _ := filepath.Abs(f.Name())
		cleanup = func() { os.Remove(vpath) }
		args = append(args, "-f", vpath)
	}
	return args, cleanup, nil
}

func helmNamespace(p HelmParams) string {
	if p.Namespace == "" {
		return "default"
	}
	return p.Namespace
}
