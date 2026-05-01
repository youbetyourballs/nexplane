package fingerprint_test

import (
	"strings"
	"testing"

	"nexplane-agent/fingerprint"
)

func TestGetMachineIDIsNonEmpty(t *testing.T) {
	id, err := fingerprint.GetMachineID()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if id == "" {
		t.Error("machine ID must not be empty")
	}
}

func TestGetMachineIDIsStable(t *testing.T) {
	id1, _ := fingerprint.GetMachineID()
	id2, _ := fingerprint.GetMachineID()
	if id1 != id2 {
		t.Errorf("machine ID not stable: %q != %q", id1, id2)
	}
}

func TestGetMachineIDNoSpaces(t *testing.T) {
	id, _ := fingerprint.GetMachineID()
	if strings.ContainsAny(id, " \t\n\r") {
		t.Errorf("machine ID should not contain whitespace: %q", id)
	}
}
