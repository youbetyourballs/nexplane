//go:build linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package kernelupgrade

import (
	"strings"
	"testing"
)

func TestPreflightReturnsCurrentKernel(t *testing.T) {
	result, err := PreflightExecute(map[string]any{
		"target_kernel": "6.1.0-999-generic", // non-existent, that's fine
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["status"] == nil {
		t.Fatal("expected status in result")
	}
	if result["current_kernel"] == nil {
		t.Fatal("expected current_kernel in result")
	}
	kernel, _ := result["current_kernel"].(string)
	if !strings.Contains(kernel, ".") {
		t.Fatalf("current_kernel looks wrong: %q", kernel)
	}
}

func TestPreflightBlocksOnInsufficientDisk(t *testing.T) {
	result, err := PreflightExecute(map[string]any{
		"target_kernel":   "6.1.0-999-generic",
		"_test_disk_free": float64(0.5), // inject fake disk free below threshold
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["status"] != "blocked" {
		t.Fatalf("expected status=blocked when disk is low, got %v", result["status"])
	}
}

func TestVerifyReturnsRunningKernel(t *testing.T) {
	result, err := VerifyExecute(map[string]any{
		"target_kernel": "6.1.0-999-generic", // won't match — verified should be false
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["running_kernel"] == nil {
		t.Fatal("expected running_kernel in result")
	}
	verified, _ := result["verified"].(bool)
	if verified {
		t.Fatal("should not be verified when target doesn't match running kernel")
	}
}

func TestRollbackWithoutPreviousKernelReturnsError(t *testing.T) {
	_, err := RollbackExecute(map[string]any{
		// no previous_kernel param
	})
	if err == nil {
		t.Fatal("expected error when previous_kernel is empty")
	}
}
