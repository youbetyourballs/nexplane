package linuxupgrade_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/linuxupgrade"
)

func TestUpgradeRequiresPath(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "path must be") {
		t.Errorf("expected path error, got: %v", err)
	}
}

func TestUpgradeInvalidPath(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{"path": "side_by_side"})
	if err == nil || !strings.Contains(err.Error(), "path must be") {
		t.Errorf("expected path error, got: %v", err)
	}
}

func TestUpgradeInvalidUpgradeType(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{"path": "inplace", "upgrade_type": "everything"})
	if err == nil || !strings.Contains(err.Error(), "upgrade_type must be") {
		t.Errorf("expected upgrade_type error, got: %v", err)
	}
}

func TestUpgradeDDRequiresTargetPath(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{"path": "inplace", "snapshot_method": "dd"})
	if err == nil || !strings.Contains(err.Error(), "snapshot_target_path") {
		t.Errorf("expected snapshot_target_path error, got: %v", err)
	}
}

func TestUpgradeContainerizeRequiresRegistry(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{"path": "containerize", "target_os": "ubuntu-24.04"})
	if err == nil || !strings.Contains(err.Error(), "container_registry") {
		t.Errorf("expected container_registry error, got: %v", err)
	}
}

func TestUpgradeContainerizeRequiresTargetOS(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{"path": "containerize", "container_registry": "registry.example.com"})
	if err == nil || !strings.Contains(err.Error(), "target_os") {
		t.Errorf("expected target_os error, got: %v", err)
	}
}
