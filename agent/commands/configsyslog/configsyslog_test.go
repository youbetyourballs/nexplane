//go:build windows

package configsyslog_test

import (
	"testing"

	"nexplane-agent/commands/configsyslog"
)

func TestExecuteRequiresDestinationHost(t *testing.T) {
	_, err := configsyslog.Execute(map[string]any{
		"destination_port": 514,
		"protocol":         "udp",
	})
	if err == nil {
		t.Error("expected error for missing destination_host")
	}
}

func TestExecuteRequiresPort(t *testing.T) {
	_, err := configsyslog.Execute(map[string]any{
		"destination_host": "logs.example.com",
		"protocol":         "udp",
	})
	if err == nil {
		t.Error("expected error for missing destination_port")
	}
}

func TestExecuteRequiresProtocol(t *testing.T) {
	_, err := configsyslog.Execute(map[string]any{
		"destination_host": "logs.example.com",
		"destination_port": 514,
	})
	if err == nil {
		t.Error("expected error for missing protocol")
	}
}
