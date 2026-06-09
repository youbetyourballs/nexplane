//go:build darwin

package credrotation

import (
	"context"
	"os/exec"
	"strings"
	"testing"
)

func TestUpdateDBUserPassword_DarwinPostgres(t *testing.T) {
	var called []string
	execCommandDB = func(name string, args ...string) *exec.Cmd {
		called = append(called, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ALTER ROLE")
	}
	t.Cleanup(func() { execCommandDB = exec.Command })

	err := updateDBUserPassword(context.Background(), DBRotateParams{
		DBEngine:    "postgres",
		DBHost:      "localhost",
		DBPort:      5432,
		DBUsername:  "appuser",
		NewPassword: "newpass",
	})
	if err != nil {
		t.Fatalf("updateDBUserPassword: %v", err)
	}
	found := false
	for _, c := range called {
		if strings.Contains(c, "psql") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected psql; got: %v", called)
	}
}

func TestRestartService_Darwin(t *testing.T) {
	var called []string
	execCommandDB = func(name string, args ...string) *exec.Cmd {
		called = append(called, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandDB = exec.Command })

	err := restartService(context.Background(), "postgresql")
	if err != nil {
		t.Fatalf("restartService: %v", err)
	}
	if len(called) == 0 {
		t.Fatal("expected at least one command")
	}
}
